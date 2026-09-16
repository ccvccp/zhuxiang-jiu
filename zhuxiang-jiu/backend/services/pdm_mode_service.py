"""38号·AI智能产品管理大模型——三态灰度与护栏服务

依据: 智图/信用/钱包/小竹模式层范式(全站大模型转段标准——
68/45/智运/62/65 同源; 62号同步桥接)。

三态灰度(PDM_MODE, 智能产品管理前缀):
    off(默认)    决策面关闭(409)——产品展示基础 7 端点零影响铁律
    shadow       观察学习期(决策放行+响应留痕标记 pdmMode)
    assist       辅助生产期(决策生效)

    读取链(优先级): 护栏暂停 > 运行时 override >
    环境变量 PDM_MODE > 默认 off。
    观测面永不关停(商品列表/详情/待审/版本/图片/上架建议/
    A-B 测试/看板报表——运营全链只读)。

护栏(智能产品管理语义三指标, 恶化>3% 自动暂停):
    终审驳回率 manualRejectRate
        = rejected / 终审已决(rejected+on_sale)
          (人工终审对 AI 辅助链路的质量恶化代理;
           流程态 draft/ai_reviewing/manual_reviewing
           不进分母——建议书制口径, 冷启动不误判)
    图片违规标记率 imageFlagRate
        = flagged / 总图片
          (审图恶化代理)
    AI 预检拦截率 aiRejectRate
        = aiReview.action==reject / 全部已预审
          (AI 预检恶化代理)
    (cur-base)/base > 3% → 自动暂停(等效 off, 留痕);
    恢复须人工 resume(决策留痕)。护栏暂停是保护机制
    (功能开关, 非处罚), 可自动。

红线(宪法域):
    - 产品展示基础 7 端点(/api/product/*)零改动:
      mode 只挡 PDM 决策面(/api/pdm/* 写端点)
    - 观测面永不关停: 运营查询/看板常开
    - 紧急下架安全阀(force-delist)永不关停:
      违规商品即刻下架的安全处置权不受 mode 影响
    - 人工终审语义保留(auditor 域): AI 只辅助
      (预检/建议/生图), 终审 approve 权在人工
    - SoD 职责分离(operator/auditor/manage)不受
      mode 影响
    - LLM 禁入: 护栏=阈值比较, 全确定性

状态存储: zhuxiang:pdm:mode_state 单键整档 JSON
(对齐 pdm_repository 表键空间互不匹配)。
"""

import json
import logging
import os
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-pdm-mode"

# ============================================================
# 常量(全站范式)
# ============================================================

# 三态
MODE_VALUES = ("off", "shadow", "assist")
DEFAULT_MODE = "off"

# 状态存储键(Redis / 内存)
_STATE_KEY_REDIS = "zhuxiang:pdm:mode_state"
_STATE_KEY_MEM = "pdm_mode_state"

# 护栏指标域(封闭三指标——智能产品管理语义)
GUARD_METRICS = ("manualRejectRate", "imageFlagRate",
                 "aiRejectRate")
GUARD_METRIC_LABELS = {
    "manualRejectRate": "终审驳回率",
    "imageFlagRate": "图片违规标记率",
    "aiRejectRate": "AI 预检拦截率",
}

# 恶化阈值(任一指标相对基线 >3% 自动暂停)
GUARD_DETERIORATION = 0.03

# 指标留痕上限(防无限膨胀)
GUARD_METRICS_MAX = 50

# 进程级快照(62号 av62 范式——服务层一代
# 同步门控 legacy_current_mode 数据源;
# mode service 异步操作后刷新)
_G: dict = {"override": "", "paused": False}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _valid_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODE_VALUES else DEFAULT_MODE


class PdmModeService:
    """智能产品管理大模型三态灰度 + 护栏"""

    def __init__(self):
        self._store = get_in_memory_store()

    # ============================================================
    # 状态存取(整体 JSON——类型安全往返)
    # ============================================================

    async def _load_state(self) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_STATE_KEY_REDIS)
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return None
        return self._store.get(_STATE_KEY_MEM)

    async def _save_state(self, st: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _STATE_KEY_REDIS,
                json.dumps(st, ensure_ascii=False))
        else:
            self._store[_STATE_KEY_MEM] = st
        _refresh_legacy(st)

    async def _state(self) -> dict:
        """运行时态(缺省创建)"""
        st = await self._load_state()
        if st is None:
            st = {
                "stateId": 1,
                "override": "",
                "paused": False,
                "pausedReason": "",
                "pausedAt": "",
                "metrics": [],
                "breachTrail": [],
                "updatedAt": _now_iso(),
            }
            await self._save_state(st)
        return st

    # ============================================================
    # 读取链
    # ============================================================

    async def current_mode(self) -> dict:
        """当前灰度态(读取链: 暂停>override>env>off)

        Returns:
            {mode, source, paused, override,
             envMode, pausedReason}
        """
        st = await self._state()
        env_mode = _valid_mode(
            os.environ.get("PDM_MODE"))
        if st.get("paused"):
            return {
                "mode": "off",
                "source": "guard_pause",
                "paused": True,
                "override": st.get("override", ""),
                "envMode": env_mode,
                "pausedReason": st.get("pausedReason", ""),
            }
        if st.get("override"):
            return {
                "mode": _valid_mode(st["override"]),
                "source": "runtime_override",
                "paused": False,
                "override": st["override"],
                "envMode": env_mode,
                "pausedReason": "",
            }
        return {
            "mode": env_mode,
            "source": "env",
            "paused": False,
            "override": "",
            "envMode": env_mode,
            "pausedReason": "",
        }

    async def require_decision_mode(self) -> dict:
        """决策面门槛(off 拒绝——全站范式)

        Raises:
            ValueError: mode=off(决策面关闭)
        """
        state = await self.current_mode()
        if state["mode"] == "off":
            raise ValueError(
                f"PDM_MODE=off({state['source']}——"
                f"决策面关闭, 观测面不受影响; "
                f"产品展示基础 7 端点零影响)")
        return state

    async def set_override(self, mode: str,
                           operator: str = "admin") -> dict:
        """运行时 override(人工切档留痕——空串清除)"""
        m = str(mode or "").strip().lower()
        if m and m not in MODE_VALUES:
            raise ValueError(
                f"非法灰度态({mode}, 合法值: "
                f"{'/'.join(MODE_VALUES)})")
        st = await self._state()
        st["override"] = m
        st["updatedAt"] = _now_iso()
        await self._save_state(st)
        logger.info("pdm_mode_override=%s by=%s", m or "清除",
                    operator)
        return await self.current_mode()

    # ============================================================
    # 护栏(三指标恶化 >3% 自动暂停)
    # ============================================================

    async def guard_check(
            self, manual_reject_rate: float,
            image_flag_rate: float, ai_reject_rate: float,
            baseline: dict = None) -> dict:
        """护栏检查(确定性阈值比较; 恶化>3% 自动暂停)

        Args:
            manual_reject_rate: 终审驳回率
                (rejected / 终审已决 rejected+on_sale)
            image_flag_rate: 图片违规标记率
                (flagged / 总图片)
            ai_reject_rate: AI 预检拦截率
                (aiReview.action==reject / 全部已预审)
            baseline: 基线三指标(缺省用内置常量基线)

        恶化口径: (cur - base) / base > 3%
        (基线=0 时按绝对值>0 判恶化——防除零)。
        """
        base = {
            "manualRejectRate": 0.10,
            "imageFlagRate": 0.10,
            "aiRejectRate": 0.10,
        }
        base.update({k: max(0.0, float(v))
                     for k, v in (baseline or {}).items()
                     if k in GUARD_METRICS})
        current = {
            "manualRejectRate": max(
                0.0, float(manual_reject_rate or 0)),
            "imageFlagRate": max(
                0.0, float(image_flag_rate or 0)),
            "aiRejectRate": max(
                0.0, float(ai_reject_rate or 0)),
        }
        breaches = []
        for key in GUARD_METRICS:
            cur, bs = current[key], base[key]
            delta = ((cur - bs) / bs if bs > 0
                     else (1.0 if cur > 0 else 0.0))
            if delta > GUARD_DETERIORATION:
                breaches.append({
                    "metric": key,
                    "label": GUARD_METRIC_LABELS[key],
                    "baseline": bs, "current": cur,
                    "deterioration": round(delta, 4)})
        # 指标留痕(滚动截断)
        st = await self._state()
        entry = {
            "checkedAt": _now_iso(),
            "metrics": current,
            "baseline": base,
            "breaches": len(breaches),
        }
        metrics = list(st.get("metrics") or [])
        metrics.append(entry)
        st["metrics"] = metrics[-GUARD_METRICS_MAX:]
        result = {"breached": bool(breaches),
                  "breaches": breaches,
                  "threshold": GUARD_DETERIORATION,
                  "metrics": current,
                  "baseline": base}
        if breaches:
            # 自动暂停(保护机制——非处罚, 可自动)
            reasons = "; ".join(
                f"{b['label']}{b['baseline']}→"
                f"{b['current']}"
                f"(+{b['deterioration']:.1%})"
                for b in breaches)
            st["paused"] = True
            st["pausedReason"] = (
                f"智能产品管理大模型护栏自动暂停: {reasons}")
            st["pausedAt"] = _now_iso()
            trail = list(st.get("breachTrail") or [])
            trail.append({
                "at": st["pausedAt"],
                "reason": st["pausedReason"],
                "breaches": breaches})
            st["breachTrail"] = trail[-GUARD_METRICS_MAX:]
            logger.warning("pdm_mode_guard_paused: %s",
                           st["pausedReason"])
        st["updatedAt"] = _now_iso()
        await self._save_state(st)
        result["pausedNow"] = bool(breaches)
        return result

    async def resume(self, operator: str = "admin",
                     note: str = "") -> dict:
        """人工恢复(护栏暂停解除——决策留痕)"""
        st = await self._state()
        if not st.get("paused"):
            raise ValueError("护栏未处于暂停态, 无需恢复")
        st["paused"] = False
        st["pausedReason"] = ""
        st["resumedBy"] = operator
        st["resumedNote"] = (note or "")[:200]
        st["resumedAt"] = _now_iso()
        st["updatedAt"] = _now_iso()
        await self._save_state(st)
        logger.info("pdm_mode_resumed by=%s note=%s",
                    operator, note[:50])
        return await self.current_mode()

    # ============================================================
    # 观测面
    # ============================================================

    async def status_view(self) -> dict:
        """灰度总览(模式+护栏状态+指标留痕+红线公示)"""
        state = await self.current_mode()
        st = await self._state()
        return {
            **state,
            "modeValues": list(MODE_VALUES),
            "observablesNeverOff": (
                "products list+detail/reviews pending"
                "/versions/listing-advice/design main-image-ab"
                "/images list+detail/report overview"
                "——观测面永不关停(宪法口径)"),
            "decisionSurfaces": (
                "products create+update+submit"
                "+ai-precheck+review+list+delist"
                "/versions rollback/images 全链"
                "+learning-feedback/design 生图+文案"
                "/images rollback"
                "——off 拒绝(409); 产品展示基础 7 端点零影响"),
            "exemptSurfaces": (
                "force-delist 紧急下架安全阀"
                "——永不关停(违规商品即刻下架的"
                "安全处置权)"),
            "guard": {
                "metrics": [
                    {"key": k,
                     "label": GUARD_METRIC_LABELS[k]}
                    for k in GUARD_METRICS],
                "threshold": GUARD_DETERIORATION,
                "pausedAt": st.get("pausedAt", ""),
                "pausedReason": st.get("pausedReason", ""),
                "checkCount": len(st.get("metrics") or []),
                "breachCount": len(st.get("breachTrail")
                                   or []),
            },
            "modelVersion": MODEL_VERSION,
        }


# ============================================================
# 同步桥接(62号 av62 范式——服务层一代门控同源)
# ============================================================

def _refresh_legacy(st: dict) -> None:
    """刷新进程级快照(mode service 读写后调用)"""
    _G["override"] = str(st.get("override") or "")
    _G["paused"] = bool(st.get("paused"))


def legacy_current_mode() -> str:
    """同步模式读取(暂停 > override > env > off)

    供智能产品管理服务层同步门控委托; env 实时读,
    override/paused 来自进程快照(异步操作刷新)。
    """
    if _G["paused"]:
        return "off"
    if _G["override"]:
        return _valid_mode(_G["override"])
    return _valid_mode(
        os.environ.get("PDM_MODE"))
