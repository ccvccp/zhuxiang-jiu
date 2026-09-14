"""37号·AI智能网站同盟大模型——三态灰度与护栏服务
(alliance_mode_service)

对齐全站范式(68号 xinzhi_mode_service 同款口径):

三态灰度(ALLIANCE37_MODE):
    off          决策面关闭(409)——观测面不受影响
    shadow       观察学习期(决策放行+留痕标记)
    assist       辅助生产期(决策生效)

    默认 assist(业务在线铁律——37号 P0-P2 已
    于 2026-09-01 上线生产, 8 商户/16 商品在营,
    升级治理层不得阻断既有交易; 新模块零影响
    范式默认 off, 已在线业务升级默认 assist)。

    读取链(优先级): 护栏暂停 > 运行时 override >
    环境变量 ALLIANCE37_MODE > 默认 assist。

观测面永不关停(宪法口径"观测与纠错永不关停"):
    商户/商品/评价/结算/报表/考核/白皮书等
    全部 GET 查询端点不受 mode 影响。

护栏(三指标对照基线, 任一恶化>3% 自动暂停):
    退款率 refundRate / 客诉进线率 complaintRate /
    商户清退率 terminationRate
    (cur-base)/base > 3% → 自动暂停(等效 off,
    运行时态留痕)。护栏暂停是保护机制(功能
    开关, 非处罚), 可自动; 恢复须人工 resume。

红线(宪法域):
    - 观测面永不关停: mode 只挡决策面
    - 护栏暂停非处罚: 不涉商户处置, 仅功能开关
    - LLM 禁入: 护栏=确定性阈值比较
"""

import logging
import os
from datetime import datetime, timedelta, UTC

from repositories.alliance_repository import (
    AllianceRepository,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-alliance37-mode"

# ============================================================
# 治理常量
# ============================================================

# 三态(全站范式)
MODE_VALUES = ("off", "shadow", "assist")

# 默认 assist(业务在线铁律——区别于新模块默认 off)
DEFAULT_MODE = "assist"

# 护栏指标域(封闭三指标)
GUARD_METRICS = ("refundRate", "complaintRate",
                 "terminationRate")
GUARD_METRIC_LABELS = {
    "refundRate": "退款率",
    "complaintRate": "客诉进线率",
    "terminationRate": "商户清退率",
}

# 恶化阈值(任一指标相对基线 >3% 自动暂停)
GUARD_DETERIORATION = 0.03

# 指标留痕上限(防无限膨胀)
GUARD_METRICS_MAX = 50

# ============================================================
# 实测指标聚合常量(护栏自动源——确定性, LLM 禁入)
# ============================================================

# 滚动窗口(天)
GUARD_WINDOW_DAYS = 30

# 样本门(窗口内样本不足不出数——防小样本误暂停)
GUARD_REFUND_SAMPLE_GATE = 5      # 结算单数
GUARD_COMPLAINT_SAMPLE_GATE = 5   # 评价数
GUARD_TERMINATION_SAMPLE_GATE = 3  # 商户数


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _valid_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODE_VALUES else DEFAULT_MODE


class AllianceModeService:
    """37号·三态灰度 + 护栏(大模型治理层)"""

    def __init__(self, repo: AllianceRepository = None):
        self.repo = (repo if repo is not None
                     else AllianceRepository())

    # ============================================================
    # 运行时态
    # ============================================================

    async def _state(self) -> dict:
        """运行时态(缺省创建——单例 stateId=1)"""
        st = await self.repo.load_state()
        if st is None:
            st = {
                "stateId": AllianceRepository.STATE_ID,
                "override": "",
                "paused": False,
                "pausedReason": "",
                "pausedAt": "",
                "metrics": [],
                "breachTrail": [],
                "updatedAt": _now_iso(),
            }
            await self.repo.save_state(st)
        return st

    async def current_mode(self) -> dict:
        """当前灰度态(读取链: 暂停>override>env>assist)

        Returns:
            {mode, source, paused, override,
             envMode, pausedReason}
        """
        st = await self._state()
        env_mode = _valid_mode(
            os.environ.get("ALLIANCE37_MODE"))
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
                f"ALLIANCE37_MODE=off({state['source']}——"
                f"决策面关闭, 观测面不受影响)")
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
        await self.repo.save_state(st)
        logger.info("alliance37_override=%s by=%s",
                    m or "清除", operator)
        return await self.current_mode()

    # ============================================================
    # 护栏(三指标恶化 >3% 自动暂停)
    # ============================================================

    async def guard_check(
            self, refund_rate: float,
            complaint_rate: float,
            termination_rate: float,
            baseline: dict = None) -> dict:
        """护栏检查(确定性阈值比较; 恶化>3% 自动暂停)

        Args:
            refund_rate: 当期退款率
            complaint_rate: 当期客诉进线率
            termination_rate: 当期商户清退率
            baseline: 基线三指标(缺省用内置常量基线)

        恶化口径: (cur - base) / base > 3%
        (基线=0 时按绝对值>0 判恶化——防除零)。
        """
        base = {
            "refundRate": 0.05,
            "complaintRate": 0.02,
            "terminationRate": 0.03,
        }
        base.update({k: max(0.0, float(v))
                     for k, v in (baseline or {}).items()
                     if k in GUARD_METRICS})
        current = {
            "refundRate": max(0.0, float(refund_rate or 0)),
            "complaintRate": max(
                0.0, float(complaint_rate or 0)),
            "terminationRate": max(
                0.0, float(termination_rate or 0)),
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
                f"护栏自动暂停: {reasons}")
            st["pausedAt"] = _now_iso()
            trail = list(st.get("breachTrail") or [])
            trail.append({
                "at": st["pausedAt"],
                "reason": st["pausedReason"],
                "breaches": breaches})
            st["breachTrail"] = trail[-GUARD_METRICS_MAX:]
            logger.warning("alliance37_guard_paused: %s",
                           st["pausedReason"])
        st["updatedAt"] = _now_iso()
        await self.repo.save_state(st)
        result["pausedNow"] = bool(breaches)
        return result

    # ============================================================
    # 实测指标自动聚合(护栏自动源——确定性, LLM 禁入)
    # ============================================================

    @staticmethod
    def _in_window(iso: str, since_dt: datetime) -> bool:
        """ISO 时间戳容错解析+窗口判定(空/畸形→False)"""
        if not iso:
            return False
        try:
            dt = datetime.fromisoformat(
                str(iso).replace("Z", "+00:00"))
            return dt >= since_dt
        except ValueError:
            return False

    async def auto_metrics(
            self, window_days: int = None) -> dict:
        """实测三指标聚合(纯同盟域——确定性公式)

        指标口径:
            refundRate 退款率 = 窗口内冲正结算单 /
                (窗口内已结算+冲正结算单)
            complaintRate 客诉进线率 = 窗口内差评
                (score≤2) / 窗口内总评价
            terminationRate 商户清退率 = 窗口内终止
                商户 / 全量商户

        样本门(防小样本误暂停): 结算单 <5 / 评价 <5 /
        商户 <3 → 对应指标记 0 且标记 gated。
        """
        days = int(GUARD_WINDOW_DAYS if window_days is None
                   else window_days)
        if days <= 0:
            raise ValueError("窗口天数必须为正整数")
        since = datetime.now(UTC) - timedelta(days=days)

        # ① 退款率(结算单冲正占比)
        settlements = await self.repo.list_settlements(
            limit=100000)
        w_settled = [
            s for s in settlements
            if s.get("status") == "settled"
            and self._in_window(s.get("settledAt"),
                                since)]
        w_reversed = [
            s for s in settlements
            if s.get("status") == "reversed"
            and self._in_window(s.get("reversedAt"),
                                since)]
        s_total = len(w_settled) + len(w_reversed)
        refund_gated = s_total < GUARD_REFUND_SAMPLE_GATE
        refund_rate = (0.0 if refund_gated else
                       len(w_reversed) / s_total
                       if s_total else 0.0)

        # ② 客诉进线率(差评进线占比)
        reviews = await self.repo.list_reviews(
            limit=100000)
        w_reviews = [
            r for r in reviews
            if self._in_window(r.get("createdAt"), since)]
        w_complaints = [
            r for r in w_reviews
            if int(r.get("score") or 0) <= 2]
        r_total = len(w_reviews)
        complaint_gated = (r_total
                           < GUARD_COMPLAINT_SAMPLE_GATE)
        complaint_rate = (0.0 if complaint_gated else
                          len(w_complaints) / r_total
                          if r_total else 0.0)

        # ③ 商户清退率(终止商户占比)
        merchants = await self.repo.list_merchants(
            limit=100000)
        w_terminated = [
            m for m in merchants
            if m.get("status") == "terminated"
            and self._in_window(m.get("terminatedAt"),
                                since)]
        m_total = len(merchants)
        term_gated = m_total < GUARD_TERMINATION_SAMPLE_GATE
        termination_rate = (0.0 if term_gated else
                            len(w_terminated) / m_total
                            if m_total else 0.0)

        return {
            "windowDays": days,
            "metrics": {
                "refundRate": round(refund_rate, 4),
                "complaintRate": round(complaint_rate, 4),
                "terminationRate": round(
                    termination_rate, 4),
            },
            "samples": {
                "settlements": s_total,
                "reviews": r_total,
                "merchants": m_total,
            },
            "gated": {
                "refundRate": refund_gated,
                "complaintRate": complaint_gated,
                "terminationRate": term_gated,
            },
            "source": "alliance-domain-auto",
        }

    async def auto_guard_check(
            self, window_days: int = None,
            baseline: dict = None) -> dict:
        """实测护栏检查(自动源聚合→guard_check)

        恶化>3% 自动暂停(与手动口径同一条链路,
        差异仅在于指标来源为实测聚合)。
        """
        computed = await self.auto_metrics(window_days)
        m = computed["metrics"]
        guard = await self.guard_check(
            refund_rate=m["refundRate"],
            complaint_rate=m["complaintRate"],
            termination_rate=m["terminationRate"],
            baseline=baseline)
        return {"computed": computed, "guard": guard}

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
        await self.repo.save_state(st)
        logger.info("alliance37_resumed by=%s note=%s",
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
            "defaultNote": (
                "默认 assist(业务在线铁律——37号 P0-P2 "
                "已上线, 升级治理层不阻断既有交易)"),
            "observablesNeverOff": (
                "merchants/products/reviews/settlements/"
                "report/assessment/coverage/geo/custom-demands/"
                "whitepaper——观测面永不关停(宪法口径)"),
            "decisionSurfaces": (
                "apply/audit/merchant状态(activate/confirm/"
                "suspend/terminate)/products上下架/order/"
                "settle/reverse/settle-run/share-settings/"
                "review提交/fold/assessment run/coverage设置/"
                "scenes gathering/redeem/custom-demands"
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
