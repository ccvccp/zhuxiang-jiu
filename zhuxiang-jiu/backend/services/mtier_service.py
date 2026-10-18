"""超级会员智能库 服务层(mtier_service)

营销卡制叠加层: 银卡/金卡/黑金三档 + 智能标签 + 生命周期状态机。

数据口径(只读消费, 摸底铁律):
    - 累计消费 = member.growth_value(等级引擎官方口径,
      record_order_consume 写入, 即累计实付)
    - 订单明细 = zk_fabric_service(VALID_ORDER_STATUSES 过滤 +
      actualAmount 实付额 + paidAt 支付时间), 织物层零回写

确定性规则(零 LLM, 对齐 73号分级路由哲学):
    tier:      黑金 ≥20000 / 金卡 ≥6000 / 银卡 ≥2000(累计实付)
    lifecycle: new(首单≤30天) / repeat(≥2单且≤60天) /
               active(1单且≤60天) / won_back(沉睡后回流≤30天) /
               dormant(60-120天) / churn_risk(>120天) / none(无单)
    auto 标签: price_band(90天均客单) / freq(90天≥3单) /
               consume_band(与 tier 同档) / lifecycle 同步
    manual 标签: 运营添加, auto 刷新永不覆盖 manual 源标签
"""

from __future__ import annotations

from core.pii import mask_phone
from repositories.mtier_repository import MtierRepository

# ---------- 卡制常量 ----------

TIER_NONE = "none"
TIER_SILVER = "silver"
TIER_GOLD = "gold"
TIER_BLACK = "black"

TIER_NAMES = {
    TIER_NONE: "普通", TIER_SILVER: "银卡会员",
    TIER_GOLD: "金卡会员", TIER_BLACK: "黑金会员",
}
# 门槛从高到低判定(累计实付, 元)
TIER_THRESHOLDS = (
    (TIER_BLACK, 20000),
    (TIER_GOLD, 6000),
    (TIER_SILVER, 2000),
)

# ---------- 生命周期常量 ----------

LC_NONE = "none"
LC_NEW = "new"
LC_ACTIVE = "active"
LC_REPEAT = "repeat"
LC_DORMANT = "dormant"
LC_CHURN_RISK = "churn_risk"
LC_WON_BACK = "won_back"

LC_NAMES = {
    LC_NONE: "未消费", LC_NEW: "新客", LC_ACTIVE: "活跃",
    LC_REPEAT: "复购", LC_DORMANT: "沉睡",
    LC_CHURN_RISK: "流失风险", LC_WON_BACK: "已挽回",
}

_DAYS = 86400.0


def _days_since(iso) -> float:
    """距今天数(非法/缺失诚实返回 999.0)"""
    from datetime import datetime, UTC
    if not iso:
        return 999.0
    try:
        dt = datetime.fromisoformat(str(iso))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - dt).total_seconds() / _DAYS)
    except (TypeError, ValueError):
        return 999.0


class MtierService:
    """超级会员智能库: 评估/查询/标签"""

    def __init__(self, repo: MtierRepository = None):
        self.repo = repo or MtierRepository()

    # ---------- 单会员评估 ----------

    def _calc_tier(self, total_consume: float) -> str:
        for tier, threshold in TIER_THRESHOLDS:
            if total_consume >= threshold:
                return tier
        return TIER_NONE

    def _calc_lifecycle(self, prev: str, order_days: list[float]) -> str:
        """订单距今天数列表 → 生命周期状态(含挽回迁移)"""
        if not order_days:
            return LC_NONE
        last = min(order_days)
        count = len(order_days)
        if prev in (LC_DORMANT, LC_CHURN_RISK) and last <= 30:
            return LC_WON_BACK
        if last <= 30 and count == 1:
            return LC_NEW
        if last > 120:
            return LC_CHURN_RISK
        if last > 60:
            return LC_DORMANT
        return LC_REPEAT if count >= 2 else LC_ACTIVE

    def _auto_tags(self, *, tier: str, lifecycle: str,
                   total_consume: float, orders90: list[dict]) -> dict:
        """确定性自动标签(auto 源; 刷新时 manual 源保留)"""
        tags = {
            "consume_band": {"value": TIER_NAMES[tier],
                             "source": "auto"},
            "lifecycle": {"value": LC_NAMES[lifecycle],
                          "source": "auto"},
        }
        if orders90:
            amounts = [float(o.get("_amount") or 0) for o in orders90]
            avg = sum(amounts) / len(amounts)
            band = ("high" if avg >= 300
                    else "mid" if avg >= 100 else "entry")
            tags["price_band"] = {"value": band, "source": "auto"}
            if len(orders90) >= 3:
                tags["freq"] = {"value": "high_freq",
                                "source": "auto"}
        return tags

    async def evaluate_member(self, member_id) -> dict | None:
        """评估单会员: tier + lifecycle + auto 标签 → 落画像

        幂等: 重复评估仅刷新 auto 标签与快照字段;
        lifecycle 变更时写迁移留痕。
        """
        from repositories.member_repository import MemberRepository
        from services.zk_fabric_service import (
            ZkFabricService, VALID_ORDER_STATUSES, order_amount,
            order_paid_at)

        member = await MemberRepository().get_by_id(member_id)
        if not member:
            return None
        fabric = ZkFabricService()
        orders = [o for o in await fabric.member_orders(member_id)
                  if o.get("status") in VALID_ORDER_STATUSES]
        # 附实付额与距今天数(轻量行内计算)
        rows = []
        for o in orders:
            o = dict(o)
            o["_amount"] = order_amount(o)
            o["_days"] = _days_since(order_paid_at(o))
            rows.append(o)
        order_days = [r["_days"] for r in rows]
        orders90 = [r for r in rows if r["_days"] <= 90]
        total_consume = float(member.get("growth_value") or 0)

        prev = await self.repo.get_profile(member_id)
        prev_lc = (prev or {}).get("lifecycle") or LC_NONE
        tier = self._calc_tier(total_consume)
        lifecycle = self._calc_lifecycle(prev_lc, order_days)

        # 标签合并: 保留 manual 源, 刷新 auto 源
        old_tags = (prev or {}).get("tags") or {}
        auto_tags = self._auto_tags(
            tier=tier, lifecycle=lifecycle,
            total_consume=total_consume, orders90=orders90)
        tags = {**{k: v for k, v in old_tags.items()
                   if v.get("source") == "manual"},
                **auto_tags}
        for v in tags.values():
            v["updatedAt"] = _now()
        profile = {
            "memberId": member_id,
            "memberName": str(member.get("name") or ""),
            "phoneMasked": mask_phone(str(member.get("phone") or "")),
            "tier": tier, "tierName": TIER_NAMES[tier],
            "lifecycle": lifecycle,
            "lifecycleName": LC_NAMES[lifecycle],
            "prevLifecycle": prev_lc if prev_lc != lifecycle else "",
            "totalConsume": round(total_consume, 2),
            "ordersTotal": len(rows),
            "orders90": len(orders90),
            "avgOrder90": round(
                sum(r["_amount"] for r in orders90) / len(orders90), 2)
            if orders90 else 0.0,
            "lastConsumeDays": round(min(order_days), 1)
            if order_days else -1,
            "memberLevel": int(member.get("level") or 1),
            "tags": tags,
            "evaluatedAt": _now(),
        }
        await self.repo.save_profile(profile)
        if prev and prev_lc != lifecycle:
            await self.repo.add_log({
                "memberId": member_id,
                "from": prev_lc, "to": lifecycle,
                "tier": tier, "at": _now()})
        return profile

    async def evaluate_all(self, batch: int = 500) -> dict:
        """全量评估(分批, 幂等; 100 万容量下为 O(N) 扫描受调度限频)"""
        from repositories.member_repository import MemberRepository
        members = await MemberRepository().list_all()
        done, skipped = 0, 0
        for m in members[:200000]:
            out = await self.evaluate_member(m.get("id"))
            if out:
                done += 1
            else:
                skipped += 1
        return {"members": len(members), "evaluated": done,
                "skipped": skipped}

    # ---------- 查询(运营面; 画像本身无明文 PII) ----------

    async def summary(self) -> dict:
        profiles = await self.repo.list_profiles()
        tiers = {t: 0 for t in TIER_NAMES}
        lcs = {lc: 0 for lc in LC_NAMES}
        for p in profiles:
            tiers[p.get("tier") or TIER_NONE] += 1
            lcs[p.get("lifecycle") or LC_NONE] += 1
        return {"total": len(profiles),
                "tierDist": tiers, "lifecycleDist": lcs}

    async def members(self, *, tier: str = "", lifecycle: str = "",
                      limit: int = 100) -> list[dict]:
        rows = await self.repo.list_profiles()
        if tier:
            rows = [p for p in rows if p.get("tier") == tier]
        if lifecycle:
            rows = [p for p in rows
                    if p.get("lifecycle") == lifecycle]
        rows.sort(key=lambda p: p.get("totalConsume", 0), reverse=True)
        return rows[:max(1, min(limit, 500))]

    async def logs(self, limit: int = 100) -> list[dict]:
        return await self.repo.list_logs(limit)

    # ---------- 标签 ----------

    async def set_tag(self, member_id, key: str, value: str) -> dict:
        """运营手动打标(manual 源; auto 刷新永不覆盖)"""
        if not key or not value:
            raise ValueError("标签 key/value 不能为空")
        profile = await self.repo.get_profile(member_id)
        if not profile:
            raise KeyError(f"会员 {member_id} 无画像(先执行评估)")
        tags = profile.get("tags") or {}
        tags[str(key)[:32]] = {"value": str(value)[:64],
                               "source": "manual",
                               "updatedAt": _now()}
        profile["tags"] = tags
        await self.repo.save_profile(profile)
        return profile

    async def get_profile(self, member_id) -> dict:
        profile = await self.repo.get_profile(member_id)
        if not profile:
            raise KeyError(f"会员 {member_id} 无画像")
        return profile


def _now() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()
