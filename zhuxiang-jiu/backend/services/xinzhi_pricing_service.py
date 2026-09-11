"""68号 P2·信值·臻选——透明定价与反馈闭环服务

依据:《信值·臻选大模型》文档"动态定价与权益引擎"+
"首次版本反馈闭环"+《68号 创新规划方案》§三 P2

核心命题:**价格构成透明化**——文档铁律:
    "价格构成清晰拆解(原价-信值抵扣-会员折扣=实付)"
    "同一商品对不同信值用户价差≤20%, 详情页公示"
    "实付金额不得低于商品成本价(地板保护)"

定价管线(全确定性, 60号 compute_price 复用):
    ① 60号三因子: base × trustDiscount(47号tier)
       × contributionDiscount(合规月数) × promo
       (叠加封顶 0.7 地板——60号铁律复用)
    ② 68号 α 信值抵扣: 雷达等级 S=0.15/A=0.12/B=0.08
       /C,D=0(文档系数; 单笔抵扣≤30% 上限)
    ③ 地板保护: 实付 ≥ 60号地板(0.7×base)——α 抵扣
       不可击穿三因子地板
    ④ 杀熟审计: 同商品跨会员 finalPrice 价差>20%
       → 留痕 auditFlag(处置经 46号, 永不自动)

反馈闭环(文档四步轻量机制):
    埋点(👍/👎+标签) → 分流:
    L1 安抚(无效/情绪——自动回复, 不入人工队列)
    L2 工单(明确问题——路由团队, 24h 时效)
    L3 紧急(关键词触发——15 分钟响应)

红线(宪法域):
    - α 只紧不松: 文档 S.15/A.12/B.08 硬编码;
      调整走 46号建议书(P7e 阈值范式)
    - 价差>20% 仅留痕(杀熟检测, 处置经 46号)
    - LLM 禁入: 定价=公式/分流=关键词路由
"""

import logging
from datetime import datetime, UTC

from repositories.xinzhi_repository import (
    XinzhiRepository,
)

logger = logging.getLogger(__name__)


# ============================================================
# P2 常量(文档口径)
# ============================================================

# α 信值抵扣系数(文档: S=0.15/A=0.12/B=0.08/C,D=0)
XINZHI_ALPHA = {"S": 0.15, "A": 0.12, "B": 0.08,
                "C": 0.0, "D": 0.0}

# 单笔抵扣上限(文档: ≤30% 防恶意套现)
ALPHA_CAP = 0.30

# 杀熟审计线(文档: 同商品不同信值用户价差≤20%)
PRICE_DIFF_AUDIT_LINE = 0.20

# 反馈分级(文档四步闭环)
FEEDBACK_L1 = "L1"   # 安抚(情绪/无效——自动回复)
FEEDBACK_L2 = "L2"   # 工单(明确问题——24h)
FEEDBACK_L3 = "L3"   # 紧急(关键词——15 分钟)
FEEDBACK_LEVELS = (FEEDBACK_L1, FEEDBACK_L2, FEEDBACK_L3)

# L3 紧急关键词(文档口径: "诈骗/泄露隐私/媒体曝光")
L3_KEYWORDS = ("诈骗", "泄露隐私", "媒体曝光", "违法",
               "人身安全")

# L2 场景标签路由(文档: 物流→物流运营/分数→信值产品/
# 客服→客服主管)
L2_TAG_ROUTES = {
    "物流": "物流运营组", "分数": "信值产品组",
    "客服": "客服主管", "价格": "定价审计组",
    "商品": "商品运营组", "互助": "社区运营组",
}

# 反馈场景→标签预设(文档: 根据场景动态生成)
SCENE_TAGS = {
    "product": ("价格不合理", "描述不符", "质量疑虑",
                "推荐不准"),
    "logistics": ("时效不准", "包装破损"),
    "radar": ("分数不合理", "数据缺失", "维度不理解"),
    "service": ("客服回复慢", "态度不佳"),
    "guide": ("推荐不合适", "解释不清楚"),
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def alpha_of_grade(grade: str) -> float:
    """雷达等级 → α 信值抵扣系数(文档表)"""
    return XINZHI_ALPHA.get(str(grade or ""), 0.0)


def classify_feedback(content: str, tags: list,
                      scene: str) -> tuple:
    """反馈分流(AI 初筛的确定性实现——关键词路由)

    Returns:
        (level, routed_to, auto_reply)
    """
    text = f"{content or ''}"
    # L3: 紧急关键词
    for kw in L3_KEYWORDS:
        if kw in text:
            return (FEEDBACK_L3, "风控负责人",
                    "已紧急受理, 专员将在15分钟内联系您。")
    # L2: 明确问题(标签或文本命中路由词)
    for key, team in L2_TAG_ROUTES.items():
        if key in text or any(
                key in str(t) for t in (tags or [])):
            return (FEEDBACK_L2, team, "")
    # L1: 无效/情绪(自动安抚)
    return (FEEDBACK_L1, "",
            "感谢您的反馈, 已记录。您的意见已纳入评估池, "
            "我们将定期公布优化进展。")


class XinzhiPricingService:
    """68号 P2·透明定价(价格拆解+杀熟审计+反馈闭环)"""

    def __init__(self, repo: XinzhiRepository = None):
        self.repo = repo if repo is not None else XinzhiRepository()

    # ============================================================
    # ① 价格构成拆解(60号三因子 + 68号 α 抵扣)
    # ============================================================

    async def price_breakdown(self, member_id: int,
                              product_id: str,
                              promo_factor: float = 1.0
                              ) -> dict:
        """价格构成拆解(原价-信值抵扣-折扣=实付, 强制公示)

        流程:
            60号 compute_price(三因子+0.7 地板)
            → 68号 α 抵扣(雷达等级, ≤30% 上限, 不击穿地板)
            → breakdownLine 拼接(文档要求的公示格式)
            → 同商品跨会员价差审计(>20% 留痕)

        Raises:
            KeyError: 商品不存在 / 会员无雷达
        """
        from repositories.product_repository import (
            ProductRepository)
        product = await ProductRepository().get_by_id(
            product_id)
        if product is None:
            raise KeyError(
                f"商品不存在(productId={product_id})")
        base_price = float(product.get("price") or 0)
        # 会员雷达等级(P0)
        radar = await self.repo.latest_snapshot(member_id)
        if radar is None:
            from services.xinzhi_radar_service import (
                XinzhiRadarService)
            radar = await XinzhiRadarService(
                repo=self.repo).compute_radar(member_id)
        grade = str(radar.get("grade") or "D")
        # 47号 tier(60号信任因子输入)
        from services.pay60_checkout_service import \
            Pay60CheckoutService
        tier = await Pay60CheckoutService()._member_tier(
            member_id)
        # ① 60号三因子
        from services.pay60_registry import compute_price
        three = compute_price(
            base_price, tier=tier,
            compliance_months=0,
            promo_factor=promo_factor)
        after_three = float(three["finalPrice"])
        # ② 68号 α 抵扣(文档系数; ≤30%; 地板不击穿)
        alpha = alpha_of_grade(grade)
        credit_amt = round(
            min(after_three * alpha,
                base_price * ALPHA_CAP), 2)
        final = round(after_three - credit_amt, 2)
        floor = round(base_price * 0.7, 2)
        floored = False
        if final < floor:
            final = floor
            floored = True
        # ③ 公示行(文档格式)
        breakdown_line = (
            f"原价¥{base_price}"
            f" - 信值抵扣¥{credit_amt}(等级{grade} α={alpha})"
            f" - 三因子折扣¥{round(base_price - after_three, 2)}"
            f" = 实付¥{final}")
        # ④ 杀熟审计(同商品跨会员价差)
        audit_flag = await self._audit_price_diff(
            product_id, member_id, final, base_price)
        seq = await self.repo.next_id("detail")
        record = {
            "detailSeq": seq,
            "productId": product_id,
            "productName": product.get("name", ""),
            "memberId": member_id,
            "grade": grade,
            "tier": tier,
            "basePrice": base_price,
            "trustDiscount": (three.get("attribution")
                             or {}).get("trustDiscount"),
            "contributionDiscount": (three.get(
                "attribution") or {}).get(
                "contributionDiscount"),
            "promoFactor": promo_factor,
            "afterThreeFactor": after_three,
            "xinzhiAlpha": alpha,
            "xinzhiCredit": credit_amt,
            "finalPrice": final,
            "breakdownLine": breakdown_line,
            "floored": floored,
            "auditFlag": audit_flag,
            "pricedAt": _now_iso(),
        }
        await self.repo.save_price_detail(record)
        return record

    async def _audit_price_diff(self, product_id: str,
                                member_id: int,
                                final: float,
                                base: float) -> str:
        """杀熟审计: 同商品跨会员价差(留痕, 处置经 46号)

        Returns:
            auditFlag: ""=正常 / "price_diff>20%"
        """
        if base <= 0:
            return ""
        history = await self.repo.list_price_details(
            product_id=product_id, limit=100)
        for h in history:
            if h.get("memberId") == member_id:
                continue
            other = float(h.get("finalPrice") or 0)
            if other <= 0:
                continue
            diff = abs(final - other) / max(final, other)
            if diff > PRICE_DIFF_AUDIT_LINE:
                flag = (f"price_diff>20%("
                        f"{final} vs {other})")
                logger.warning(
                    "xinzhi_p2_price_audit %s m=%s: %s",
                    product_id, member_id, flag)
                return flag
        return ""

    # ============================================================
    # ② 反馈闭环(四步轻量机制)
    # ============================================================

    async def submit_feedback(self, member_id: int,
                              scene: str, tags: list,
                              content: str) -> dict:
        """反馈提交(埋点+分流+时效承诺)

        Args:
            scene: product/logistics/radar/service/guide
            tags: 场景标签(预设见 SCENE_TAGS)
            content: 文本反馈

        Returns:
            {feedbackId, level, routedTo, autoReply,
             sla, sceneTags}
        """
        level, routed_to, auto_reply = classify_feedback(
            content, tags, scene)
        sla = {FEEDBACK_L1: "即时",
               FEEDBACK_L2: "24小时内",
               FEEDBACK_L3: "15分钟"}[level]
        feedback_id = await self.repo.next_id("feedback")
        record = {
            "feedbackId": feedback_id,
            "memberId": member_id,
            "scene": scene,
            "tags": list(tags or []),
            "content": (content or "")[:500],
            "level": level,
            "status": ("auto_replied" if level == FEEDBACK_L1
                       else "pending"),
            "routedTo": routed_to,
            "autoReply": auto_reply,
            "sla": sla,
            "respondedAt": ("" if level != FEEDBACK_L1
                            else _now_iso()),
            "resolvedNote": "",
            "createdAt": _now_iso(),
        }
        await self.repo.save_feedback(record)
        if level == FEEDBACK_L3:
            logger.warning("xinzhi_p2_l3_feedback id=%s "
                           "m=%s scene=%s", feedback_id,
                           member_id, scene)
        return record

    async def feedback_status(self,
                              feedback_id: int) -> dict:
        """反馈进度(透明——文档"用户可见状态")"""
        fb = await self.repo.get_feedback(feedback_id)
        if fb is None:
            raise KeyError(
                f"反馈不存在(feedbackId={feedback_id})")
        return fb

    async def resolve_feedback(self, feedback_id: int,
                               resolved_note: str,
                               resolver: str = "admin"
                               ) -> dict:
        """反馈处置(L2/L3 工单闭环——人工轨)"""
        fb = await self.repo.get_feedback(feedback_id)
        if fb is None:
            raise KeyError(
                f"反馈不存在(feedbackId={feedback_id})")
        if fb.get("level") == FEEDBACK_L1:
            raise ValueError("L1 自动回复反馈无需处置")
        if fb.get("status") in ("resolved", "adopted",
                                "rejected"):
            raise ValueError(
                f"反馈已处置({fb.get('status')}), 不可重复")
        fb.update({
            "status": "resolved",
            "resolvedNote": (resolved_note or "")[:500],
            "respondedAt": _now_iso(),
        })
        return await self.repo.save_feedback(fb)

    # ============================================================
    # 观测面
    # ============================================================

    async def price_audit_view(self,
                               product_id: str = None,
                               limit: int = 50) -> list[dict]:
        """价格构成审计视图(跨会员价差留痕)"""
        return await self.repo.list_price_details(
            product_id=product_id, limit=limit)

    async def feedback_stats(self) -> dict:
        """反馈统计(周报口径——TOP 问题沉淀)"""
        all_fb = await self.repo.list_feedback(limit=10000)
        by_level = {lv: 0 for lv in FEEDBACK_LEVELS}
        tag_counter: dict = {}
        for fb in all_fb:
            by_level[fb.get("level", FEEDBACK_L1)] += 1
            for t in (fb.get("tags") or []):
                tag_counter[str(t)] = \
                    tag_counter.get(str(t), 0) + 1
        top_tags = sorted(tag_counter.items(),
                          key=lambda x: -x[1])[:10]
        return {"total": len(all_fb),
                "byLevel": by_level,
                "topTags": [{"tag": t, "count": c}
                            for t, c in top_tags]}
