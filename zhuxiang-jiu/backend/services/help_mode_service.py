"""AI智能叫帮大模型(67号)——三态灰度与护栏服务

依据: 图标主题/条款协议/PDM/智图模式层范式(全站大模型转段标准——
68/45/智运/62/65 同源; 62号同步桥接)。

三态灰度(HELP_MODE, 叫帮前缀):
    off(默认)    决策面关闭(409)——仅锁"新增互助入口"
    shadow       观察学习期(决策放行+响应留痕标记 helpMode)
    assist       辅助生产期(决策生效)

    读取链(优先级): 护栏暂停 > 运行时 override >
    环境变量 HELP_MODE > 默认 off。

宪法豁免面(履约流转 6 端点永不关停——对齐钱包"off 只锁新增
盈利入口, 存量资金退出零影响"范式):
    - accept / start / complete / cancel / review:
      已发布求助的匹配与履约是已建立的互助承诺——
      接单权/履约结算权/发布人撤回权/互信评价回流不可设障
    - parse(需求解析)为发布前置辅助(纯推荐不落库), 归观测面

护栏(叫帮语义三指标, 恶化>3% 自动暂停; 全确定性聚合):
    爽约率 cancelRate
        = cancelled / (completed + cancelled)
          (已决分母——在途单不进分母, 对齐智图"已处置口径")
    差评率 lowReviewRate
        = 评价 score <= 2 / 总评价(5 分制)
          (互助质量恶化代理)
    在途滞留率 stuckRate
        = (matched + in_progress) /
          (matched + in_progress + completed)
          (调度流转恶化代理; 宽口径 0.60, 对齐智图月台饱和率)
    (cur-base)/base > 3% → 自动暂停(等效 off, 留痕);
    恢复须人工 resume(决策留痕)。护栏暂停是保护机制
    (功能开关, 非处罚), 可自动。
    小样本保护: 分母不足 10 的指标跳过恶化判定
    (指标照常留痕)——6 单 1 取消=16.7% 是单次事件
    非业务恶化(生产首轮误判实测修正)。

红线:
    - 大厅/类目/信值档案/匹配推荐/故事卡/安全护航/碳积分/
      白皮书/信任指数/开放统计——观测面永不关停
    - 订单状态机 published→matched→in_progress→completed/
      cancelled 语义保留, 不受 mode 影响
    - 一代安全预检(确定性违禁词表)语义保留——红线不受 mode 影响
    - LLM 禁入: 护栏=阈值比较, 全确定性

状态存储: zhuxiang:help:mode_state 单键整档 JSON。
"""

import json
import logging
import os
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-help-mode"

# ============================================================
# 常量(全站范式)
# ============================================================

# 三态
MODE_VALUES = ("off", "shadow", "assist")
DEFAULT_MODE = "off"

# 状态存储键(Redis / 内存)
_STATE_KEY_REDIS = "zhuxiang:help:mode_state"
_STATE_KEY_MEM = "help_mode_state"

# 护栏指标域(封闭三指标——叫帮语义)
GUARD_METRICS = ("cancelRate", "lowReviewRate", "stuckRate")
GUARD_METRIC_LABELS = {
    "cancelRate": "爽约率",
    "lowReviewRate": "差评率",
    "stuckRate": "在途滞留率",
}

# 恶化阈值(任一指标相对基线 >3% 自动暂停)
GUARD_DETERIORATION = 0.03

# 指标留痕上限(防无限膨胀)
GUARD_METRICS_MAX = 50

# 护栏最小分母样本(小样本不判恶化——单次事件不代表业务恶化;
# 智图"冷启动不误判"同类口径)
_MIN_SAMPLE = 10

# 进程级快照(62号 av62 范式——服务层一代
# 同步门控数据源; mode service 异步操作后刷新)
_G: dict = {"override": "", "paused": False}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _valid_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODE_VALUES else DEFAULT_MODE


class HelpModeService:
    """AI智能叫帮大模型三态灰度 + 护栏"""

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
        env_mode = _valid_mode(os.environ.get("HELP_MODE"))
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
                f"HELP_MODE=off({state['source']}——"
                f"决策面关闭(新增互助入口), 观测面不受影响; "
                f"存量互助履约流转不受影响)")
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
        logger.info("help_mode_override=%s by=%s",
                    m or "清除", operator)
        return await self.current_mode()

    # ============================================================
    # 护栏(三指标恶化 >3% 自动暂停)
    # ============================================================

    async def guard_check(self, cancel_rate: float,
                          low_review_rate: float,
                          stuck_rate: float,
                          baseline: dict = None,
                          skip_metrics: tuple = ()) -> dict:
        """护栏检查(确定性阈值比较; 恶化>3% 自动暂停)

        Args:
            cancel_rate: 爽约率
                (cancelled / (completed + cancelled))
            low_review_rate: 差评率
                (评价 score<=2 / 总评价)
            stuck_rate: 在途滞留率
                ((matched+in_progress) /
                 (matched+in_progress+completed))
            baseline: 基线三指标(缺省用内置常量基线)
            skip_metrics: 跳过的指标键(小样本保护——
                分母不足最小样本时由 patrol 传入, 不判恶化)

        恶化口径: (cur - base) / base > 3%
        (基线=0 时按绝对值>0 判恶化——防除零)。
        """
        base = {
            "cancelRate": 0.10,
            "lowReviewRate": 0.05,
            "stuckRate": 0.60,
        }
        base.update({k: max(0.0, float(v))
                     for k, v in (baseline or {}).items()
                     if k in GUARD_METRICS})
        current = {
            "cancelRate": max(0.0, float(cancel_rate or 0)),
            "lowReviewRate": max(
                0.0, float(low_review_rate or 0)),
            "stuckRate": max(0.0, float(stuck_rate or 0)),
        }
        breaches = []
        for key in GUARD_METRICS:
            if key in skip_metrics:
                continue  # 小样本保护: 不判恶化(指标照常留痕)
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
                f"AI智能叫帮大模型护栏自动暂停: {reasons}")
            st["pausedAt"] = _now_iso()
            trail = list(st.get("breachTrail") or [])
            trail.append({
                "at": st["pausedAt"],
                "reason": st["pausedReason"],
                "breaches": breaches})
            st["breachTrail"] = trail[-GUARD_METRICS_MAX:]
            logger.warning("help_mode_guard_paused: %s",
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
        logger.info("help_mode_resumed by=%s note=%s",
                    operator, note[:50])
        return await self.current_mode()

    # ============================================================
    # 护栏巡检聚合(确定性——LLM 禁入)
    # ============================================================

    async def aggregate_guard_metrics(self) -> dict:
        """从仓储确定性聚合三指标(巡检数据源)

        口径:
            爽约率 = cancelled / (completed + cancelled)
                (已决分母——在途单不进分母)
            差评率 = score<=2 评价 / 总评价
            在途滞留率 = (matched + in_progress) /
                (matched + in_progress + completed)
        冷启动(分母 0)按 0 处理(不判恶化)。
        """
        from repositories.help_repository import (
            HelpRepository,
        )
        repo = HelpRepository()
        orders = await repo.list_orders(limit=10000)
        by_status = {"completed": 0, "cancelled": 0,
                     "matched": 0, "in_progress": 0}
        for o in orders:
            s = o.get("status")
            if s in by_status:
                by_status[s] += 1
        decided = (by_status["completed"]
                   + by_status["cancelled"])
        cancel_rate = (by_status["cancelled"] / decided
                       if decided else 0.0)
        active = (by_status["matched"]
                  + by_status["in_progress"])
        flow = active + by_status["completed"]
        stuck_rate = (active / flow if flow else 0.0)

        reviews = await repo.list_reviews(limit=10000)
        total_reviews = len(reviews)
        low_reviews = sum(
            1 for r in reviews
            if int(r.get("score") or 0) <= 2)
        low_review_rate = (low_reviews / total_reviews
                           if total_reviews else 0.0)
        return {
            "cancelRate": round(cancel_rate, 4),
            "lowReviewRate": round(low_review_rate, 4),
            "stuckRate": round(stuck_rate, 4),
            "sample": {
                "orders": len(orders),
                "decided": decided,
                "completed": by_status["completed"],
                "inFlight": active,
                "reviews": total_reviews,
            },
        }

    async def patrol(self) -> dict:
        """护栏自动巡检(聚合 → 检查)

        小样本保护(智图"冷启动不误判"口径): 分母不足
        最小样本(_MIN_SAMPLE=10)的指标跳过恶化判定——
        小样本下单次事件(如 6 单 1 取消=16.7%)不代表
        业务恶化, 指标照常留痕但不自动暂停。
        """
        metrics = await self.aggregate_guard_metrics()
        sample = metrics.pop("sample", {})
        skip = set()
        if sample.get("decided", 0) < _MIN_SAMPLE:
            skip.add("cancelRate")
        if sample.get("reviews", 0) < _MIN_SAMPLE:
            skip.add("lowReviewRate")
        flow = (sample.get("inFlight", 0)
                + sample.get("completed", 0))
        if flow < _MIN_SAMPLE:
            skip.add("stuckRate")
        result = await self.guard_check(
            cancel_rate=metrics["cancelRate"],
            low_review_rate=metrics["lowReviewRate"],
            stuck_rate=metrics["stuckRate"],
            skip_metrics=tuple(skip))
        result["sample"] = sample
        result["skippedSmallSample"] = sorted(skip)
        return result

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
                "hall orders/categories/order detail/trust"
                "/match/preferences/story/guard/carbon"
                "/whitepaper/trust-index/open stats/parse"
                "——观测面永不关停"),
            "decisionSurfaces": (
                "publish orders/donate/heritage apply+accept"
                "+cancel/csr packages+donate"
                "——off 拒绝(409)"),
            "exemptSurfaces": (
                "accept/start/complete/cancel/review"
                "——履约流转宪法豁免永不关停"
                "(存量互助承诺不可设障)"),
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

    供叫帮服务层同步门控委托; env 实时读,
    override/paused 来自进程快照(异步操作刷新)。
    """
    if _G["paused"]:
        return "off"
    if _G["override"]:
        return _valid_mode(_G["override"])
    return _valid_mode(os.environ.get("HELP_MODE"))
