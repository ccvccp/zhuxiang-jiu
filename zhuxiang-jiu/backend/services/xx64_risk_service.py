"""64号·信值兑换管理 动态风控层
(xx64_risk_service, P3)

计划(docs/64号_P3_动态风控层详细设计.md
§三/§四/§七):
    ① 五防检测器(确定性模式库——
       阈值+窗口聚合纯函数, LLM 不进
       判定链, 同输入同输出):
        ARB-HF      套利-高频小额
                    (24h ≥10 笔且总额
                    > 单次限额×3)
        ARB-MA      套利-多账号集中
                    (同商品/同卖方 1h 内
                    ≥5 账号)
        PTS-SHOCK   积分冲击
                    (量级 ≥1000 信值/
                    持续 3 日满频/
                    探测 ≥5 次——含取消)
        PRICE-MANIP 价格操纵
                    (7 日均价涨幅 >20%
                    且叠加信值支付)
        LIQ-CRUNCH  流动性推演
                    (24h 消耗推演触
                    总信值 40%)
    ② 分级处置引擎(风险分三档
       ×47号 tier 摩擦修正):
        低(<40)     直通(pass)
        中(40-69)   标记+下笔增强
                    验证(enhanced_verify)
        高(≥70)     阻断当前笔+冻结
                    待审(freeze_review)
    ③ 触发链路:
        同步前置——pay/exchange 决策
        面入口三查(单用户轻量)
        手动扫描——POST /risk/scan
        全量五防(仅落事件+建议书)

铁律(计划 §一 QC):
    - 预警仅建议不自动处置——惩罚性
      处置(tier 降档/下架/冷却/账户
      冻结)永远只输出建议书(人工/
      46号执行——永不自动)
    - 交易级拦截仅"当前这一笔":
      assist 态阻断(可申诉秒级解冻
      ——P4 通道); shadow 态仅观察
      留痕不阻断(站内模式铁律)
    - 45/47/46号零改动(tier/总分/
      审批总线纯读取/提交)
"""

import logging
import os
from datetime import datetime, timedelta, UTC

from core.helpers import ts

from repositories.xx64_repository import (
    Xx64Repository,
)

logger = logging.getLogger("xx64_risk")

MODEL_VERSION = "v1-xx64-risk"

SCORER_ID = "value_exchange"

# ============================================================
# 检测阈值(计划 §三——全部确定性)
# ============================================================

ARB_HF_MIN_ORDERS = 10        # 24h 笔数阈值
ARB_HF_MULTIPLE = 3           # 单次限额倍数
ARB_MA_MIN_ACCOUNTS = 5       # 集中账号数阈值
ARB_MA_WINDOW_MIN = 60        # 集中窗口(分钟)
PTS_SHOCK_MAX_TRUST = 1000.0  # 24h 信值等值阈值
PTS_SHOCK_CONSEC_DAYS = 3     # 持续天数(每日满频)
PTS_SHOCK_PROBE_ATTEMPTS = 5  # 探测尝试次数(含取消)
PRICE_MANIP_MAX_DRIFT = 0.20   # 7 日均价涨幅阈值
PRICE_MANIP_MIN_SAMPLES = 3    # 样本下限(冷启动防误报)
PRICE_MANIP_WINDOW_DAYS = 7    # 价格窗口(日)
LIQ_CRUNCH_RATIO = 0.40        # 24h 消耗推演占比

SEVERITY_WEIGHT = {
    "low": 20, "medium": 45, "high": 75,
}
MULTI_FINDING_BONUS = 10       # 叠加命中加成/额外

# 47号 tier 摩擦修正(信任越高摩擦
# 越低——对齐"支付摩擦与信任成反比"
# 铁律; tier 经 trust_risk_profile
# 纯读取)
TIER_FRICTION = {
    "trusted": 0.6,
    "standard": 0.8,
    "watched": 1.0,
    "restricted": 1.2,
}

LEVEL_LOW_MAX = 39             # 低档上限(含)
LEVEL_HIGH_MIN = 70            # 高档下限(含)

PAID_STATES = ("paid", "settled",
               "completed")


def current_mode() -> str:
    """模块开关(XX64_MODE——同底座)"""
    return os.environ.get(
        "XX64_MODE", "off")


def risk_enforce_enabled() -> bool:
    """交易级拦截开关(assist 态
    ——shadow 仅观察留痕)"""
    return current_mode() == "assist"


def _parse_dt(value) -> datetime | None:
    """ISO 时间解析(容错——
    无效返回 None)"""
    try:
        return datetime.fromisoformat(
            str(value))
    except (TypeError, ValueError):
        return None


def _within_hours(value, hours: float
                  ) -> bool:
    dt = _parse_dt(value)
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt >= datetime.now(UTC) \
        - timedelta(hours=hours)


def risk_score(findings: list,
               tier: str = None) -> int:
    """风险分(确定性加权——
    severity 权重+叠加加成
    ×tier 摩擦修正)

    tier=None(商品/卖方/全局实体)
    摩擦 1.0。
    """
    base = sum(
        SEVERITY_WEIGHT.get(
            f.get("severity"), 0)
        for f in (findings or []))
    base += MULTI_FINDING_BONUS * max(
        0, len(findings or []) - 1)
    friction = TIER_FRICTION.get(
        str(tier or ""), 1.0)
    return max(0, min(100, round(
        base * friction)))


def disposition(score: int) -> dict:
    """三档处置(计划 §四.3)"""
    if score <= LEVEL_LOW_MAX:
        return {
            "level": "low",
            "action": "pass",
            "note": "直通",
        }
    if score < LEVEL_HIGH_MIN:
        return {
            "level": "medium",
            "action": "enhanced_verify",
            "note": "标记+下笔增强验证",
        }
    return {
        "level": "high",
        "action": "freeze_review",
        "note": "阻断当前笔+冻结待审",
    }


class Xx64RiskService:
    """64号动态风控层(P3——五防
    检测+分级处置)"""

    def __init__(self):
        self.repo = Xx64Repository()

    # ============================================================
    # ① 五防检测器(纯只读——
    # 命中输出 finding 全量指标)
    # ============================================================

    async def detect_arb_hf(
            self, buyer_id: int,
            trust_id: int = None
    ) -> dict | None:
        """ARB-HF 套利-高频小额
        (RT-02 拆单绕限——24h
        ≥10 笔且总额>单次限额×3)

        Args:
            buyer_id: 买方 memberId
            trust_id: 45号档案 id
                (余额基准——缺省取
                订单最近快照 fail-soft)
        """
        orders = await self.repo.list_orders(
            buyer_id=int(buyer_id),
            limit=200)
        recent = [
            o for o in orders
            if o.get("status") in PAID_STATES
            and _within_hours(
                o.get("paidAt")
                or o.get("createdAt"), 24)]
        count = len(recent)
        total_tv = round(sum(
            float(o.get("trustValue") or 0)
            for o in recent), 2)
        # 余额基准(45号只读——
        # 档案缺失回退最近快照)
        balance = None
        if recent and trust_id:
            from services.xx64_service import (
                get_trust_balance,
            )
            try:
                bal = await get_trust_balance(
                    int(trust_id))
                balance = float(
                    bal["balance"])
            except (KeyError, ValueError):
                balance = float(
                    recent[0].get(
                        "balanceSnapshot")
                    or 0)
        elif recent:
            balance = float(
                recent[0].get(
                    "balanceSnapshot") or 0)
        quota_x3 = round(
            (balance or 0) * 0.20
            * ARB_HF_MULTIPLE, 2)
        if count >= ARB_HF_MIN_ORDERS \
                and total_tv > quota_x3:
            return {
                "detector": "ARB-HF",
                "severity": "medium",
                "entityType": "user",
                "entityId": int(buyer_id),
                "trustId": int(trust_id
                                or 0),
                "detail": {
                    "orderCount": count,
                    "trustTotal":
                        total_tv,
                    "quotaX3": quota_x3,
                    "balance": balance,
                    "windowHours": 24,
                    "rule": f"24h 兑换 {count}"
                            f" 笔(≥{ARB_HF_MIN_ORDERS})"
                            f"且总额 {total_tv}"
                            f" > 单次限额×"
                            f"{ARB_HF_MULTIPLE}"
                            f"={quota_x3}",
                },
            }
        return None

    async def detect_arb_ma(
            self, product: str = None,
            seller_id: int = None
    ) -> dict | None:
        """ARB-MA 套利-多账号集中
        (同商品 1h ≥5 账号=high;
        同卖方 1h ≥5 账号跨 ≥2 商品
        =medium 弱信号)"""
        orders = await self.repo.list_orders(
            limit=200)
        recent = [
            o for o in orders
            if o.get("status") in PAID_STATES
            and _within_hours(
                o.get("paidAt")
                or o.get("createdAt"),
                ARB_MA_WINDOW_MIN / 60)]
        # ① 同商品集中(high)
        if product:
            same = [
                o for o in recent
                if o.get("product")
                == product]
            buyers = sorted({
                int(o.get("buyerId") or 0)
                for o in same})
            if len(buyers) \
                    >= ARB_MA_MIN_ACCOUNTS:
                return {
                    "detector": "ARB-MA",
                    "severity": "high",
                    "entityType": "product",
                    "entityId": str(product),
                    "detail": {
                        "accountCount":
                            len(buyers),
                        "buyers": buyers,
                        "product": product,
                        "windowMinutes":
                            ARB_MA_WINDOW_MIN,
                        "rule": f"商品 {product}"
                                f" 1h 内 "
                                f"{len(buyers)} "
                                f"账号集中兑换"
                                f"(≥"
                                f"{ARB_MA_MIN_ACCOUNTS}"
                                f")",
                    },
                }
        # ② 同卖方弱信号(medium——
        # 跨 ≥2 商品的集中)
        if seller_id:
            same = [
                o for o in recent
                if int(o.get("sellerId")
                       or 0)
                == int(seller_id)]
            buyers = sorted({
                int(o.get("buyerId") or 0)
                for o in same})
            products = sorted({
                str(o.get("product") or "")
                for o in same})
            if len(buyers) \
                    >= ARB_MA_MIN_ACCOUNTS \
                    and len(products) >= 2:
                return {
                    "detector": "ARB-MA",
                    "severity": "medium",
                    "entityType": "seller",
                    "entityId": int(seller_id),
                    "detail": {
                        "accountCount":
                            len(buyers),
                        "buyers": buyers,
                        "productCount":
                            len(products),
                        "sellerId":
                            int(seller_id),
                        "windowMinutes":
                            ARB_MA_WINDOW_MIN,
                        "rule": f"卖方 "
                                f"{seller_id} 1h "
                                f"内 {len(buyers)}"
                                f" 账号跨 "
                                f"{len(products)}"
                                f" 商品集中",
                    },
                }
        return None

    async def detect_pts_shock(
            self, user_id: int
    ) -> dict | None:
        """PTS-SHOCK 积分冲击
        (RT-03——三信号任一命中:
        ① 量级 24h ≥1000 信值=high
        ② 持续 3 日每日满频=medium
        ③ 探测 当日 ≥5 次含取消
        =medium)"""
        records = await self.repo \
            .list_exchanges(
                user_id=int(user_id),
                limit=200)
        trust_ids = {
            int(r.get("trustId") or 0)
            for r in records}
        trust_id = next(
            iter(trust_ids)) \
            if trust_ids else 0
        active = [
            r for r in records
            if r.get("status") in (
                "pending", "credited")]
        # ① 量级(24h 信值等值)
        recent = [
            r for r in active
            if _within_hours(
                r.get("createdAt"), 24)]
        tv_24h = round(sum(
            float(r.get("pointsValue")
                   or 0)
            for r in recent), 2)
        if tv_24h >= PTS_SHOCK_MAX_TRUST:
            return {
                "detector":
                    "PTS-SHOCK",
                "severity": "high",
                "entityType": "user",
                "entityId": int(user_id),
                "trustId": trust_id,
                "detail": {
                    "signal": "magnitude",
                    "trustValue24h":
                        tv_24h,
                    "threshold":
                        PTS_SHOCK_MAX_TRUST,
                    "exchangeCount":
                        len(recent),
                    "rule": f"24h 积分兑换 "
                            f"{tv_24h} 信值"
                            f"(≥"
                            f"{PTS_SHOCK_MAX_TRUST}"
                            f")",
                },
            }
        # ② 持续(连续 3 日每日满频 3 次)
        today = datetime.now(UTC) \
            .strftime("%Y-%m-%d")
        days = {}
        for r in active:
            day = str(
                r.get("createdAt")
                or "")[:10]
            if day:
                days[day] = days.get(
                    day, 0) + 1
        day_keys = sorted(
            days.keys(), reverse=True)
        consec = 0
        for day in day_keys:
            if days[day] >= 3:
                consec += 1
            else:
                break
        if consec \
                >= PTS_SHOCK_CONSEC_DAYS:
            return {
                "detector":
                    "PTS-SHOCK",
                "severity": "medium",
                "entityType": "user",
                "entityId": int(user_id),
                "trustId": trust_id,
                "detail": {
                    "signal":
                        "sustained",
                    "consecutiveDays":
                        consec,
                    "dailyCounts": {
                        k: days[k]
                        for k in day_keys[:3]},
                    "rule": f"连续 {consec}"
                            f" 日每日满频兑换"
                            f"(≥3 次/日)",
                },
            }
        # ③ 探测(当日记录含取消 ≥5 次
        # ——限频 3 次下需反复取消重试)
        today_total = sum(
            1 for r in records
            if str(r.get("createdAt")
                   or "").startswith(
                today))
        if today_total \
                >= PTS_SHOCK_PROBE_ATTEMPTS:
            return {
                "detector":
                    "PTS-SHOCK",
                "severity": "medium",
                "entityType": "user",
                "entityId": int(user_id),
                "trustId": trust_id,
                "detail": {
                    "signal": "probe",
                    "todayAttempts":
                        today_total,
                    "threshold":
                        PTS_SHOCK_PROBE_ATTEMPTS,
                    "rule": f"当日兑换尝试 "
                            f"{today_total} 次"
                            f"(含取消 ≥"
                            f"{PTS_SHOCK_PROBE_ATTEMPTS}"
                            f")",
                },
            }
        return None

    async def detect_price_manip(
            self, product: str = None
    ) -> list:
        """PRICE-MANIP 价格操纵
        (RT-04——近 7 日均价 vs 前
        7 日均价涨幅 >20% 且叠加信值
        支付; 样本 <3 降级不判定)

        Returns:
            finding 列表(商品维——
            指定 product 单查/缺省全量)
        """
        orders = await self.repo.list_orders(
            limit=500)
        paid = [
            o for o in orders
            if o.get("status") in PAID_STATES]
        groups = {}
        for o in paid:
            p = o.get("product") or ""
            groups.setdefault(p, []).append(o)
        targets = ([str(product)]
                   if product else list(groups))
        findings = []
        now = datetime.now(UTC)
        for p in targets:
            if not p or p not in groups:
                continue
            items = groups[p]
            recent, prior = [], []
            for o in items:
                dt = _parse_dt(
                    o.get("paidAt")
                    or o.get("createdAt"))
                if dt is None:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(
                        tzinfo=UTC)
                if now - dt <= timedelta(
                        days=PRICE_MANIP_WINDOW_DAYS):
                    recent.append(o)
                elif timedelta(
                        days=PRICE_MANIP_WINDOW_DAYS) \
                        < now - dt <= timedelta(
                        days=PRICE_MANIP_WINDOW_DAYS * 2):
                    prior.append(o)
            if len(recent) \
                    < PRICE_MANIP_MIN_SAMPLES \
                    or len(prior) \
                    < PRICE_MANIP_MIN_SAMPLES:
                continue  # 冷启动防误报
            avg_recent = round(sum(
                float(o.get("price") or 0)
                for o in recent)
                / len(recent), 2)
            avg_prior = round(sum(
                float(o.get("price") or 0)
                for o in prior)
                / len(prior), 2)
            if avg_prior <= 0:
                continue
            drift = round(
                (avg_recent - avg_prior)
                / avg_prior, 4)
            has_trust = any(
                float(o.get("trustValue")
                      or 0) > 0
                for o in recent)
            if drift > PRICE_MANIP_MAX_DRIFT \
                    and has_trust:
                findings.append({
                    "detector":
                        "PRICE-MANIP",
                    "severity": "medium",
                    "entityType":
                        "product",
                    "entityId": str(p),
                    "detail": {
                        "avgRecent":
                            avg_recent,
                        "avgPrior": avg_prior,
                        "drift": drift,
                        "driftThreshold":
                            PRICE_MANIP_MAX_DRIFT,
                        "trustAttached":
                            has_trust,
                        "samplesRecent":
                            len(recent),
                        "samplesPrior":
                            len(prior),
                        "rule": f"商品 {p} "
                                f"7 日均价 "
                                f"{avg_prior}→"
                                f"{avg_recent}"
                                f"(涨幅 "
                                f"{drift:.0%} >"
                                f"{PRICE_MANIP_MAX_DRIFT:.0%}"
                                f"且叠加信值支付)",
                    },
                })
        return findings

    async def detect_liq_crunch(
            self) -> dict | None:
        """LIQ-CRUNCH 流动性推演
        (全站 24h 消耗线性推演触
        总信值 40%——唯一零拦截
        high: 处置仅建议)"""
        entries = await self.repo.list_ledger(
            limit=500)
        debits = [
            e for e in entries
            if e.get("direction") == "debit"
            and _within_hours(
                e.get("createdAt"), 24)]
        consumed_24h = round(sum(
            abs(float(e.get("amount")
                       or 0))
            for e in debits), 2)
        # 全站信值总量(45号只读——
        # 档案 score 求和)
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        profiles = await (
            TrustValue45Repository()
            .list_profiles(limit=5000))
        total_supply = round(sum(
            float(p.get("score") or 0)
            for p in profiles), 2)
        if total_supply <= 0:
            return None
        projected = round(
            consumed_24h
            / total_supply, 4)
        if projected >= LIQ_CRUNCH_RATIO:
            rate = round(
                consumed_24h / 24, 4)
            return {
                "detector":
                    "LIQ-CRUNCH",
                "severity": "high",
                "entityType": "global",
                "entityId": "site",
                "detail": {
                    "consumed24h":
                        consumed_24h,
                    "totalSupply":
                        total_supply,
                    "projectedRatio":
                        projected,
                    "ratioThreshold":
                        LIQ_CRUNCH_RATIO,
                    "burnRatePerHour":
                        rate,
                    "rule": f"24h 消耗 "
                            f"{consumed_24h}"
                            f"/总信值 "
                            f"{total_supply}"
                            f"={projected:.1%}"
                            f"(≥"
                            f"{LIQ_CRUNCH_RATIO:.0%}"
                            f")",
                },
            }
        return None

    # ============================================================
    # ② 风险事件落库(dedupe 窗口)
    # ============================================================

    async def _save_finding(
            self, finding: dict,
            order_id: int = 0,
            exchange_id: int = 0,
            tier: str = None
    ) -> dict:
        """finding→xx64_risk 落库
        (同窗同实体去重——已有
        open 事件不重复)"""
        detector = finding.get(
            "detector")
        entity_key = str(
            finding.get("entityType")) \
            + ":" + str(
            finding.get("entityId"))
        exists = await self.repo.list_risks(
            limit=200)
        for r in exists:
            if r.get("detectorCode") \
                    == detector \
                    and r.get("status") \
                    == "open" \
                    and str(r.get(
                        "entityKey")) \
                    == entity_key \
                    and _within_hours(
                        r.get("detectedAt"), 24):
                return r  # 同窗去重
        score = risk_score(
            [finding], tier)
        disp = disposition(score)
        risk_id = await \
            self.repo.next_risk_id()
        record = {
            "riskId": risk_id,
            "detectedAt": ts(),
            "detectorCode": detector,
            "entityKey": entity_key,
            "entityType": finding.get(
                "entityType"),
            "entityId": finding.get(
                "entityId"),
            "trustId": int(
                finding.get("trustId") or 0),
            "severity": finding.get(
                "severity"),
            "riskScore": score,
            "matched": True,
            "detail": finding.get(
                "detail") or {},
            "action": disp["action"],
            "suggested": self._suggestion(
                finding, disp),
            "status": "open",
            "orderId": int(order_id or 0),
            "exchangeId": int(
                exchange_id or 0),
        }
        await self.repo.save_risk(record)
        try:
            await self.repo.add_event({
                "eventId": await
                self.repo.next_event_id(),
                "orderId": int(
                    order_id or 0),
                "eventType": "risk",
                "detail": {
                    "riskId": risk_id,
                    "detector": detector,
                    "severity": finding.get(
                        "severity"),
                    "riskScore": score,
                    "action": disp["action"],
                    "entityKey": entity_key,
                },
                "createdAt": ts(),
            })
        except Exception as exc:
            logger.warning(
                "xx64_risk_event_failed: %s",
                exc)
        return record

    @staticmethod
    def _suggestion(finding: dict,
                    disp: dict) -> dict:
        """建议书(惩罚性处置永不
        自动——人工/46号执行)"""
        detector = finding.get("detector")
        base = {
            "executor": "人工/46号审批",
            "autoExecute": False,
        }
        if detector == "ARB-HF":
            base.update({
                "suggestion":
                    "人工关注买家窗口用量"
                    "与 R5 基准快照序列"
                    "(是否拆单压基数)",
                "severity":
                    finding.get("severity"),
            })
        elif detector == "ARB-MA":
            base.update({
                "suggestion":
                    "人工核查命中账号关联性"
                    "(同 IP/同设备属 47号"
                    "职权——64号仅提供清单)",
                "severity":
                    finding.get("severity"),
            })
        elif detector == "PTS-SHOCK":
            base.update({
                "suggestion":
                    "建议 47号 tier 降档联动"
                    "(经 46号 submit——"
                    "不直改 47号)",
                "severity":
                    finding.get("severity"),
            })
        elif detector == "PRICE-MANIP":
            base.update({
                "suggestion":
                    "商品下架建议+告警"
                    "(64号无下架权——"
                    "人工/46号决策)",
                "severity":
                    finding.get("severity"),
            })
        elif detector == "LIQ-CRUNCH":
            base.update({
                "suggestion":
                    "兑换冷却建议(如 24h "
                    "冷却窗)——全局动作"
                    "不可自动, 人工/46号",
                "severity":
                    finding.get("severity"),
            })
        base["disposition"] = disp
        return base

    # ============================================================
    # ③ 触发链路
    # ============================================================

    async def sync_gate_pay(
            self, order: dict
    ) -> dict:
        """支付同步前置三查
        (ARB-HF 买家+ARB-MA 商品
        ——决策面入口轻量)

        assist 态 high→阻断当前笔
        (ValueError 含 riskId);
        shadow 态仅落事件不阻断。

        Returns:
            {blocked, riskId,
             enhancedVerify, findings}
        """
        buyer_id = int(
            order.get("buyerId") or 0)
        trust_id = int(
            order.get("trustId") or 0)
        findings = []
        hf = await self.detect_arb_hf(
            buyer_id, trust_id)
        if hf:
            findings.append(hf)
        ma = await self.detect_arb_ma(
            product=order.get("product"))
        if ma:
            findings.append(ma)
        tier = await self._tier_of(
            trust_id)
        blocked = False
        risk_id = 0
        enhanced = False
        for f in findings:
            record = await \
                self._save_finding(
                    f,
                    order_id=int(
                        order.get("orderId")
                        or 0),
                    tier=tier)
            risk_id = record.get(
                "riskId")
            if f.get("severity") == "high" \
                    and risk_enforce_enabled():
                blocked = True
            elif record.get("action") \
                    == "enhanced_verify":
                enhanced = True
        return {
            "blocked": blocked,
            "riskId": risk_id,
            "enhancedVerify": enhanced,
            "findings": findings,
            "mode": current_mode(),
        }

    async def sync_gate_exchange(
            self, user_id: int,
            trust_id: int
    ) -> dict:
        """积分兑换同步前置查
        (PTS-SHOCK——量级 high
        拦截当笔, assist 态)"""
        findings = []
        shock = await self.detect_pts_shock(
            int(user_id))
        if shock:
            findings.append(shock)
        tier = await self._tier_of(
            int(trust_id))
        blocked = False
        risk_id = 0
        for f in findings:
            record = await \
                self._save_finding(
                    f, tier=tier)
            risk_id = record.get(
                "riskId")
            if f.get("severity") == "high" \
                    and risk_enforce_enabled():
                blocked = True
        return {
            "blocked": blocked,
            "riskId": risk_id,
            "findings": findings,
            "mode": current_mode(),
        }

    async def scan_all(self
                       ) -> dict:
        """手动全量五防扫描
        (POST /risk/scan——管理面)

        只落事件+建议书, 不阻断
        任何已成立交易(预警仅建议)。
        """
        # ① ARB-HF(按买家聚合)
        orders = await self.repo.list_orders(
            limit=200)
        buyers = sorted({
            int(o.get("buyerId") or 0)
            for o in orders
            if o.get("status")
            in PAID_STATES})
        hf_count = 0
        for b in buyers:
            trust_ids = {
                int(o.get("trustId") or 0)
                for o in orders
                if int(o.get("buyerId")
                       or 0) == b}
            f = await self.detect_arb_hf(
                b, next(iter(trust_ids))
                if trust_ids else None)
            if f:
                tier = await self._tier_of(
                    f.get("trustId") or b)
                await self._save_finding(
                    f, tier=tier)
                hf_count += 1
        # ② ARB-MA(按商品聚合+卖方)
        products = sorted({
            str(o.get("product") or "")
            for o in orders
            if o.get("status")
            in PAID_STATES})
        sellers = sorted({
            int(o.get("sellerId") or 0)
            for o in orders
            if o.get("status")
            in PAID_STATES})
        ma_count = 0
        for p in products:
            if not p:
                continue
            f = await self.detect_arb_ma(
                product=p)
            if f:
                await self._save_finding(f)
                ma_count += 1
        for s in sellers:
            if not s:
                continue
            f = await self.detect_arb_ma(
                seller_id=s)
            if f:
                await self._save_finding(f)
                ma_count += 1
        # ③ PTS-SHOCK(按兑换用户)
        exchanges = await \
            self.repo.list_exchanges(
                limit=200)
        exchangers = {}
        for r in exchanges:
            u = int(r.get("buyerId") or 0)
            if u and u not in exchangers:
                exchangers[u] = int(
                    r.get("trustId") or 0)
        pts_count = 0
        for u, tid in sorted(
                exchangers.items()):
            f = await self.detect_pts_shock(u)
            if f:
                tier = await self._tier_of(
                    tid or u)
                await self._save_finding(
                    f, tier=tier)
                pts_count += 1
        # ④ PRICE-MANIP(全量商品)
        price_findings = await \
            self.detect_price_manip()
        for f in price_findings:
            await self._save_finding(f)
        # ⑤ LIQ-CRUNCH(全站)
        liq = await self.detect_liq_crunch()
        liq_count = 0
        if liq:
            await self._save_finding(liq)
            liq_count = 1
        total = (hf_count + ma_count
                 + pts_count
                 + len(price_findings)
                 + liq_count)
        return {
            "success": True,
            "scannedAt": ts(),
            "detectors": {
                "ARB-HF": hf_count,
                "ARB-MA": ma_count,
                "PTS-SHOCK": pts_count,
                "PRICE-MANIP":
                    len(price_findings),
                "LIQ-CRUNCH": liq_count,
            },
            "matched": total,
            "note": "五防扫描——仅落事件"
                    "+建议书, 不阻断已成立"
                    "交易(预警仅建议铁律; "
                    "同窗同实体去重)",
        }

    async def user_status(
            self, trust_id: int
    ) -> dict:
        """用户风险画像(观测面——
        当前风险分+tier 摩擦+命中
        事件+处置状态)"""
        risks = await self.repo.list_risks(
            trust_id=int(trust_id),
            limit=50)
        recent = [
            r for r in risks
            if _within_hours(
                r.get("detectedAt"),
                24 * 30)]
        open_findings = [
            {"detector": r.get(
                "detectorCode"),
             "severity": r.get(
                 "severity")}
            for r in recent
            if r.get("status") == "open"]
        tier = await self._tier_of(
            int(trust_id))
        score = risk_score(
            open_findings, tier)
        disp = disposition(score)
        return {
            "success": True,
            "trustId": int(trust_id),
            "tier": tier,
            "friction":
                TIER_FRICTION.get(
                    tier, 1.0),
            "riskScore": score,
            "disposition": disp,
            "openEvents": len(
                open_findings),
            "events": [
                {"riskId": r.get("riskId"),
                 "detector": r.get(
                     "detectorCode"),
                 "severity": r.get(
                     "severity"),
                 "riskScore": r.get(
                     "riskScore"),
                 "action": r.get(
                     "action"),
                 "status": r.get(
                     "status"),
                 "detectedAt": r.get(
                     "detectedAt")}
                for r in recent[:20]],
            "note": "风险画像——tier 摩擦"
                    "修正(47号纯读取); "
                    "处置建议永不自动执行",
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    @staticmethod
    async def _tier_of(trust_id: int
                       ) -> str:
        """47号 tier 纯读取
        (fail-soft——档案缺失
        回退 standard)"""
        try:
            from services.trust_risk_profile_service import (
                TrustRiskProfileService,
            )
            profile = await (
                TrustRiskProfileService()
                .get_profile(
                    int(trust_id or 0)))
            return profile.get("tier") \
                or "standard"
        except Exception:
            return "standard"
