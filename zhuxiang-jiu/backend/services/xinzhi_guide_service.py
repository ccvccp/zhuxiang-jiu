"""68号 P2·信值·臻选——小竹臻选导购人格(SOP五步法)

依据:《信值·臻选大模型》文档"导购助手 SOP话术库"+
《68号 创新规划方案》§三 P2/§八决策备忘3

SOP五步法(文档口径):
    ① 意图锚定(用户想要什么——关键词分类, 确定性)
    ② 信值佐证(您的信值等级——雷达查询层直出)
    ③ 透明解释(价格构成公示——P2 price_breakdown 直出)
    ④ 履约跟进(商品口碑——P1 三维评分+销量直出)
    ⑤ 价值沉淀(信值成长建议——雷达最弱维确定性映射)

★ LLM 禁数字铁律(全站口径不变):
    话术=确定性模板拼接(语言层); 一切数字(等级/总分/
    价格/抵扣/评分/销量)100%来自查询层实时结果, 本服务
    禁止生成任何预测性数字/承诺(判定层 LLM 禁入)。

红线(宪法域):
    - 不承诺降价/不诱导消费(导购=解释者非推销者——
      文档"透明化决策解释")
    - 硬闸商品(L4)如实告知风险, 永不静默丢弃
    - 价值沉淀仅指向成长路径(46号域建议, 不涉惩罚)
"""

import logging
from datetime import datetime, UTC

from repositories.xinzhi_repository import (
    XinzhiRepository, DIMENSION_LABELS, DIMENSIONS,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-xinzhi-guide"

# 导购人格(48号小竹人格扩展——确定性话术模板域)
GUIDE_PERSONA = "臻选导购"

# SOP 五步(文档口径)
SOP_STEPS = ("intent_anchor", "trust_evidence",
             "transparent_price", "fulfillment",
             "value_growth")
SOP_STEP_LABELS = {
    "intent_anchor": "意图锚定",
    "trust_evidence": "信值佐证",
    "transparent_price": "透明解释",
    "fulfillment": "履约跟进",
    "value_growth": "价值沉淀",
}

# ① 意图锚定规则(关键词→意图, 封闭注册)
INTENT_RULES = (
    ("price", ("多少钱", "价格", "贵不贵", "便宜",
               "贵", "预算", "划不划算", "折扣")),
    ("recommend", ("推荐", "买什么", "选哪个", "适合",
                   "建议", "哪款")),
    ("doubt", ("为什么", "区别", "介绍", "怎么样",
               "靠谱", "好不好")),
)
INTENT_LABELS = {
    "price": "价格咨询",
    "recommend": "推荐请求",
    "doubt": "商品疑问",
    "browse": "浏览了解",
}

# ⑤ 价值沉淀(最弱维→成长路径——确定性映射, 文档"价值沉淀")
GROWTH_PATHS = {
    "integrity": "保持守约记录, 诚信度是臻选权益的基石",
    "mutual": "参与邻里互助(67号叫帮), 互助值提升最快",
    "expert": "发布高质量评价/案例, 专业度稳步累积",
    "activity": "保持适度活跃, 活跃度随自然使用增长",
    "growth": "持续使用平台服务, 成长力将稳步上行",
}

# 红线声明(透明化公示——文档口径)
GUIDE_RED_LINES = (
    "导购话术中所有数字均来自实时查询层(雷达/定价/评分), 无任何预测性数字",
    "不承诺降价、不诱导消费——导购是解释者而非推销者",
    "风险商品如实告知, 永不静默丢弃",
    "信值成长建议仅指向路径, 惩罚与处置永远经46号审批",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def classify_intent(query: str) -> str:
    """① 意图锚定(关键词分类——确定性, LLM 禁入)"""
    text = str(query or "")
    for intent, keywords in INTENT_RULES:
        if any(k in text for k in keywords):
            return intent
    return "browse"


class XinzhiGuideService:
    """68号 P2·小竹臻选导购人格(SOP五步法)"""

    def __init__(self, repo: XinzhiRepository = None):
        self.repo = repo if repo is not None else XinzhiRepository()

    # ============================================================
    # 导购应答(SOP 五步全链——数字全查询层)
    # ============================================================

    async def guide_reply(self, member_id: int,
                          product_id: str,
                          query: str = "") -> dict:
        """导购应答(五步话术——确定性拼接)

        流程(每步查询层直出, 无 LLM 参与):
            ① 意图锚定: classify_intent(query)
            ② 信值佐证: 雷达 get_radar(等级/总分)
            ③ 透明解释: price_breakdown(构成公示)
            ④ 履约跟进: P1 product_detail(三维评分)
            ⑤ 价值沉淀: 雷达最弱维→成长路径

        Raises:
            KeyError: 商品不存在
        """
        from services.xinzhi_radar_service import (
            XinzhiRadarService)
        from services.xinzhi_pricing_service import (
            XinzhiPricingService)
        from services.xinzhi_prime_service import (
            XinzhiPrimeService)

        # ① 意图锚定
        intent = classify_intent(query)

        # ② 信值佐证(查询层)
        radar = await XinzhiRadarService(
            repo=self.repo).get_radar(member_id)
        grade = str(radar.get("grade") or "D")
        total = float(radar.get("totalScore") or 0)
        tier = str(radar.get("tier") or "standard")

        # ③ 透明解释(查询层——P2 构成公示)
        pricing = XinzhiPricingService(repo=self.repo)
        price = await pricing.price_breakdown(
            member_id, product_id)
        product_name = str(price.get("productName") or "")

        # ④ 履约跟进(查询层——商品口碑+P1 三维评分)
        from repositories.product_repository import (
            ProductRepository)
        product = await ProductRepository().get_by_id(
            product_id)
        if product is None:
            raise KeyError(
                f"商品不存在(productId={product_id})")
        detail = await XinzhiPrimeService(
            repo=self.repo).product_detail(
            member_id, product_id)
        detail["salesMonthly"] = product.get(
            "sales_monthly", 0)
        detail["ratingAvg"] = product.get(
            "rating_avg", 0)

        # ⑤ 价值沉淀(查询层——雷达最弱维)
        dims = {d["key"]: float(d["score"] or 0)
                for d in (radar.get("dimensions") or [])}
        weakest = min(DIMENSIONS,
                      key=lambda d: dims.get(d, 0)) \
            if dims else "integrity"
        growth = GROWTH_PATHS.get(weakest, "")

        steps = {
            "intent_anchor": {
                "intent": intent,
                "label": INTENT_LABELS[intent],
                "say": self._say_intent(intent,
                                        product_name),
            },
            "trust_evidence": {
                "grade": grade, "totalScore": total,
                "tier": tier,
                "say": self._say_trust(grade, total),
            },
            "transparent_price": {
                "breakdownLine": price.get("breakdownLine"),
                "finalPrice": price.get("finalPrice"),
                "auditFlag": price.get("auditFlag", ""),
                "say": self._say_price(price),
            },
            "fulfillment": {
                "valueScore": detail.get("valueScore"),
                "grade": detail.get("grade"),
                "sales": detail.get("salesMonthly"),
                "rating": detail.get("ratingAvg"),
                "say": self._say_fulfillment(detail),
            },
            "value_growth": {
                "weakestDim": weakest,
                "weakestLabel": DIMENSION_LABELS.get(
                    weakest, weakest),
                "say": self._say_growth(weakest, grade,
                                        growth),
            },
        }
        reply = "".join(s["say"] for s in steps.values())
        return {
            "persona": GUIDE_PERSONA,
            "memberId": member_id,
            "productId": product_id,
            "productName": product_name,
            "intent": intent,
            "steps": steps,
            "reply": reply,
            "redLines": list(GUIDE_RED_LINES),
            "modelVersion": MODEL_VERSION,
            "repliedAt": _now_iso(),
        }

    # ============================================================
    # 话术模板(语言层——数字仅由查询结果插值)
    # ============================================================

    @staticmethod
    def _say_intent(intent: str,
                    product_name: str) -> str:
        if intent == "price":
            return (f"您想了解{product_name}的价格构成, "
                    f"我为您逐项拆解。")
        if intent == "recommend":
            return (f"根据您的信值画像, "
                    f"我为您客观介绍{product_name}。")
        if intent == "doubt":
            return (f"关于{product_name}的疑问, "
                    f"我用真实数据为您解答。")
        return (f"欢迎了解{product_name}, "
                f"以下信息全部来自实时查询。")

    @staticmethod
    def _say_trust(grade: str, total: float) -> str:
        return (f"您当前信值等级{grade}"
                f"(雷达总分{total}), "
                f"等级决定了您的臻选抵扣权益。")

    @staticmethod
    def _say_price(price: dict) -> str:
        line = str(price.get("breakdownLine") or "")
        say = f"价格构成: {line}, 每一项都可追溯。"
        if price.get("floored"):
            say += ("本次抵扣已触达平台保护地板, "
                    "不再进一步下探。")
        if price.get("auditFlag"):
            say += ("提示: 本商品存在跨会员价差记录, "
                    "已进入平台审计通道。")
        return say

    @staticmethod
    def _say_fulfillment(detail: dict) -> str:
        hard = bool(detail.get("hardBlocked"))
        if hard:
            return ("履约提示: 该商品暂处风险观察状态, "
                    "建议谨慎购买(拦截原因已公示)。")
        return (f"口碑参考: 价值分"
                f"{detail.get('valueScore')}"
                f"({detail.get('grade')}档), "
                f"月销{detail.get('salesMonthly')}, "
                f"评分{detail.get('ratingAvg')}。")

    @staticmethod
    def _say_growth(weakest: str, grade: str,
                    growth: str) -> str:
        label = DIMENSION_LABELS.get(weakest, weakest)
        return (f"信值成长建议: 您的{label}维度提升空间最大, "
                f"{growth}(当前等级{grade})。")

    # ============================================================
    # 人格卡片(透明化公示)
    # ============================================================

    def persona_card(self) -> dict:
        """导购人格卡片(能力边界公示——文档口径)"""
        return {
            "persona": GUIDE_PERSONA,
            "baseEngine": "48号小竹(语言层人格扩展)",
            "sopSteps": [
                {"key": s,
                 "label": SOP_STEP_LABELS[s]}
                for s in SOP_STEPS],
            "intents": [{"key": k, "label": v}
                        for k, v in
                        INTENT_LABELS.items()],
            "redLines": list(GUIDE_RED_LINES),
            "llmBoundary": ("判定层 LLM 禁入——意图/评分/"
                            "定价全确定性; 话术为模板拼接, "
                            "数字全部来自查询层"),
            "modelVersion": MODEL_VERSION,
        }
