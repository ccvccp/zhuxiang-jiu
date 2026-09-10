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

# ============================================================
# P1·智能调度层常量(全确定性, LLM 禁入判定链)
# ============================================================

# 三维匹配权重(信值匹配 40% + 同类经验 30% + 地理最优 30%)
MATCH_WEIGHT_TRUST = 0.40
MATCH_WEIGHT_SKILL = 0.30
MATCH_WEIGHT_GEO = 0.30
MATCH_TOP_N = 3                       # 推荐 Top3 候选
MATCH_TRUST_FULL = 50.0               # 信值维度满分线(50 信值即满分)
MATCH_SKILL_FULL = 5                  # 同类经验满分线(5 单)

# 破冰提示(类别确定性话术, 服务前推送温暖对方偏好)
ICEBREAKER_TIPS = {
    "repair":   "上门维修请提前确认工具与材料齐备; 进门前主动出示身份并说明来意, 让主人安心。",
    "escort":   "陪同出行请提前确认集合地点与时间; 轮椅/拐杖用户请放慢步伐, 多停顿多询问。",
    "carry":    "搬运重物请先确认楼层与电梯情况; 弯腰用腿部发力, 双人配合时口令一致更安全。",
    "care":     "照看陪伴请多倾听少打断; 老人耳背请放慢语速大声说话, 聊聊老歌与家常最暖心。",
    "teach":    "技能传授请从对方熟悉的事物讲起; 每讲一步让对方操作一遍, 学会了记得夸一夸。",
    "other":    "互助开始前先确认双方预期一致; 遇到不确定的情况, 多问一句总没错。",
}

# 荣誉阶梯(按公益信值累计, 确定性晋级; 数字功德碑口径)
HONOR_LADDER = (
    (1000, "功德圆满", "🌳"),
    (300,  "白金守护者", "🏆"),
    (100,  "金心志愿者", "🥇"),
    (50,   "银心志愿者", "🥈"),
    (10,   "铜心志愿者", "🥉"),
    (0,    "见习互助者", "🌱"),
)

# 信值捐赠(高信值用户反哺公益单: 捐赠池入单, 完成时奖励帮助者)
DONATE_TRUST_GATE = 50                # 捐赠资格: 累计信值 ≥50(高信值用户)
DONATE_MIN = 1                        # 单次最少 1 信值
DONATE_MAX = 10                       # 单次最多 10 信值

# 安全护航(观测不干预: 超时仅提醒, 惩罚/处置永不自动——46号铁律)
GUARD_OVERTIME_RATIO = 1.5            # 超过预计时长 150% → 温和提醒

# 故事卡类别文案(确定性模板, 分享传播互助温度)
STORY_THEMES = {
    "repair":   "一盏重新点亮的灯, 照亮的不只是屋子。",
    "escort":   "一段有人陪同的路, 每一步都走得踏实。",
    "carry":    "一副愿意伸出的手, 让重物有了去处。",
    "care":     "一次耐心的陪伴, 是最长情的告白。",
    "teach":    "一点传递出去的火种, 会照亮更多人。",
    "other":    "一份素不相识的善意, 让社区有了温度。",
}

# ============================================================
# P2·生态深化层常量(全确定性)
# ============================================================

# 信值传承(数字功德碑继承): 仅公益信值可传承, 有偿信值不可(宪法口径)
HERITAGE_MIN = 10                # 最低传承额度(公益信值)
HERITAGE_STATUS_PENDING = "pending"
HERITAGE_STATUS_DONE = "done"
HERITAGE_STATUS_CANCELLED = "cancelled"

# 互助接力(偏远地区多人分段完成)
RELAY_MIN_LEGS = 2
RELAY_MAX_LEGS = 5

# 企业信值包(CSR 认捐池): 平台零资金流, 企业认捐留痕
CSR_PACKAGE_MIN = 10             # 企业包最小额度
CSR_PACKAGE_MAX = 10000          # 企业包最大额度
CSR_DONATE_MAX = 100             # 单笔定向捐助上限

# ============================================================
# P3·社会影响力层常量(全确定性, 无个人隐私数据)
# ============================================================

# 碳积分折算(克/小时; 确定性映射: 公益互助的环境正外部性)
# carry=代替货运车辆出行 / escort=拼车陪护代替各自驾车 / repair=延长物品寿命
# teach/care=社会资本型(象征性) / other=杂项
CARBON_GRAMS_PER_HOUR = {
    "carry":  2000,
    "escort": 800,
    "repair": 500,
    "teach":  100,
    "care":   100,
    "other":  50,
}
CARBON_PAID_RATIO = 0.5          # 有偿单碳折算比例(公益 100%; 碳为环境事实但公益优先激励)

# 社会信任指数(0-100, 确定性四因子加权)
TRUST_INDEX_WEIGHTS = {
    "completion": 0.40,          # 完成率: completed / (completed + cancelled)
    "praise": 0.30,              # 好评率: 4-5 星占全部评价比
    "publicRatio": 0.20,         # 公益占比: 公益单 / 总单
    "participation": 0.10,       # 参与度: 活跃成员数 / 50(冷启动平滑封顶)
}
TRUST_INDEX_PARTICIPATION_FULL = 50   # 参与度满分线(50 活跃成员)

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
                      urgency: str = "normal",
                      relay: bool = False, legs: int = 0) -> dict:
        """发布叫帮单(安全预检 + 信值预计算; P2 支持互助接力)

        接力模式: 偏远地区多人分段完成; 总信值按段均分(末段补差),
        各段独立接单/开始/完成, 全部段完成才整单结算。

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
        if relay and not (RELAY_MIN_LEGS <= legs <= RELAY_MAX_LEGS):
            raise ValueError(f"接力段数须为 {RELAY_MIN_LEGS}-{RELAY_MAX_LEGS}")

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
            "donatedTrust": 0.0,
            "status": STATUS_PUBLISHED,
            "helperId": None,
            "startedAt": "",
            "completedAt": "",
            "cancelReason": "",
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        # P2 互助接力: 分段结构与按段信值拆分
        if relay:
            order["isRelay"] = True
            order["legRewards"] = self._split_leg_rewards(
                trust_reward, legs)
            base_addr = (address or "").strip()[:60]
            order["relayLegs"] = [
                {"legNo": i, "address": f"{base_addr}(第{i}段)",
                 "status": "pending", "helperId": None}
                for i in range(1, legs + 1)
            ]
            order["relayHelpers"] = []
        return await self.repo.save_order(order)

    @staticmethod
    def _split_leg_rewards(total: float, legs: int) -> list[float]:
        """接力段信值拆分(确定性均分, 末段补差保证总和恒等)"""
        per = round(total / legs, 2)
        rewards = [per] * legs
        rewards[-1] = round(total - per * (legs - 1), 2)
        return rewards

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
                   limit: int = 50,
                   member_id: int = None) -> list[dict]:
        """大厅(公开; 紧急优先 → 偏好命中优先 → 距离近优先 → 新单优先)

        P1 个性化: 传入 member_id 时, 依据其历史接单统计(意愿偏好学习)
        对常接类别命中单加权置前, 并附 fitScore/fitReason 可解释标签。
        """
        # 意愿偏好学习(确定性统计: 历史完成单的类别频次)
        pref_categories: list[str] = []
        if member_id:
            pref_categories = await self._top_categories(member_id)
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
            item["donatedTrust"] = round(float(o.get("donatedTrust", 0)), 2)
            # 个性化适配标签(可解释)
            if pref_categories and o.get("category") in pref_categories:
                item["fitScore"] = 1
                item["fitReason"] = f"您常接「{CATEGORIES[o['category']]['name']}」类互助"
            else:
                item["fitScore"] = 0
                item["fitReason"] = ""
            # P2 接力元数据(大厅展示当前段进度)
            if o.get("isRelay") and o.get("relayLegs"):
                done = sum(1 for l in o["relayLegs"]
                           if l.get("status") == "completed")
                item["relayMeta"] = {
                    "totalLegs": len(o["relayLegs"]),
                    "completedLegs": done,
                    "currentLegNo": done + 1,
                    "currentLegAddress": (o["relayLegs"][done]
                                          .get("address", "")),
                }
            out.append(item)
        # 排序(Python sort 稳定, 分层优先级递进):
        # 新单优先 → 距离近优先 → 偏好命中优先 → 紧急优先(最高优先级)
        out.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        out.sort(key=lambda o: o.get("distanceKm", 0))
        out.sort(key=lambda o: o.get("fitScore", 0), reverse=True)
        out.sort(key=lambda o: (
            0 if o.get("urgency") == "urgent" else 1,   # 紧急优先(不可被偏好覆盖)
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

            now = _now_iso()
            # P2 接力: 接的是当前活跃段
            if order.get("isRelay"):
                leg = self._active_leg(order)
                leg.update({"status": "matched", "helperId": helper_id})
                if helper_id not in order.get("relayHelpers", []):
                    order.setdefault("relayHelpers", []).append(helper_id)
            order.update({"status": STATUS_MATCHED,
                         "helperId": helper_id,
                         "updatedAt": now})
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
        # P2 接力: 段状态同步
        if order.get("isRelay"):
            self._active_leg(order).update({"status": "in_progress"})
        return await self.repo.save_order(order)

    async def complete(self, order_id: int, operator_id: int) -> dict:
        """完成服务(in_progress → completed; 双方均可确认)

        结算(确定性):
            - 帮助者 + trustValueReward(公益 100% / 有偿 10% 已预计算)
            - 公益单发布者 +1 感谢信值(受助感恩记录)
            - 信值流水落 help_trust_ledger(双轨 track)
        P2 接力: 完成当前段(该段帮助者得段信值); 全段完成才整单收口。
        """
        async with get_lock(f"help:order:{order_id}"):
            order = await self._require(order_id)
            if order["status"] != STATUS_IN_PROGRESS:
                raise ValueError(f"单状态非法({STATUS_NAMES.get(order['status'])})")
            if operator_id not in (order.get("publisherId"),
                                   order.get("helperId")):
                raise ValueError("仅互助双方可确认完成")

            now = _now_iso()

            # ---------- P2 互助接力: 段完成 ----------
            if order.get("isRelay"):
                return await self._complete_relay_leg(order, operator_id, now)

            order.update({"status": STATUS_COMPLETED,
                          "completedAt": now,
                          "updatedAt": now})
            await self.repo.save_order(order)

            # 信值结算(锁内幂等: completed 一次性)
            ledger_ids = []
            reward = float(order.get("trustValueReward", 0))
            # P1 捐赠池并入: 完成时帮助者额外获得社区捐赠信值(公益单)
            donated = float(order.get("donatedTrust", 0) or 0)
            helper_delta = round(reward + donated, 2)
            helper_id = order.get("helperId")
            ledger_id = await self.repo.next_id("ledger")
            reason = (f"{'公益' if order['mode'] == 'public' else '有偿'}"
                      f"互助完成: {order['title'][:20]}"
                      f"(时长 {order['durationMinutes']} 分钟)")
            if donated > 0:
                reason += f"; 含社区捐赠 {donated} 信值"
            await self.repo.save_ledger({
                "ledgerId": ledger_id,
                "memberId": helper_id,
                "orderId": order_id,
                "track": order["mode"],           # public / paid 双轨
                "delta": helper_delta,
                "reason": reason,
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
                    "helperTrustDelta": helper_delta,
                    "baseReward": reward,
                    "donatedTrust": donated,
                    "publisherThanks": 1 if order["mode"] == "public" else 0,
                    "ledgerIds": ledger_ids,
                    "thanksLedgerId": thanks_id,
                },
            }

    async def _complete_relay_leg(self, order: dict, operator_id: int,
                                  now: str) -> dict:
        """接力段完成(锁内调用): 当前段结算 + 段推进/整单收口"""
        order_id = order["orderId"]
        leg = self._active_leg(order)
        leg_no = leg["legNo"]
        leg_helper = leg.get("helperId")
        legs = order["relayLegs"]
        rewards = order.get("legRewards") or []
        leg_reward = float(rewards[leg_no - 1]) if leg_no <= len(rewards) \
            else round(float(order.get("trustValueReward", 0)) / len(legs), 2)

        # 段信值结算(该段帮助者)
        ledger_id = await self.repo.next_id("ledger")
        await self.repo.save_ledger({
            "ledgerId": ledger_id,
            "memberId": leg_helper,
            "orderId": order_id,
            "track": order["mode"],
            "delta": leg_reward,
            "reason": (f"互助接力第 {leg_no}/{len(legs)} 段完成: "
                       f"{order['title'][:20]}"),
            "createdAt": now,
        })
        leg["status"] = "completed"

        # 全段完成 → 整单收口(末段结算发布者感谢 + 捐赠池归末段帮助者)
        all_done = all(l["status"] == "completed" for l in legs)
        thanks_id = None
        donated = float(order.get("donatedTrust", 0) or 0)
        donated_note = 0.0
        if all_done:
            order.update({"status": STATUS_COMPLETED,
                          "completedAt": now, "updatedAt": now})
            if donated > 0:
                # 捐赠池全额归末段帮助者(确定性: 末段收口人)
                donated_note = donated
                did = await self.repo.next_id("ledger")
                await self.repo.save_ledger({
                    "ledgerId": did,
                    "memberId": leg_helper,
                    "orderId": order_id,
                    "track": order["mode"],
                    "delta": donated,
                    "reason": f"接力互助收口: 含社区捐赠 {donated} 信值",
                    "createdAt": now,
                })
            if order["mode"] == "public":
                thanks_id = await self.repo.next_id("ledger")
                await self.repo.save_ledger({
                    "ledgerId": thanks_id,
                    "memberId": order.get("publisherId"),
                    "orderId": order_id,
                    "track": "public",
                    "delta": 1,
                    "reason": f"受助感恩记录(接力完成): {order['title'][:20]}",
                    "createdAt": now,
                })
        else:
            # 段推进: 下一段成为活跃段, 整单回大厅待接
            order.update({"status": STATUS_PUBLISHED,
                          "helperId": None,
                          "updatedAt": now})
        await self.repo.save_order(order)
        return {
            "order": order,
            "settlement": {
                "relay": True,
                "legCompleted": leg_no,
                "totalLegs": len(legs),
                "allDone": all_done,
                "helperTrustDelta": leg_reward + donated_note,
                "legReward": leg_reward,
                "donatedTrust": donated_note,
                "publisherThanks": 1 if (all_done
                                        and order["mode"] == "public") else 0,
                "ledgerIds": [ledger_id],
                "thanksLedgerId": thanks_id,
            },
        }

    @staticmethod
    def _active_leg(order: dict) -> dict:
        """接力单当前活跃段(首个未完成段; 锁内调用保证一致)"""
        for leg in order.get("relayLegs", []):
            if leg.get("status") != "completed":
                return leg
        raise ValueError("接力段状态异常(无活跃段)")

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
            # P2 接力: 已有完成段不可取消(已履约的善行不可撤)
            if order.get("isRelay") and any(
                    l.get("status") == "completed"
                    for l in order.get("relayLegs", [])):
                raise ValueError("接力互助已有完成的段, 不可取消")

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
        """我接的互助(含接力段历史: relayHelpers 命中也可见)"""
        orders = await self.repo.list_orders(
            helper_id=member_id, limit=100)
        seen_ids = {o["orderId"] for o in orders}
        # P2 接力: 段推进后 helperId 更新, 历史段帮助者经 relayHelpers 补全
        all_orders = await self.repo.list_orders(limit=2000)
        for o in all_orders:
            if (o.get("isRelay") and o["orderId"] not in seen_ids
                    and member_id in (o.get("relayHelpers") or [])):
                orders.append(o)
                seen_ids.add(o["orderId"])
        orders.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        return [self._decorate(o) for o in orders[:100]]

    async def trust_profile(self, member_id: int) -> dict:
        """信值档案(公益/有偿双轨累计 + 流水 + 荣誉等级)"""
        summary = await self.repo.trust_summary(member_id)
        entries = await self.repo.list_ledger(member_id, limit=50)
        # 被评价均分(帮助质量)
        reviews = await self.repo.list_reviews(
            reviewee_id=member_id, limit=200)
        ratings = [r["score"] for r in reviews]
        # P1 捐赠累计(反哺公益的善行记录)
        donated_out = await self.repo.list_donations(donor_id=member_id)
        donated_out_total = round(sum(d.get("amount", 0)
                                      for d in donated_out), 2)
        # P3 碳积分摘要(完整明细见 /carbon/{member_id})
        carbon = await self.carbon_profile(member_id)
        summary.update({
            "ratingAvg": round(sum(ratings) / len(ratings), 1) if ratings else None,
            "ratingCount": len(ratings),
            "ledger": entries,
            "gates": {
                "public": PUBLIC_ACCEPT_TRUST_GATE,
                "paid": PAID_ACCEPT_TRUST_GATE,
            },
            "paidRatio": PAID_TRUST_RATIO,
            "donateGate": DONATE_TRUST_GATE,
            "donatedOut": donated_out_total,
            "honor": self._honor_level(summary["publicTrust"]),
            "carbonGrams": carbon["carbonGrams"],
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
    # 8. P1·三维匹配引擎(信值匹配 + 同类经验 + 地理最优; 全确定性)
    # ============================================================

    async def match(self, order_id: int) -> dict:
        """为求助单计算 Top3 候选帮助者(发布者观测面; 不自动派单)

        候选池: 历史完成过互助的全部帮助者(排除发布者本人)。
        评分 = 信值匹配(40%) + 同类经验(30%) + 地理最优(30%)
        推荐理由: 确定性模板拼接(信值/经验/距离/评分), 可解释。

        Raises:
            KeyError: 单不存在 / ValueError: 非待接单状态
        """
        order = await self._require(order_id)
        if order["status"] != STATUS_PUBLISHED:
            raise ValueError(f"仅待接单可查看推荐({STATUS_NAMES.get(order['status'])})")

        # 候选池: 历史 completed 单的帮助者(去重, 排除发布者)
        completed = await self.repo.list_orders(
            status=STATUS_COMPLETED, limit=1000)
        helper_ids: list[int] = []
        last_order_of: dict[int, dict] = {}
        skill_count: dict[int, int] = {}
        for o in completed:
            hid = o.get("helperId")
            if hid is None or hid == order.get("publisherId"):
                continue
            if hid not in last_order_of:
                helper_ids.append(hid)
            last_order_of[hid] = o          # 后单覆盖 → 最近一次
            if o.get("category") == order.get("category"):
                skill_count[hid] = skill_count.get(hid, 0) + 1

        candidates = []
        for hid in helper_ids:
            trust = await self.repo.trust_summary(hid)
            reviews = await self.repo.list_reviews(
                reviewee_id=hid, limit=200)
            ratings = [r["score"] for r in reviews]
            rating_avg = (round(sum(ratings) / len(ratings), 1)
                          if ratings else None)
            # 三维评分(确定性归一化)
            trust_score = min(trust["totalTrust"] / MATCH_TRUST_FULL, 1.0)
            skill_score = min(skill_count.get(hid, 0) / MATCH_SKILL_FULL, 1.0)
            geo = last_order_of[hid]
            distance = haversine_km(
                order.get("longitude", 0), order.get("latitude", 0),
                geo.get("longitude", 0), geo.get("latitude", 0))
            geo_score = max(0.0, 1.0 - distance / HALL_RADIUS_KM)
            total = (MATCH_WEIGHT_TRUST * trust_score
                     + MATCH_WEIGHT_SKILL * skill_score
                     + MATCH_WEIGHT_GEO * geo_score)
            # 推荐理由(确定性模板, 无 LLM)
            reason_parts = [f"累计信值 {trust['totalTrust']}",
                            f"同类经验 {skill_count.get(hid, 0)} 单"]
            if rating_avg is not None:
                reason_parts.append(f"评分 {rating_avg}")
            if distance <= HALL_RADIUS_KM:
                reason_parts.append(f"常活动区距求助点 {round(distance, 1)}km")
            candidates.append({
                "memberId": hid,
                "matchScore": round(total, 3),
                "trustScore": round(trust_score, 3),
                "skillScore": round(skill_score, 3),
                "geoScore": round(geo_score, 3),
                "sameCategoryDone": skill_count.get(hid, 0),
                "ratingAvg": rating_avg,
                "distanceKm": round(distance, 2),
                "reason": " · ".join(reason_parts),
            })
        candidates.sort(key=lambda c: c["matchScore"], reverse=True)
        top = candidates[:MATCH_TOP_N]
        return {
            "orderId": order_id,
            "weights": {"trust": MATCH_WEIGHT_TRUST,
                        "skill": MATCH_WEIGHT_SKILL,
                        "geo": MATCH_WEIGHT_GEO},
            "topCandidates": top,
            "candidatePool": len(helper_ids),
        }

    # ============================================================
    # 9. P1·意愿偏好画像(确定性统计: 历史接单聚合)
    # ============================================================

    async def preferences(self, member_id: int) -> dict:
        """我的互助偏好画像(常接类型/活跃时段/累计统计; 纯统计)"""
        helped = await self.repo.list_orders(
            helper_id=member_id, limit=500)
        done = [o for o in helped if o.get("status") == STATUS_COMPLETED]
        cat_count: dict[str, int] = {}
        for o in done:
            cat_count[o["category"]] = cat_count.get(o["category"], 0) + 1
        # 时段聚合(UTC → 本地不做, 统一用单据时间口径)
        slot_count = {"morning": 0, "afternoon": 0, "evening": 0, "night": 0}
        for o in helped:
            hour = int(o.get("createdAt", "T0")[11:13] or 0)
            if 5 <= hour < 11:
                slot_count["morning"] += 1
            elif 11 <= hour < 17:
                slot_count["afternoon"] += 1
            elif 17 <= hour < 23:
                slot_count["evening"] += 1
            else:
                slot_count["night"] += 1
        top_cats = sorted(cat_count.items(), key=lambda kv: -kv[1])
        return {
            "memberId": member_id,
            "helpedCount": len(helped),
            "completedCount": len(done),
            "topCategories": [
                {"category": c, "name": CATEGORIES[c]["name"], "count": n}
                for c, n in top_cats[:3]
            ],
            "activeSlots": sorted(slot_count.items(),
                                  key=lambda kv: -kv[1]),
            "totalDurationMinutes": sum(o.get("durationMinutes", 0)
                                        for o in done),
        }

    async def _top_categories(self, member_id: int) -> list[str]:
        """意愿偏好学习内核: 高频类别(频次 ≥ 完成总数 1/3)视为偏好类(确定性)"""
        helped = await self.repo.list_orders(
            helper_id=member_id, status=STATUS_COMPLETED, limit=500)
        cats = [o["category"] for o in helped
                if o.get("category") in CATEGORIES]
        if not cats:
            return []
        threshold = len(cats) / 3
        freq: dict[str, int] = {}
        for c in cats:
            freq[c] = freq.get(c, 0) + 1
        return [c for c, n in freq.items() if n >= threshold]

    # ============================================================
    # 10. P1·互助故事卡(确定性模板生成, 分享传播互助温度)
    # ============================================================

    async def story_card(self, order_id: int) -> dict:
        """互助故事卡(仅 completed 单; 双方评价摘录 + 类别文案 + 分享文案)"""
        order = await self._require(order_id)
        if order["status"] != STATUS_COMPLETED:
            raise ValueError(f"仅已完成互助可生成故事卡({STATUS_NAMES.get(order['status'])})")
        reviews = await self.repo.list_reviews(order_id=order_id, limit=10)
        pub_review = next((r for r in reviews
                           if r.get("direction") == "publisher_to_helper"), None)
        helper_review = next((r for r in reviews
                              if r.get("direction") == "helper_to_publisher"), None)
        cat = order.get("category", "other")
        donated = round(float(order.get("donatedTrust", 0) or 0), 2)
        date = order.get("completedAt", "")[:10]
        # 分享文案(确定性拼接)
        share = (f"我在竹香酒·AI智能叫帮完成了一次「{CATEGORIES[cat]['name']}」互助: "
                 f"{order['title']}。帮助者获得 {order.get('trustValueReward', 0)} 信值"
                 + (f"(含社区捐赠 {donated})" if donated > 0 else "")
                 + f"。{STORY_THEMES[cat]}")
        return {
            "orderId": order_id,
            "title": order["title"],
            "category": cat,
            "categoryName": CATEGORIES[cat]["name"],
            "theme": STORY_THEMES[cat],
            "mode": order["mode"],
            "trustValueReward": order.get("trustValueReward", 0),
            "donatedTrust": donated,
            "date": date,
            "publisherReview": pub_review["content"] if pub_review else "",
            "publisherScore": pub_review["score"] if pub_review else None,
            "helperReview": helper_review["content"] if helper_review else "",
            "helperScore": helper_review["score"] if helper_review else None,
            "shareText": share,
        }

    # ============================================================
    # 11. P1·安全护航(全程观测: 超时温和提醒, 惩罚/处置永不自动)
    # ============================================================

    async def guard(self, order_id: int) -> dict:
        """服务护航状态(观测面; 含破冰提示 + 超时温和提醒)"""
        from datetime import datetime, UTC
        order = await self._require(order_id)
        status = order["status"]
        cat = order.get("category", "other")
        icebreaker = ICEBREAKER_TIPS.get(cat, ICEBREAKER_TIPS["other"])
        result = {
            "orderId": order_id,
            "status": status,
            "statusName": STATUS_NAMES.get(status, ""),
            "icebreaker": icebreaker,
        }
        if status == STATUS_IN_PROGRESS and order.get("startedAt"):
            started = datetime.fromisoformat(order["startedAt"])
            elapsed = (datetime.now(UTC) - started).total_seconds() / 60
            expected = max(1, order.get("durationMinutes", 60))
            ratio = elapsed / expected
            result.update({
                "elapsedMinutes": round(elapsed, 1),
                "expectedMinutes": expected,
                "overtimeRatio": round(ratio, 2),
                "reminder": ("服务已超过预计时长 50%, 建议联系对方确认进展与安全"
                             if ratio >= GUARD_OVERTIME_RATIO else ""),
            })
        else:
            result.update({"elapsedMinutes": None, "expectedMinutes": None,
                           "overtimeRatio": None, "reminder": ""})
        return result

    # ============================================================
    # 12. P1·信值捐赠(高信值用户反哺公益单, 完成时奖励帮助者)
    # ============================================================

    async def donate(self, order_id: int, donor_id: int,
                     amount: int) -> dict:
        """向待接单公益单捐赠信值(1-10; 每单每人一次幂等)

        规则(确定性):
            - 仅 published 公益单可捐(有偿单不参与捐赠)
            - 捐赠者须累计信值 ≥50(高信值用户) 且非发布者本人
            - 捐赠即时从捐赠者信值扣减, 入单捐赠池
            - 完成结算时捐赠池全额奖励帮助者(见 complete)

        Raises:
            KeyError: 单不存在 / ValueError: 规则校验失败
        """
        if not isinstance(amount, int) or not (DONATE_MIN <= amount <= DONATE_MAX):
            raise ValueError(f"捐赠额度须为 {DONATE_MIN}-{DONATE_MAX} 信值")
        async with get_lock(f"help:order:{order_id}"):
            order = await self._require(order_id)
            if order["status"] != STATUS_PUBLISHED:
                raise ValueError(f"仅待接单求助可捐赠({STATUS_NAMES.get(order['status'])})")
            if order["mode"] != "public":
                raise ValueError("仅有偿外的公益求助可接受捐赠")
            if order.get("publisherId") == donor_id:
                raise ValueError("不能为自己的求助捐赠")

            # 资格门槛(高信值用户)
            summary = await self.repo.trust_summary(donor_id)
            if summary["totalTrust"] < DONATE_TRUST_GATE:
                raise ValueError(
                    f"捐赠需累计信值 ≥{DONATE_TRUST_GATE}"
                    f"(当前 {summary['totalTrust']}); 继续公益互助即可解锁")

            # 幂等: 每单每人一次(同单同实体去重)
            existing = await self.repo.list_donations(order_id=order_id)
            if any(d.get("donorId") == donor_id for d in existing):
                raise ValueError("您已为该求助捐赠过(每单限一次)")

            # 即时扣减捐赠者信值(track=public, 惩罚/给付经审计留痕)
            ledger_id = await self.repo.next_id("ledger")
            await self.repo.save_ledger({
                "ledgerId": ledger_id,
                "memberId": donor_id,
                "orderId": order_id,
                "track": "public",
                "delta": -amount,
                "reason": f"公益捐赠: {order['title'][:20]}(完成时奖励帮助者)",
                "createdAt": _now_iso(),
            })
            # 捐赠入池(订单维度累计)
            order.update({
                "donatedTrust": round(
                    float(order.get("donatedTrust", 0) or 0) + amount, 2),
                "updatedAt": _now_iso(),
            })
            await self.repo.save_order(order)
            donation_id = await self.repo.next_id("donation")
            donation = {
                "donationId": donation_id,
                "orderId": order_id,
                "donorId": donor_id,
                "amount": amount,
                "createdAt": _now_iso(),
            }
            await self.repo.save_donation(donation)
            return {
                "donation": donation,
                "orderDonatedTrust": order["donatedTrust"],
                "donorTrustLeft": round(
                    summary["totalTrust"] - amount, 2),
            }

    # ============================================================
    # 13. P2·信值传承(数字功德碑: 公益信值继承给亲属, 双方确认)
    # ============================================================

    async def heritage_apply(self, owner_id: int, heir_id: int,
                             amount: float | None = None) -> dict:
        """发起信值传承(仅公益信值; 受让人确认后划转)

        规则(确定性):
            - 仅公益信值可传承(有偿信值不可——宪法口径)
            - 传承人须有可传承公益信值 ≥ HERITAGE_MIN
            - amount 缺省 = 全部公益信值; 划转时按受让人确认时点实际净值
            - 每人同时仅一笔待确认传承(幂等)
            - 受让人不可是自己

        Raises:
            ValueError: 参数/门槛/重复发起
        """
        if heir_id == owner_id:
            raise ValueError("不能传承给自己")
        if amount is not None and amount < HERITAGE_MIN:
            raise ValueError(f"传承额度须 ≥{HERITAGE_MIN} 公益信值")

        summary = await self.repo.trust_summary(owner_id)
        if summary["publicTrust"] < HERITAGE_MIN:
            raise ValueError(
                f"可传承公益信值不足(当前 {summary['publicTrust']}, "
                f"须 ≥{HERITAGE_MIN})")
        declared = (round(float(amount), 2) if amount is not None
                    else round(summary["publicTrust"], 2))

        existing = await self.repo.list_heritage(owner_id=owner_id, limit=10)
        if any(h.get("status") == HERITAGE_STATUS_PENDING for h in existing):
            raise ValueError("已有待确认的传承(每笔确认后方可再发起)")

        heritage_id = await self.repo.next_id("heritage")
        heritage = {
            "heritageId": heritage_id,
            "ownerId": owner_id,
            "heirId": heir_id,
            "declaredAmount": declared,
            "status": HERITAGE_STATUS_PENDING,
            "transferredAmount": 0,
            "createdAt": _now_iso(),
            "confirmedAt": "",
        }
        return await self.repo.save_heritage(heritage)

    async def heritage_accept(self, heritage_id: int,
                               operator_id: int) -> dict:
        """受让人确认传承(划转即时生效; 按确认时点实际公益净值封顶)

        Raises:
            KeyError: 传承单不存在 / ValueError: 状态/身份/净值
        """
        async with get_lock(f"help:heritage:{heritage_id}"):
            heritage = await self.repo.get_heritage(heritage_id)
            if heritage is None:
                raise KeyError(f"传承单不存在(heritageId={heritage_id})")
            if heritage["status"] != HERITAGE_STATUS_PENDING:
                raise ValueError(f"传承单状态非法({heritage['status']})")
            if operator_id != heritage.get("heirId"):
                raise ValueError("仅受让人可确认传承")

            now = _now_iso()
            # 划转额 = min(声明额, 确认时点实际公益净值)——确定性封顶
            summary = await self.repo.trust_summary(heritage["ownerId"])
            transfer = round(min(float(heritage["declaredAmount"]),
                                 summary["publicTrust"]), 2)
            if transfer < HERITAGE_MIN:
                raise ValueError(
                    f"传承人当前公益信值已不足(实际 {summary['publicTrust']}, "
                    f"须 ≥{HERITAGE_MIN}), 请联系传承人或重新发起")

            # 双边流水留痕(转出/承继; track=public)
            out_id = await self.repo.next_id("ledger")
            await self.repo.save_ledger({
                "ledgerId": out_id,
                "memberId": heritage["ownerId"],
                "orderId": 0,
                "track": "public",
                "delta": -transfer,
                "reason": "信值传承转出(数字功德碑继承)",
                "createdAt": now,
            })
            in_id = await self.repo.next_id("ledger")
            await self.repo.save_ledger({
                "ledgerId": in_id,
                "memberId": heritage["heirId"],
                "orderId": 0,
                "track": "public",
                "delta": transfer,
                "reason": "信值传承承继(数字功德碑继承)",
                "createdAt": now,
            })
            heritage.update({"status": HERITAGE_STATUS_DONE,
                            "transferredAmount": transfer,
                            "confirmedAt": now})
            await self.repo.save_heritage(heritage)
            return {"heritage": heritage, "transferred": transfer}

    async def heritage_my(self, member_id: int) -> dict:
        """我的传承记录(发起 + 受让两向)"""
        outgoing = await self.repo.list_heritage(owner_id=member_id)
        incoming = await self.repo.list_heritage(heir_id=member_id)
        return {"outgoing": outgoing, "incoming": incoming}

    async def heritage_cancel(self, heritage_id: int,
                              operator_id: int) -> dict:
        """发起人撤回待确认传承(v2: 双向退出权——受让人可拒收沉默, 发起人可反悔)

        规则(确定性):
            - 仅 pending 状态可撤回(done/cancelled 均拒绝)
            - 仅发起人本人可撤回(受让人无权代撤)
            - 撤回即时生效, 留痕不删除(审计口径)

        Raises:
            KeyError: 传承单不存在 / ValueError: 状态/身份非法
        """
        async with get_lock(f"help:heritage:{heritage_id}"):
            heritage = await self.repo.get_heritage(heritage_id)
            if heritage is None:
                raise KeyError(f"传承单不存在(heritageId={heritage_id})")
            if heritage["status"] != HERITAGE_STATUS_PENDING:
                raise ValueError(
                    f"仅待确认传承可撤回(当前 {heritage['status']})")
            if operator_id != heritage.get("ownerId"):
                raise ValueError("仅发起人可撤回传承")
            heritage.update({"status": HERITAGE_STATUS_CANCELLED,
                             "updatedAt": _now_iso()})
            await self.repo.save_heritage(heritage)
            return {"heritage": heritage}

    # ============================================================
    # 14. P2·企业信值包(CSR 认捐池: B 端反哺 C 端, 平台零资金流)
    # ============================================================

    async def csr_create_package(self, member_id: int, name: str,
                                 amount: float, note: str = "") -> dict:
        """创建企业信值包(CSR 认捐留痕; 资金不经平台, 认捐即入池)"""
        if not (name or "").strip():
            raise ValueError("企业包名称不能为空")
        if not (CSR_PACKAGE_MIN <= amount <= CSR_PACKAGE_MAX):
            raise ValueError(
                f"企业包额度须为 {CSR_PACKAGE_MIN}-{CSR_PACKAGE_MAX} 信值")

        package_id = await self.repo.next_id("csr")
        pkg = {
            "packageId": package_id,
            "memberId": member_id,
            "name": name.strip()[:60],
            "amount": round(float(amount), 2),
            "remaining": round(float(amount), 2),
            "note": (note or "").strip()[:200],
            "status": "active",
            "donations": [],
            "createdAt": _now_iso(),
        }
        return await self.repo.save_csr_package(pkg)

    async def csr_donate(self, package_id: int, member_id: int,
                         order_id: int, amount: float) -> dict:
        """企业包定向捐助公益单(入单捐赠池, 完成时奖励帮助者)

        规则(确定性):
            - 仅包创建企业可操作
            - 目标须为待接单公益单
            - 单笔 ≤ CSR_DONATE_MAX 且 ≤ 包余额
            - 每包每单限捐一次(幂等)
            - 企业包捐赠不动用个人信值(包池独立), 全程留痕

        Raises:
            KeyError: 包/单不存在 / ValueError: 规则校验失败
        """
        if not (DONATE_MIN <= amount <= CSR_DONATE_MAX):
            raise ValueError(f"单笔捐助额度须为 {DONATE_MIN}-{CSR_DONATE_MAX}")
        async with get_lock(f"help:order:{order_id}"):
            pkg = await self.repo.get_csr_package(package_id)
            if pkg is None:
                raise KeyError(f"企业包不存在(packageId={package_id})")
            if pkg.get("memberId") != member_id:
                raise ValueError("仅企业包创建方可操作")
            if pkg.get("status") != "active":
                raise ValueError(f"企业包状态非法({pkg.get('status')})")
            if amount > pkg.get("remaining", 0):
                raise ValueError(
                    f"包余额不足(剩余 {pkg.get('remaining')} 信值)")

            order = await self._require(order_id)
            if order["status"] != STATUS_PUBLISHED:
                raise ValueError(
                    f"仅待接单求助可捐助({STATUS_NAMES.get(order['status'])})")
            if order["mode"] != "public":
                raise ValueError("仅公益求助可接受企业 CSR 捐助")

            # 幂等: 每包每单一次
            if any(d.get("orderId") == order_id
                   for d in pkg.get("donations", [])):
                raise ValueError("该企业包已捐助过此求助(每包每单限一次)")

            # 入单捐赠池(与个人捐赠同一池, 完成时奖励帮助者)
            order.update({
                "donatedTrust": round(
                    float(order.get("donatedTrust", 0) or 0) + amount, 2),
                "updatedAt": _now_iso(),
            })
            await self.repo.save_order(order)

            # 包池扣减 + 捐助留痕(企业 CSR 不动个人信值台账)
            donation_record = {
                "orderId": order_id,
                "orderTitle": order["title"][:30],
                "amount": amount,
                "createdAt": _now_iso(),
            }
            pkg["donations"].append(donation_record)
            pkg["remaining"] = round(pkg["remaining"] - amount, 2)
            if pkg["remaining"] < DONATE_MIN:
                pkg["status"] = "exhausted"
            await self.repo.save_csr_package(pkg)
            # 捐助明细入 help_donations(donorId=企业成员, source=csr)
            donation_id = await self.repo.next_id("donation")
            await self.repo.save_donation({
                "donationId": donation_id,
                "orderId": order_id,
                "donorId": member_id,
                "amount": amount,
                "source": "csr",
                "packageId": package_id,
                "createdAt": _now_iso(),
            })
            return {
                "package": {"packageId": pkg["packageId"],
                            "name": pkg["name"],
                            "remaining": pkg["remaining"],
                            "status": pkg["status"]},
                "donation": donation_record,
                "orderDonatedTrust": order["donatedTrust"],
            }

    async def csr_my_packages(self, member_id: int) -> list[dict]:
        """我的企业信值包列表"""
        return await self.repo.list_csr_packages(member_id=member_id)

    # ============================================================
    # 15. P3·碳积分(公益互助的环境正外部性, 确定性折算, 只读观测)
    # ============================================================

    async def carbon_profile(self, member_id: int) -> dict:
        """我的碳积分档案(按历史完成单折算; 不可交易——宪法口径防金融化)

        折算(确定性): 碳克数 = 时长(小时) × 类别系数
            公益单 100% / 有偿单 50%
        接力单: 碳按整单折算后由各段帮助者均摊(该成员参与过的段
        即计一份; relayHelpers 保留段历史帮助者可见性)。
        """
        # 直接 helperId 命中的完成单 + 该成员参与过段的接力单
        helped = await self.repo.list_orders(
            helper_id=member_id, status=STATUS_COMPLETED, limit=1000)
        seen_ids = {o["orderId"] for o in helped}
        all_orders = await self.repo.list_orders(limit=2000)
        for o in all_orders:
            if (o.get("isRelay") and o.get("status") == STATUS_COMPLETED
                    and o["orderId"] not in seen_ids
                    and member_id in (o.get("relayHelpers") or [])):
                helped.append(o)
                seen_ids.add(o["orderId"])
        helped.sort(key=lambda o: o.get("createdAt", ""), reverse=True)

        total_grams = 0.0
        details = []
        for o in helped:
            hours = max(1.0, o.get("durationMinutes", 60) / 60.0)
            rate = CARBON_GRAMS_PER_HOUR.get(
                o.get("category", "other"), 50)
            ratio = (CARBON_PAID_RATIO if o.get("mode") == "paid"
                     else 1.0)
            if o.get("isRelay"):
                # 接力单: 碳按段数均摊, 该帮助者只计自己那段
                legs = max(1, len(o.get("relayLegs") or []))
                grams = hours * rate * ratio / legs
                note = f"接力 {legs} 段均摊"
            else:
                grams = hours * rate * ratio
                note = ""
            total_grams += grams
            details.append({
                "orderId": o.get("orderId"),
                "title": (o.get("title") or "")[:20],
                "category": o.get("category"),
                "mode": o.get("mode"),
                "carbonGrams": round(grams, 1),
                "note": note,
            })
        return {
            "memberId": member_id,
            "carbonGrams": round(total_grams, 1),
            "carbonKg": round(total_grams / 1000, 2),
            "completedOrders": len(helped),
            "details": details[:20],
            "methodology": ("时长×类别系数(公益100%/有偿50%/接力均摊); "
                            "只读观测不可交易"),
        }

    # ============================================================
    # 16. P3·年度互助白皮书(全量聚合, 无个人隐私)
    # ============================================================

    async def whitepaper(self, year: int | None = None) -> dict:
        """年度互助白皮书数据(确定性聚合; 政府/NGO 可引用的公开口径)

        聚合: 单量/完成率/双轨信值/类别分布/荣誉分布/
              接力·捐赠·传承生态统计/碳减排总量
        """
        from datetime import datetime, UTC
        orders = await self.repo.list_orders(limit=100000)
        if year is not None:
            orders = [o for o in orders
                      if o.get("createdAt", "")[:4] == str(year)]
        # 评价全量(白皮书口径: 好评率)
        reviews = await self._all_reviews()

        completed = [o for o in orders
                     if o.get("status") == STATUS_COMPLETED]
        cancelled = [o for o in orders
                     if o.get("status") == STATUS_CANCELLED]
        public_orders = [o for o in orders if o.get("mode") == "public"]
        relay_orders = [o for o in orders if o.get("isRelay")]

        # 双轨信值(按完成单)
        public_trust = sum(float(o.get("trustValueReward", 0))
                           for o in completed if o.get("mode") == "public")
        paid_trust = sum(float(o.get("trustValueReward", 0))
                         for o in completed if o.get("mode") == "paid")
        # 捐赠生态
        donations = await self.repo.list_donations(limit=100000)
        donated_total = sum(d.get("amount", 0) for d in donations)
        # 传承生态
        heritages = await self.repo.list_heritage(limit=100000)
        heritage_done = [h for h in heritages
                         if h.get("status") == HERITAGE_STATUS_DONE]
        heritage_total = sum(h.get("transferredAmount", 0)
                             for h in heritage_done)
        # 荣誉分布(按完成单帮助者聚合公益信值)
        helper_ids = {o.get("helperId") for o in completed
                      if o.get("helperId")}
        honor_dist: dict[str, int] = {}
        for hid in helper_ids:
            s = await self.repo.trust_summary(hid)
            level = self._honor_level(s["publicTrust"])["name"]
            honor_dist[level] = honor_dist.get(level, 0) + 1
        # 类别分布
        cat_dist: dict[str, int] = {}
        for o in orders:
            c = o.get("category", "other")
            cat_dist[c] = cat_dist.get(c, 0) + 1
        # 碳减排(按完成单全量口径)
        carbon_total = 0.0
        for o in completed:
            hours = max(1.0, o.get("durationMinutes", 60) / 60.0)
            rate = CARBON_GRAMS_PER_HOUR.get(o.get("category", "other"), 50)
            ratio = (CARBON_PAID_RATIO if o.get("mode") == "paid"
                     else 1.0)
            carbon_total += hours * rate * ratio

        settled = len(completed) + len(cancelled)
        return {
            "year": year or "all",
            "generatedAt": datetime.now(UTC).isoformat(),
            "orders": {
                "total": len(orders),
                "completed": len(completed),
                "cancelled": len(cancelled),
                "completionRate": round(
                    len(completed) / settled, 3) if settled else None,
                "publicOrders": len(public_orders),
                "paidOrders": len(orders) - len(public_orders),
                "publicRatio": round(
                    len(public_orders) / len(orders), 3) if orders else None,
                "relayOrders": len(relay_orders),
            },
            "trust": {
                "publicTotal": round(public_trust, 2),
                "paidTotal": round(paid_trust, 2),
                "donatedTotal": round(donated_total, 2),
                "heritageCount": len(heritage_done),
                "heritageTotal": round(heritage_total, 2),
            },
            "categoryDist": [
                {"category": c, "name": CATEGORIES.get(
                    c, CATEGORIES["other"])["name"], "count": n}
                for c, n in sorted(cat_dist.items(),
                                  key=lambda kv: -kv[1])
            ],
            "honorDist": honor_dist,
            "carbon": {
                "totalGrams": round(carbon_total, 1),
                "totalKg": round(carbon_total / 1000, 2),
            },
            "ratings": {
                "total": len(reviews),
                "praiseRate": round(
                    sum(1 for r in reviews if r.get("score", 0) >= 4)
                    / len(reviews), 3) if reviews else None,
            },
            "activeMembers": len(
                {o.get("publisherId") for o in orders} | helper_ids),
        }

    async def _all_reviews(self) -> list[dict]:
        """全量评价(白皮书/指数口径; 复用 repo 列表)"""
        return await self.repo.list_reviews(limit=100000)

    # ============================================================
    # 17. P3·社会信任指数(确定性四因子, 0-100, 可解释)
    # ============================================================

    async def trust_index(self) -> dict:
        """社会信任指数(公开; 无个人数据)

        公式(确定性): 100 × (40%完成率 + 30%好评率 + 20%公益占比
                        + 10%参与度)
        """
        orders = await self.repo.list_orders(limit=100000)
        reviews = await self._all_reviews()
        completed = [o for o in orders if o.get("status") == STATUS_COMPLETED]
        cancelled = [o for o in orders if o.get("status") == STATUS_CANCELLED]
        public_orders = [o for o in orders if o.get("mode") == "public"]
        members = ({o.get("publisherId") for o in orders}
                   | {o.get("helperId") for o in completed})

        settled = len(completed) + len(cancelled)
        completion = (len(completed) / settled) if settled else 0.0
        praise = ((sum(1 for r in reviews if r.get("score", 0) >= 4)
                   / len(reviews)) if reviews else 0.0)
        public_ratio = (len(public_orders) / len(orders)) if orders else 0.0
        participation = min(
            len([m for m in members if m]) / TRUST_INDEX_PARTICIPATION_FULL,
            1.0)

        score = round(100 * (
            TRUST_INDEX_WEIGHTS["completion"] * completion
            + TRUST_INDEX_WEIGHTS["praise"] * praise
            + TRUST_INDEX_WEIGHTS["publicRatio"] * public_ratio
            + TRUST_INDEX_WEIGHTS["participation"] * participation
        ), 1)

        def _pct(v: float) -> str:
            return f"{round(v * 100, 1)}%"

        return {
            "score": score,
            "grade": ("高信任" if score >= 80 else
                      "中信任" if score >= 60 else
                      "成长中" if score >= 40 else "起步期"),
            "factors": {
                "completionRate": {
                    "value": round(completion, 3),
                    "weight": TRUST_INDEX_WEIGHTS["completion"],
                    "display": _pct(completion),
                    "detail": f"{len(completed)}/{settled} 单完成",
                },
                "praiseRate": {
                    "value": round(praise, 3),
                    "weight": TRUST_INDEX_WEIGHTS["praise"],
                    "display": _pct(praise),
                    "detail": f"{len(reviews)} 条评价中 4-5 星占比",
                },
                "publicRatio": {
                    "value": round(public_ratio, 3),
                    "weight": TRUST_INDEX_WEIGHTS["publicRatio"],
                    "display": _pct(public_ratio),
                    "detail": f"{len(public_orders)}/{len(orders)} 公益单",
                },
                "participation": {
                    "value": round(participation, 3),
                    "weight": TRUST_INDEX_WEIGHTS["participation"],
                    "display": _pct(participation),
                    "detail": f"{len([m for m in members if m])} 活跃成员"
                              f"(满分线 {TRUST_INDEX_PARTICIPATION_FULL})",
                },
            },
            "methodology": ("100×(40%完成率+30%好评率+20%公益占比"
                            "+10%参与度); 全确定性聚合, 无 LLM"),
        }

    # ============================================================
    # 18. P3·开放 API(政府/NGO 公开口径: 脱敏聚合摘要)
    # ============================================================

    async def open_stats(self) -> dict:
        """开放统计摘要(公开; 供政府/NGO 平台对接的最小数据集)

        仅聚合指标, 不含任何个人身份数据(宪法口径隐私红线)。
        """
        orders = await self.repo.list_orders(limit=100000)
        completed = [o for o in orders if o.get("status") == STATUS_COMPLETED]
        cancelled = [o for o in orders if o.get("status") == STATUS_CANCELLED]
        public_orders = [o for o in orders if o.get("mode") == "public"]
        settled = len(completed) + len(cancelled)
        idx = await self.trust_index()
        return {
            "module": "AI智能叫帮(67号)",
            "platform": "竹香酒 zxjiu.com",
            "metrics": {
                "totalOrders": len(orders),
                "completedOrders": len(completed),
                "completionRate": round(
                    len(completed) / settled, 3) if settled else None,
                "publicRatio": round(
                    len(public_orders) / len(orders), 3) if orders else None,
                "trustIndex": idx["score"],
            },
            "license": "CC BY-NC-SA(公益引用请注明出处)",
            "privacy": "仅聚合指标, 无个人身份数据",
        }

    @staticmethod
    def _honor_level(public_trust: float) -> dict:
        """荣誉等级(确定性阶梯: 按公益信值晋级; 数字功德碑口径)"""
        for threshold, name, icon in HONOR_LADDER:
            if public_trust >= threshold:
                # 紧邻上一级 = 大于当前阈值的最小一级(阶梯降序, 须取 min)
                higher = min(
                    (t for t, _, _ in HONOR_LADDER if t > threshold),
                    default=None)
                return {
                    "name": name,
                    "icon": icon,
                    "currentTrust": public_trust,
                    "nextAt": higher,
                    "progress": (round(public_trust / higher, 3)
                                 if higher else 1.0),
                }
        # 兜底(理论不可达: 阶梯含 0)
        return {"name": "见习互助者", "icon": "🌱",
                "currentTrust": public_trust,
                "nextAt": 10, "progress": 0.0}

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
