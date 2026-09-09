"""67号·AI智能叫帮模块业务逻辑层

核心定位: 信值驱动的社会互助网络(公益优先/有偿为辅/零佣金)

确定性铁律(LLM 禁入判定链, 全规则可解释):
    - 需求类型识别: 关键词规则表(非 LLM)
    - 公益信值 = 类型基准 × max(1, ceil(时长/60min))   —— 100% 记录
    - 有偿信值 = 公益同口径 × 0.10                      —— 比公益低 90%
      (比例常量 PAID_TRUST_RATIO, 宪法级口径不可配置篡改)
    - 接单信值门槛: 公益单 ≥20 / 有偿单 ≥50(防失信者参与)
    - 在忙不可接(进行中单 ≥1)
    - 恶意/差评(≤2星)经确定性核实 → 扣帮助者 5 信值
    - 接单后无故取消 → 扣 2 信值(免责: 服务开始前发布者取消不扣)
    - 平台零佣金: 有偿价格双方协商, 平台仅确认与留痕

安全预检(确定性违禁词表): 代考/代写/涉黄/赌博等 → 拒绝发布。
"""

import math

from core.locks import get_lock
from repositories.help_repository import HelpRepository
from repositories.location_repository import haversine_km


def _now_iso() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()


# ============================================================
# 确定性规则常量(宪法级口径)
# ============================================================

# 类型字典: code → (名称, 公益信值基准/小时, 关键词表)
CATEGORIES = {
    "repair":  {"name": "维修互助", "trustBase": 10,
                "keywords": ["灯", "水管", "修", "家电", "电闸", "漏水", "换灯"]},
    "escort":  {"name": "陪护出行", "trustBase": 8,
                "keywords": ["陪", "接送", "医院", "买药", "挂号", "腿脚"]},
    "carry":   {"name": "搬运帮手", "trustBase": 8,
                "keywords": ["搬", "扛", "提", "运", "行李"]},
    "care":    {"name": "照看陪伴", "trustBase": 12,
                "keywords": ["老人", "孩子", "照看", "照顾", "陪聊", "残障"]},
    "teach":   {"name": "技能传授", "trustBase": 10,
                "keywords": ["教", "辅导", "功课", "琴", "字", "培训"]},
    "other":   {"name": "其他求助", "trustBase": 6,
                "keywords": []},
}

# 公益识别关键词(命中 → 默认推荐公益模式 + 优先响应提示)
PUBLIC_HINT_KEYWORDS = ["孤寡", "老人", "残障", "紧急", "独居", "困难", "公益", "免费"]

# 有偿场景关键词(命中 → 推荐有偿模式, 提示信值按 10% 记录)
PAID_HINT_KEYWORDS = ["搬运公司", "专业", "设计", "商业", "批量", "收费", "报酬"]

# 违禁词(安全预检, 命中拒发)
BANNED_KEYWORDS = [
    "代考", "代写论文", "代写作业", "涉黄", "色情", "招嫖", "陪聊涉黄",
    "赌博", "博彩", "洗钱", "代开发票", "刷单", "办证", "枪", "毒品",
]

# 有偿信值记录比(比公益低 90%; 宪法级口径, 禁止任何手段篡改)
PAID_TRUST_RATIO = 0.10

# 接单信值门槛(公益优先理念: 公益零门槛人人可助人, 是信值积累入口;
# 有偿涉及金钱协商 → 需信值 ≥20 解锁, 防失信者参与)
PUBLIC_ACCEPT_TRUST_GATE = 0
PAID_ACCEPT_TRUST_GATE = 20

# 恶意履约惩罚(确定性): 差评扣帮助者 / 接单后取消扣
BAD_REVIEW_PENALTY = 5
CANCEL_AFTER_ACCEPT_PENALTY = 2

# 大厅默认半径(km)
HALL_RADIUS_KM = 20.0

# 订单状态机
STATUS_PUBLISHED = "published"      # 已发布(大厅可见)
STATUS_MATCHED = "matched"          # 已接单(待开始)
STATUS_IN_PROGRESS = "in_progress" # 服务中
STATUS_COMPLETED = "completed"      # 已完成(已结算)
STATUS_CANCELLED = "cancelled"      # 已取消

STATUS_FLOW = {
    STATUS_PUBLISHED: {STATUS_MATCHED, STATUS_CANCELLED},
    STATUS_MATCHED: {STATUS_IN_PROGRESS, STATUS_CANCELLED},
    STATUS_IN_PROGRESS: {STATUS_COMPLETED},
    STATUS_COMPLETED: set(),
    STATUS_CANCELLED: set(),
}

STATUS_NAMES = {
    STATUS_PUBLISHED: "待接单",
    STATUS_MATCHED: "已接单",
    STATUS_IN_PROGRESS: "服务中",
    STATUS_COMPLETED: "已完成",
    STATUS_CANCELLED: "已取消",
}

# 进行中状态(在忙判定)
ACTIVE_STATUSES = (STATUS_MATCHED, STATUS_IN_PROGRESS)


class HelpService:
    """AI智能叫帮服务(67号·P0 基础互助全链)"""

    def __init__(self, repo: HelpRepository = None):
        self.repo = repo or HelpRepository()

    # ============================================================
    # 1. 智能需求解析(确定性规则引擎)
    # ============================================================

    @staticmethod
    def parse_demand(title: str, description: str) -> dict:
        """需求解析: 类型识别 + 模式推荐 + 安全预检(全关键词规则)

        Returns:
            {category, suggestedMode, publicFirst, hint, banned}
        """
        text = f"{title} {description}"
        # 安全预检(违禁词 → 拒发)
        banned = next((w for w in BANNED_KEYWORDS if w in text), None)
        # 类型识别(关键词命中计数, 取最高; 无命中 → other)
        best_cat, best_hits = "other", 0
        for code, meta in CATEGORIES.items():
            hits = sum(1 for kw in meta["keywords"] if kw in text)
            if hits > best_hits:
                best_cat, best_hits = code, hits
        # 模式推荐(公益优先原则)
        public_first = any(kw in text for kw in PUBLIC_HINT_KEYWORDS)
        paid_hint = any(kw in text for kw in PAID_HINT_KEYWORDS)
        if public_first:
            suggested_mode, hint = "public", "检测到公益求助特征, 默认公益模式——公益信值 100% 记录, 可享社区优先响应"
        elif paid_hint:
            suggested_mode, hint = "paid", "检测到专业服务场景, 推荐有偿模式——双方协商价格, 信值按公益标准 10% 记录, 平台零佣金"
        else:
            suggested_mode, hint = "public", "默认公益模式——互助更光荣, 公益信值 100% 记录; 可切换有偿(信值 10%)"
        return {
            "category": best_cat,
            "suggestedMode": suggested_mode,
            "publicFirst": public_first,
            "hint": hint,
            "banned": banned,
        }

    # ============================================================
    # 2. 发布求助
    # ============================================================

    async def publish(self, publisher_id: int, mode: str, category: str,
                      title: str, description: str,
                      longitude: float, latitude: float, address: str,
                      duration_minutes: int = 60, price: float = 0.0,
                      urgency: str = "normal") -> dict:
        """发布叫帮单(安全预检 + 信值预计算)

        Raises:
            ValueError: 违禁词/参数非法/在途单超限
        """
        if mode not in ("public", "paid"):
            raise ValueError(f"模式非法({mode}, 须为 public/paid)")
        if category not in CATEGORIES:
            raise ValueError(f"类型非法({category})")
        if not (title or "").strip():
            raise ValueError("标题不能为空")
        if duration_minutes < 10 or duration_minutes > 24 * 60:
            raise ValueError("预计时长须为 10 分钟 ~ 24 小时")
        if mode == "paid" and price <= 0:
            raise ValueError("有偿模式须填写协商价格(>0)")
        if urgency not in ("normal", "urgent"):
            raise ValueError("紧急度非法(normal/urgent)")

        # 安全预检
        banned = next((w for w in BANNED_KEYWORDS
                       if w in f"{title} {description}"), None)
        if banned:
            raise ValueError(f"需求含违禁内容({banned}), 不可发布")

        # 在途单限制: 每人发布中(待接单)最多 3 单
        publishing = await self.repo.list_orders(
            publisher_id=publisher_id, status=STATUS_PUBLISHED, limit=100)
        if len(publishing) >= 3:
            raise ValueError("待接单求助已达上限(3 单), 请等待响应后再发布")

        # 信值预计算(确定性: 类型基准 × 时长系数; 有偿 ×10%)
        trust_reward = self.calc_trust(category, duration_minutes, mode)

        order_id = await self.repo.next_id("order")
        order = {
            "orderId": order_id,
            "publisherId": publisher_id,
            "mode": mode,
            "category": category,
            "title": title.strip()[:60],
            "description": (description or "").strip()[:500],
            "longitude": float(longitude or 0),
            "latitude": float(latitude or 0),
            "address": (address or "").strip()[:100],
            "durationMinutes": int(duration_minutes),
            "price": round(float(price or 0), 2),
            "urgency": urgency,
            "trustValueReward": trust_reward,
            "status": STATUS_PUBLISHED,
            "helperId": None,
            "startedAt": "",
            "completedAt": "",
            "cancelReason": "",
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        return await self.repo.save_order(order)

    @staticmethod
    def calc_trust(category: str, duration_minutes: int, mode: str) -> float:
        """信值计算(确定性公式, 宪法级口径)

        公益 = 类型基准 × max(1, ceil(分钟/60))
        有偿 = 公益同口径 × 0.10(比公益低 90%)
        """
        base = CATEGORIES.get(category, CATEGORIES["other"])["trustBase"]
        hours = max(1, math.ceil(duration_minutes / 60))
        public_trust = base * hours
        if mode == "paid":
            return round(public_trust * PAID_TRUST_RATIO, 2)
        return round(float(public_trust), 2)

    # ============================================================
    # 3. 互助大厅(LBS + 紧急/距离排序)
    # ============================================================

    async def hall(self, longitude: float, latitude: float,
                   mode: str = None, category: str = None,
                   radius_km: float = HALL_RADIUS_KM,
                   limit: int = 50) -> list[dict]:
        """大厅(公开; 紧急优先 → 距离近优先 → 新单优先)"""
        orders = await self.repo.list_orders(
            status=STATUS_PUBLISHED, mode=mode, category=category,
            limit=500)
        out = []
        for o in orders:
            distance = haversine_km(longitude, latitude,
                                    o.get("longitude", 0),
                                    o.get("latitude", 0))
            if distance > radius_km:
                continue
            item = dict(o)
            item["distanceKm"] = round(distance, 2)
            out.append(item)
        # 排序(Python sort 稳定): 先新单优先 → 再紧急优先 → 距离近优先
        # (后两级 key 相同时保持新单在前)
        out.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        out.sort(key=lambda o: (
            0 if o.get("urgency") == "urgent" else 1,   # 紧急优先
            o.get("distanceKm", 0),                      # 距离近优先
        ))
        return out[:limit]

    async def get_order(self, order_id: int) -> dict:
        order = await self.repo.get_order(order_id)
        if order is None:
            raise KeyError(f"叫帮单不存在(orderId={order_id})")
        order = dict(order)
        order["statusName"] = STATUS_NAMES.get(order.get("status"), "")
        order["categoryName"] = CATEGORIES.get(
            order.get("category"), CATEGORIES["other"])["name"]
        return order

    # ============================================================
    # 4. 接单(信值门槛 + 在忙校验)
    # ============================================================

    async def accept(self, order_id: int, helper_id: int) -> dict:
        """帮助者接单(published → matched)

        门槛: 公益单累计信值 ≥20 / 有偿单 ≥50; 进行中单不可叠加。

        Raises:
            KeyError: 单不存在 / ValueError: 状态/门槛/在忙
        """
        async with get_lock(f"help:order:{order_id}"):
            order = await self.repo.get_order(order_id)
            if order is None:
                raise KeyError(f"叫帮单不存在(orderId={order_id})")
            if order["status"] != STATUS_PUBLISHED:
                raise ValueError(f"单状态非法({STATUS_NAMES.get(order['status'])}, 不可接)")
            if order.get("publisherId") == helper_id:
                raise ValueError("不能接自己发布的求助")

            # 在忙校验
            active = await self.repo.list_orders(
                helper_id=helper_id, limit=200)
            busy = [o for o in active
                    if o.get("status") in ACTIVE_STATUSES
                    and o.get("helperId") == helper_id]
            if busy:
                raise ValueError("您有进行中的互助(最多并行 1 单), 完成后再接")

            # 信值门槛校验(公益零门槛; 有偿单需累计信值 ≥20)
            if order["mode"] == "paid":
                summary = await self.repo.trust_summary(helper_id)
                if summary["totalTrust"] < PAID_ACCEPT_TRUST_GATE:
                    raise ValueError(
                        f"有偿互助需累计信值 ≥{PAID_ACCEPT_TRUST_GATE}"
                        f"(当前 {summary['totalTrust']}); "
                        "先从公益互助积累信值, 解锁有偿接单资格")

            order.update({"status": STATUS_MATCHED,
                         "helperId": helper_id,
                         "updatedAt": _now_iso()})
            return await self.repo.save_order(order)

    # ============================================================
    # 5. 履约流转
    # ============================================================

    async def start(self, order_id: int, operator_id: int) -> dict:
        """开始服务(matched → in_progress; 帮助者本人)"""
        order = await self._require(order_id)
        if order.get("helperId") != operator_id:
            raise ValueError("仅接单帮助者可开始服务")
        if order["status"] != STATUS_MATCHED:
            raise ValueError(f"单状态非法({STATUS_NAMES.get(order['status'])})")
        order.update({"status": STATUS_IN_PROGRESS,
                      "startedAt": _now_iso(),
                      "updatedAt": _now_iso()})
        return await self.repo.save_order(order)

    async def complete(self, order_id: int, operator_id: int) -> dict:
        """完成服务(in_progress → completed; 双方均可确认)

        结算(确定性):
            - 帮助者 + trustValueReward(公益 100% / 有偿 10% 已预计算)
            - 公益单发布者 +1 感谢信值(受助感恩记录)
            - 信值流水落 help_trust_ledger(双轨 track)
        """
        async with get_lock(f"help:order:{order_id}"):
            order = await self._require(order_id)
            if order["status"] != STATUS_IN_PROGRESS:
                raise ValueError(f"单状态非法({STATUS_NAMES.get(order['status'])})")
            if operator_id not in (order.get("publisherId"),
                                   order.get("helperId")):
                raise ValueError("仅互助双方可确认完成")

            now = _now_iso()
            order.update({"status": STATUS_COMPLETED,
                          "completedAt": now,
                          "updatedAt": now})
            await self.repo.save_order(order)

            # 信值结算(锁内幂等: completed 一次性)
            ledger_ids = []
            reward = float(order.get("trustValueReward", 0))
            helper_id = order.get("helperId")
            ledger_id = await self.repo.next_id("ledger")
            await self.repo.save_ledger({
                "ledgerId": ledger_id,
                "memberId": helper_id,
                "orderId": order_id,
                "track": order["mode"],           # public / paid 双轨
                "delta": reward,
                "reason": (f"{'公益' if order['mode'] == 'public' else '有偿'}"
                           f"互助完成: {order['title'][:20]}"
                           f"(时长 {order['durationMinutes']} 分钟)"),
                "createdAt": now,
            })
            ledger_ids.append(ledger_id)
            # 公益单发布者 +1 感谢信值
            thanks_id = None
            if order["mode"] == "public":
                thanks_id = await self.repo.next_id("ledger")
                await self.repo.save_ledger({
                    "ledgerId": thanks_id,
                    "memberId": order.get("publisherId"),
                    "orderId": order_id,
                    "track": "public",
                    "delta": 1,
                    "reason": f"受助感恩记录: {order['title'][:20]}",
                    "createdAt": now,
                })
            return {
                "order": order,
                "settlement": {
                    "helperTrustDelta": reward,
                    "publisherThanks": 1 if order["mode"] == "public" else 0,
                    "ledgerIds": ledger_ids,
                    "thanksLedgerId": thanks_id,
                },
            }

    async def cancel(self, order_id: int, operator_id: int,
                     reason: str = "") -> dict:
        """取消(published/matched 可取消; 接单后取消扣帮助者 2 信值)"""
        async with get_lock(f"help:order:{order_id}"):
            order = await self._require(order_id)
            if order["status"] not in (STATUS_PUBLISHED, STATUS_MATCHED):
                raise ValueError(f"单状态非法({STATUS_NAMES.get(order['status'])}, 不可取消)")
            is_publisher = operator_id == order.get("publisherId")
            is_helper = operator_id == order.get("helperId")
            if not (is_publisher or is_helper):
                raise ValueError("仅互助双方可取消")

            penalty = 0
            # 接单后帮助者取消 → 扣 2 信值(确定性惩罚)
            if order["status"] == STATUS_MATCHED and is_helper:
                penalty = CANCEL_AFTER_ACCEPT_PENALTY
                ledger_id = await self.repo.next_id("ledger")
                await self.repo.save_ledger({
                    "ledgerId": ledger_id,
                    "memberId": operator_id,
                    "orderId": order_id,
                    "track": order["mode"],
                    "delta": -penalty,
                    "reason": "接单后无故取消(惩罚性扣减)",
                    "createdAt": _now_iso(),
                })
            order.update({"status": STATUS_CANCELLED,
                          "cancelReason": reason or "",
                          "updatedAt": _now_iso()})
            await self.repo.save_order(order)
            return {"order": order, "trustPenalty": penalty}

    # ============================================================
    # 6. 双向评价(差评确定性扣分)
    # ============================================================

    async def review(self, order_id: int, reviewer_id: int,
                      score: int, content: str = "") -> dict:
        """双向评价(完成后一单一评; 1-5 星)

        规则(确定性):
            - 发布者评帮助者: ≤2 星 → 扣帮助者 5 信值(经核实口径)
            - 帮助者评发布者: 仅记录(信值约束主向为帮助质量)
        """
        if not 1 <= score <= 5:
            raise ValueError("评分须为 1-5 星")
        order = await self._require(order_id)
        if order["status"] != STATUS_COMPLETED:
            raise ValueError("互助完成后方可评价")
        is_publisher = reviewer_id == order.get("publisherId")
        is_helper = reviewer_id == order.get("helperId")
        if not (is_publisher or is_helper):
            raise ValueError("仅互助双方可评价")
        reviewee_id = (order.get("helperId") if is_publisher
                       else order.get("publisherId"))
        # 一单一评(同向幂等)
        existing = await self.repo.list_reviews(order_id=order_id, limit=10)
        if any(r.get("reviewerId") == reviewer_id for r in existing):
            raise ValueError("该方向已评价(一单一评)")

        review_id = await self.repo.next_id("review")
        review = {
            "reviewId": review_id,
            "orderId": order_id,
            "reviewerId": reviewer_id,
            "revieweeId": reviewee_id,
            "direction": "publisher_to_helper" if is_publisher
                         else "helper_to_publisher",
            "score": int(score),
            "content": (content or "").strip()[:200],
            "createdAt": _now_iso(),
        }
        await self.repo.save_review(review)

        # 差评惩罚(确定性): 发布者给帮助者 ≤2 星 → 扣 5 信值
        penalty = 0
        if is_publisher and score <= 2:
            penalty = BAD_REVIEW_PENALTY
            ledger_id = await self.repo.next_id("ledger")
            await self.repo.save_ledger({
                "ledgerId": ledger_id,
                "memberId": order.get("helperId"),
                "orderId": order_id,
                "track": order["mode"],
                "delta": -penalty,
                "reason": f"服务差评({score} 星, 经核实口径扣减)",
                "createdAt": _now_iso(),
            })
        return {**review, "trustPenalty": penalty}

    # ============================================================
    # 7. 我的互助与信值档案
    # ============================================================

    async def my_published(self, member_id: int) -> list[dict]:
        orders = await self.repo.list_orders(
            publisher_id=member_id, limit=100)
        return [self._decorate(o) for o in orders]

    async def my_helped(self, member_id: int) -> list[dict]:
        orders = await self.repo.list_orders(
            helper_id=member_id, limit=100)
        return [self._decorate(o) for o in orders]

    async def trust_profile(self, member_id: int) -> dict:
        """信值档案(公益/有偿双轨累计 + 流水)"""
        summary = await self.repo.trust_summary(member_id)
        entries = await self.repo.list_ledger(member_id, limit=50)
        # 被评价均分(帮助质量)
        reviews = await self.repo.list_reviews(
            reviewee_id=member_id, limit=200)
        ratings = [r["score"] for r in reviews]
        summary.update({
            "ratingAvg": round(sum(ratings) / len(ratings), 1) if ratings else None,
            "ratingCount": len(ratings),
            "ledger": entries,
            "gates": {
                "public": PUBLIC_ACCEPT_TRUST_GATE,
                "paid": PAID_ACCEPT_TRUST_GATE,
            },
            "paidRatio": PAID_TRUST_RATIO,
        })
        return summary

    async def categories(self) -> list[dict]:
        """类型字典(公开; 含公益信值基准)"""
        return [
            {"code": code, "name": meta["name"],
             "trustBase": meta["trustBase"]}
            for code, meta in CATEGORIES.items()
        ]

    # ============================================================
    # 辅助
    # ============================================================

    async def _require(self, order_id: int) -> dict:
        order = await self.repo.get_order(order_id)
        if order is None:
            raise KeyError(f"叫帮单不存在(orderId={order_id})")
        return order

    @staticmethod
    def _decorate(order: dict) -> dict:
        o = dict(order)
        o["statusName"] = STATUS_NAMES.get(o.get("status"), "")
        o["categoryName"] = CATEGORIES.get(
            o.get("category"), CATEGORIES["other"])["name"]
        return o
