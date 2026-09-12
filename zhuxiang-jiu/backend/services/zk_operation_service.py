"""智客·AI智能会员大模型 P2 权益运营引擎(zk_operation_service)

「从预测到行动(建议书)」:
    - 权益匹配: L1-L5 差异化权益矩阵 × RFM 分层修正(生日礼/专属
      折扣/免邮阈值/优先购), 输出建议书(永不自动执行)
    - 积分运营: 沉淀余额/获取速率/兑换倾向(订单 usedPoints vs
      consumedPoints 比率)/过期风险(30 天内将过期批次)确定性统计
    - 沉睡唤醒: 健康度活跃维<40 或 60 天无消费 → 分级触达建议书
      (渠道×时机×权益三要素, 分级模板), 永不自动发送

铁律: 全部确定性规则; 建议书 disposition; LLM 禁入; 只读不回写。
"""

import logging

from services.zk_fabric_service import (
    _ZkStore, ZkFabricService, _round2, _now_iso, _days_since, status_of)
from services.zk_insight_service import (
    ZkInsightService, order_paid_of, _months_since)
from services.member_service import LEVEL_NAMES

logger = logging.getLogger(__name__)

# ============================================================
# P2 常量(确定性)
# ============================================================

# L1-L5 差异化权益矩阵(基础档)
LEVEL_BENEFITS = {
    1: ["生日礼: 新人小样酒版", "新人专享券 ¥20",
        "免邮阈值 ¥199"],
    2: ["生日礼: ¥50 无门槛券", "专属折扣 95 折",
        "免邮阈值 ¥99"],
    3: ["生日礼: ¥100 生日券", "专属折扣 92 折",
        "免邮阈值 ¥59", "新品优先购"],
    4: ["生日礼: 精品酒具套装", "专属折扣 88 折",
        "免邮全免", "限量款优先购"],
    5: ["生日礼: 年份珍藏礼盒", "专属折扣 85 折",
        "免邮全免", "限量首发优先购+专属客服"],
}

# RFM 修正追加权益
BENEFIT_OVERRIDES = {
    "重要价值会员": "专属品鉴会邀请(年度)",
    "流失风险会员": "高价值回归礼包(¥100 券+免邮)",
    "重要挽留会员": "90 天回归礼包(¥60 券)",
}

# 唤醒分级模板(渠道×时机×权益三要素, 确定性)
WAKEUP_TIERS = {
    "deep": {
        "tierName": "深度沉睡", "condition": "无消费≥120 天或活跃分<20",
        "channel": "短信+站内信", "timing": "工作日 12:00-13:00",
        "benefit": "大额回归券 ¥50 + 全单免邮",
    },
    "medium": {
        "tierName": "中度沉睡", "condition": "无消费≥90 天",
        "channel": "站内信+短信", "timing": "周末 20:00-21:00",
        "benefit": "专属折扣 9 折 + ¥20 券",
    },
    "light": {
        "tierName": "轻度沉睡", "condition": "活跃分<40 或无消费≥60 天",
        "channel": "站内信", "timing": "晚间 21:00-22:00",
        "benefit": "积分翻倍券 + 满 199-30",
    },
}

# 唤醒判定阈值
WAKEUP_ACTIVITY_LINE = 40     # 健康度活跃维阈值
WAKEUP_DEEP_DAYS = 120
WAKEUP_MEDIUM_DAYS = 90

# 建议书统一口径
DISPOSITION = "策略为建议书; 执行须管理员确认"

# 积分过期风险窗口(天)
POINTS_EXPIRE_WINDOW_DAYS = 30


class ZkOperationService:
    """P2: 权益匹配 + 积分运营 + 沉睡唤醒(建议书引擎)"""

    def __init__(self, fabric: ZkFabricService = None,
                 insight: ZkInsightService = None,
                 store: _ZkStore = None):
        self.fabric = fabric or ZkFabricService()
        self.insight = insight or ZkInsightService(self.fabric)
        self.store = store or self.fabric.store

    # ============================================================
    # 权益匹配(等级 × RFM)
    # ============================================================

    async def benefit_match(self, member_id) -> dict:
        """L1-L5 差异化权益建议(等级基础档 + RFM 修正, 建议书)

        Raises:
            KeyError: 会员不存在
        """
        member = await self.fabric.get_member(member_id)
        portrait = await self.insight.portrait(member_id)
        level = int(member.get("level", 1) or 1)
        label = portrait["segmentLabel"]

        benefits = list(LEVEL_BENEFITS.get(level, LEVEL_BENEFITS[1]))
        override = BENEFIT_OVERRIDES.get(label)
        if override:
            benefits.append(override)
        return {
            "memberId": member_id,
            "nickname": member.get("nickname", ""),
            "level": level,
            "levelName": LEVEL_NAMES.get(level, ""),
            "rfmLabel": label,
            "benefits": benefits,
            "matchRule": (f"等级基础档 L{level} × RFM 分层「{label}」"
                          f"修正{'(追加 ' + override + ')'
                          if override else '(无追加)'}(确定性规则表)"),
            "disposition": DISPOSITION,
            "matchedAt": _now_iso(),
        }

    # ============================================================
    # 积分运营分析(确定性统计)
    # ============================================================

    async def points_analysis(self, member_id=None) -> dict:
        """积分运营分析(单会员/全量)

        口径:
            - 沉淀余额: 积分模块账户 totalPoints(无账户回退档案 points)
            - 获取速率: totalEarned / 注册月数(竹叶/月)
            - 兑换倾向: 订单 usedPoints / (usedPoints + consumedPoints)
              (抵扣占比, 越高兑换意愿越强)
            - 过期风险: 30 天内将过期批次余额(只读提示)
        """
        if member_id is not None:
            return await self._points_of_member(member_id)
        members = await self.fabric.members()
        orders = await self.fabric.all_orders()
        used_total = sum(int(o.get("usedPoints", 0) or 0) for o in orders)
        earned_total = sum(int(o.get("consumedPoints", 0) or 0)
                           for o in orders)
        legacy_total = sum(int(m.get("points", 0) or 0) for m in members)
        redeem_ratio = (used_total / (used_total + earned_total)
                        if (used_total + earned_total) > 0 else 0.0)
        risky = 0
        for m in members:
            expiring = await self._expiring_points(m["id"])
            if expiring > 0:
                risky += 1
        return {
            "scope": "all",
            "memberTotal": len(members),
            "legacyPointsTotal": legacy_total,
            "orderUsedPointsTotal": used_total,
            "orderConsumedPointsTotal": earned_total,
            "redeemTendency": _round2(redeem_ratio),
            "membersWithExpiringRisk": risky,
            "suggestion": (f"全量会员兑换倾向 {_round2(redeem_ratio * 100)}%; "
                           f"{risky} 名会员 30 天内有积分到期, "
                           "建议定向推送兑换提醒(须管理员确认后执行)"),
            "disposition": DISPOSITION,
            "analyzedAt": _now_iso(),
        }

    async def _points_of_member(self, member_id) -> dict:
        """单会员积分运营分析(只读)"""
        member = await self.fabric.get_member(member_id)
        account = await self._points_account(member_id)
        orders = await self.fabric.member_orders(member_id)
        used = sum(int(o.get("usedPoints", 0) or 0) for o in orders)
        earned = sum(int(o.get("consumedPoints", 0) or 0) for o in orders)
        months = _months_since(member.get("created_at", ""))

        # 沉淀余额: 有积分账户走账本, 无账户回退档案遗留字段
        if account:
            balance = int(account.get("totalPoints", 0) or 0)
            total_earned = int(account.get("totalEarned", 0) or 0)
        else:
            balance = int(member.get("points", 0) or 0)
            total_earned = earned
        earn_rate = total_earned / months
        denom = used + earned
        redeem = (used / denom) if denom > 0 else 0.0
        expiring = await self._expiring_points(member_id)
        risk_note = (f"{expiring} 竹叶 30 天内到期, 建议优先引导兑换"
                     if expiring > 0 else "近 30 天无到期批次")
        return {
            "scope": "member",
            "memberId": member_id,
            "nickname": member.get("nickname", ""),
            "depositBalance": balance,
            "totalEarned": total_earned,
            "earnRatePerMonth": _round2(earn_rate),
            "orderUsedPoints": used,
            "orderConsumedPoints": earned,
            "redeemTendency": _round2(redeem),
            "expiringSoon": expiring,
            "expiryRiskNote": risk_note,
            "suggestion": ("兑换倾向高: 推送积分专兑专区"
                           if redeem >= 0.5 else
                           "兑换倾向低: 建议满减+积分抵扣组合引导"
                           if denom > 0 else "暂无积分行为: 注册/签到引导"),
            "disposition": DISPOSITION,
            "analyzedAt": _now_iso(),
        }

    @staticmethod
    async def _points_account(member_id) -> dict | None:
        """只读积分账户(不存在返回 None, 不创建——零回写铁律)"""
        from repositories.points_repository import PointsRepository
        return await PointsRepository().get_account(member_id)

    @staticmethod
    async def _expiring_points(member_id) -> int:
        """30 天内将过期积分(只读)"""
        from repositories.points_repository import PointsRepository
        batches = await PointsRepository().list_expiring_soon(
            member_id, days=POINTS_EXPIRE_WINDOW_DAYS)
        return sum(int(b.get("points", 0) or 0)
                   - int(b.get("consumedPoints", 0) or 0) for b in batches)

    # ============================================================
    # 沉睡唤醒建议(分级模板, 永不自动发送)
    # ============================================================

    async def wakeup_suggest(self, level: int = None) -> dict:
        """沉睡会员分级触达建议书

        沉睡判定(确定性): 健康度活跃维 < 40 或 60 天无有效消费;
        分级: 深度(≥120 天或活跃<20) / 中度(≥90 天) / 轻度(其余)。
        """
        members = await self.fabric.members()
        suggestions = []
        for m in members:
            # 禁用账号不触达(合规口径, 确定性跳过)
            if status_of(m) == 0:
                continue
            if level is not None and int(m.get("level", 1) or 1) != int(level):
                continue
            suggestion = await self._wakeup_of_member(m)
            if suggestion:
                suggestions.append(suggestion)
        tier_counts = {"deep": 0, "medium": 0, "light": 0}
        for s in suggestions:
            tier_counts[s["tier"]] += 1
        return {
            "sleepingTotal": len(suggestions),
            "tierCounts": tier_counts,
            "suggestions": suggestions,
            "formula": ("沉睡 = 活跃维<40 或 60 天无有效消费; "
                        "深度=无消费≥120 天或活跃<20, 中度=≥90 天, "
                        "轻度=其余(确定性阈值)"),
            "disposition": ("建议书; 触达永不自动发送, 须管理员确认后"
                            "由消息模块执行"),
            "generatedAt": _now_iso(),
        }

    async def _wakeup_of_member(self, member: dict) -> dict | None:
        """单会员唤醒建议(非沉睡返回 None)"""
        mid = member["id"]
        health = await self.insight.health(mid)
        activity = self.insight.activity_score_of(health)
        orders = self.fabric.valid_orders(
            await self.fabric.member_orders(mid))
        last_paid = max((order_paid_of(o) for o in orders), default="")
        sleep_days = _round2(_days_since(last_paid)) if last_paid else 999.0

        sleeping = activity < WAKEUP_ACTIVITY_LINE or sleep_days >= 60
        if not sleeping:
            return None
        if sleep_days >= WAKEUP_DEEP_DAYS or activity < 20:
            tier = "deep"
        elif sleep_days >= WAKEUP_MEDIUM_DAYS:
            tier = "medium"
        else:
            tier = "light"
        template = WAKEUP_TIERS[tier]
        record = {
            "suggestionId": await self.store.next_id("zk_wakeups"),
            "memberId": mid,
            "nickname": member.get("nickname", ""),
            "level": int(member.get("level", 1) or 1),
            "levelName": LEVEL_NAMES.get(int(member.get("level", 1) or 1),
                                         ""),
            "sleepDays": sleep_days,
            "activityScore": activity,
            "tier": tier,
            "tierName": template["tierName"],
            "channel": template["channel"],
            "timing": template["timing"],
            "benefit": template["benefit"],
            "reason": (f"活跃分 {activity}(<{WAKEUP_ACTIVITY_LINE}) / "
                       f"距上次消费 {sleep_days} 天; 分级依据: "
                       f"{template['condition']}"),
            "disposition": "建议书; 永不自动发送, 须管理员确认",
            "generatedAt": _now_iso(),
        }
        await self.store.save("zk_wakeups", record["suggestionId"], record)
        return record

    async def wakeups(self, limit: int = 50) -> list[dict]:
        """已生成唤醒建议书列表(按建议书 ID 倒序=最近优先)"""
        rows = await self.store.list("zk_wakeups")
        rows.sort(key=lambda r: int(r.get("suggestionId", 0)), reverse=True)
        return rows[:max(1, int(limit))]
