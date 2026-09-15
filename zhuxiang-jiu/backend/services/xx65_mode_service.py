"""65号·网店及商品AI智能管理——三态灰度与护栏服务

依据: 智运 ZwModeService / 45号 Trust45ModeService 范式
(全站大模型转段标准——PAY60/XX64/MEMBER73/ATTRACT72/NEXUSFLOW74 同款)。

三态灰度(XX65_MODE, 65号前缀):
    off(默认)    决策面关闭(409)——观测面不受影响
    shadow       开店观察期(决策放行+留痕, 不初始化)
    assist       辅助经营期(开店开放)

    读取链(优先级): 护栏暂停 > 运行时 override >
    环境变量 XX65_MODE > 默认 off。
    观测面永不关停(registry/shops 列表详情/model status/
    drafts 详情/products/order-window/campaigns recommend
    列表 report/health/coach/learn status/dashboard)。
    宪法豁免面永不关停(关店=经营者退出权利/人工兜底 S6/
    上架后巡检/回流 collect——productId 1:1 幂等)。

护栏(65号业务语义三指标, 恶化>3% 自动暂停):
    S1 合规拦截率 complianceBlockRate (AI 生成内容被
        三道防线拦截占比——AI 内容质量代理)
    店铺违规率 shopViolationRate    (active→suspended
        违规冻结占比——经营合规代理)
    S5 撤销率 campaignRevokeRate    (营销承诺 5 分钟
        窗口撤销占比——营销质量代理)
    (cur-base)/base > 3% → 自动暂停(等效 off, 留痕);
    恢复须人工 resume(决策留痕)。护栏暂停是保护机制
    (功能开关, 非处罚), 可自动。

红线(宪法域):
    - S1-S8 刚性规则不受 mode 影响(registry 宪法级)
    - 关店/人工兜底/巡检/回流不受开关影响
    - LLM 禁入判定链(护栏=阈值比较, 全确定性)

状态存储: zhuxiang:xx65:mode_state 单键整档 JSON
(对齐智运范式——整体序列化, 无字段级清单风险,
规避 45号 P0 序列化教训)。
"""

import json
import logging
import os
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-xx65-mode"

# 进程级快照(62号 av62 范式——服务层一代
# 同步门控 legacy_current_mode 数据源;
# mode service 异步操作后刷新)
_G: dict = {"override": "", "paused": False}


# ============================================================
# 常量(全站范式)
# ============================================================

# 三态(对齐 xx65_registry.MODE_VALUES——同源不漂移)
MODE_VALUES = ("off", "shadow", "assist")
DEFAULT_MODE = "off"

# 状态存储键(Redis / 内存)
_STATE_KEY_REDIS = "zhuxiang:xx65:mode_state"
_STATE_KEY_MEM = "xx65_mode_state"

# 护栏指标域(封闭三指标——65号网店及商品语义)
GUARD_METRICS = ("complianceBlockRate",
                 "shopViolationRate",
                 "campaignRevokeRate")
GUARD_METRIC_LABELS = {
    "complianceBlockRate": "S1 合规拦截率",
    "shopViolationRate": "店铺违规率",
    "campaignRevokeRate": "S5 撤销率",
}

# 恶化阈值(任一指标相对基线 >3% 自动暂停)
GUARD_DETERIORATION = 0.03

# 指标留痕上限(防无限膨胀)
GUARD_METRICS_MAX = 50


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _valid_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODE_VALUES else DEFAULT_MODE


class Xx65ModeService:
    """65号大模型三态灰度 + 护栏"""

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
        else:
            _refresh_legacy(st)
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
            os.environ.get("XX65_MODE"))
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
                f"XX65_MODE=off({state['source']}——"
                f"决策面关闭, 观测面不受影响; "
                f"关店/人工兜底/巡检/回流"
                f"不受开关影响)")
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
        logger.info("xx65_mode_override=%s by=%s", m or "清除",
                    operator)
        return await self.current_mode()

    # ============================================================
    # 护栏(三指标恶化 >3% 自动暂停)
    # ============================================================

    async def guard_check(
            self, compliance_block_rate: float,
            shop_violation_rate: float,
            campaign_revoke_rate: float,
            baseline: dict = None) -> dict:
        """护栏检查(确定性阈值比较; 恶化>3% 自动暂停)

        Args:
            compliance_block_rate: S1 合规拦截率
                (AI 生成内容被三道防线拦截占比)
            shop_violation_rate: 店铺违规率
                (active→suspended 违规冻结占比)
            campaign_revoke_rate: S5 撤销率
                (营销承诺 5 分钟窗口撤销占比)
            baseline: 基线三指标(缺省用内置常量基线)

        恶化口径: (cur - base) / base > 3%
        (基线=0 时按绝对值>0 判恶化——防除零)。
        """
        base = {
            "complianceBlockRate": 0.10,
            "shopViolationRate": 0.05,
            "campaignRevokeRate": 0.05,
        }
        base.update({k: max(0.0, float(v))
                    for k, v in (baseline or {}).items()
                    if k in GUARD_METRICS})
        current = {
            "complianceBlockRate": max(
                0.0, float(compliance_block_rate or 0)),
            "shopViolationRate": max(
                0.0, float(shop_violation_rate or 0)),
            "campaignRevokeRate": max(
                0.0, float(campaign_revoke_rate or 0)),
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
                f"65号大模型护栏自动暂停: {reasons}")
            st["pausedAt"] = _now_iso()
            trail = list(st.get("breachTrail") or [])
            trail.append({
                "at": st["pausedAt"],
                "reason": st["pausedReason"],
                "breaches": breaches})
            st["breachTrail"] = trail[-GUARD_METRICS_MAX:]
            logger.warning("xx65_mode_guard_paused: %s",
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
        logger.info("xx65_mode_resumed by=%s note=%s",
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
                "registry/shops列表详情/model-status/"
                "drafts详情/products/order-window/"
                "campaigns recommend列表report/health/"
                "coach/learn-status/dashboard"
                "——观测面永不关停(宪法口径)"),
            "exemptNeverOff": (
                "shops close(经营者退出权利)/"
                "drafts human-review(S6 人工兜底)/"
                "products inspect(上架后巡检——合规防线)/"
                "feedback collect(回流 productId 1:1 幂等)"
                "——宪法豁免面永不关停"),
            "decisionSurfaces": (
                "intents/parse, shops apply/claim/activate, "
                "products/draft, drafts/publish, campaigns "
                "create/revoke, quota-adjust, "
                "dispute-assist, redteam"
                "——off 拒绝(409)"),
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

    供 xx65_service 一代同步门控委托; env 实时读,
    override/paused 来自进程快照(异步操作刷新)。
    """
    if _G["paused"]:
        return "off"
    if _G["override"]:
        return _valid_mode(_G["override"])
    return _valid_mode(
        os.environ.get("XX65_MODE"))
