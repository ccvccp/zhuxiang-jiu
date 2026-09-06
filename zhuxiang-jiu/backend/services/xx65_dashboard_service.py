"""65号·网店及商品AI智能管理 四区
看板服务(xx65_dashboard, P4)

计划(§八 P4):
    GET /dashboard 观测面——
    四区实时聚合:
        ① 店铺区(shops): 六态
           分布+活跃数+配额档
        ② 内容区(content): 草稿
           四态+商品数+合规标记
           +禁词替换数
        ③ 营销区(campaigns):
           策略分布+撤销审计
           +ROI 双算汇总
        ④ 治理区(governance):
           健康度+合规事件三道
           防线+回流实况
    + constitution(三开关)

铁律: 观测口径不落库、不受
XX65_MODE 影响、LLM 不进
判定链(对齐 64号 dashboard
范式)。
"""

import logging

from core.helpers import ts
from repositories.xx65_repository import (
    Xx65Repository,
)
from services.xx65_learn_service import (
    learn_mode,
)
from services.xx65_registry import (
    current_mode, llm_mode,
)

logger = logging.getLogger(
    "xx65_dashboard")


class Xx65DashboardService:
    """65号四区看板(P4)"""

    def __init__(self):
        self.repo = Xx65Repository()

    async def dashboard(self) -> dict:
        """四区看板主入口(观测面)"""
        shops = await \
            self.repo.list_shops(
                limit=500)
        products = await \
            self.repo.list_products(
                limit=500)
        drafts = await \
            self.repo.list_drafts(
                limit=500)
        campaigns = await \
            self.repo.list_campaigns(
                limit=500)
        compliance = await \
            self.repo.list_compliance(
                limit=500)
        tips = await \
            self.repo.list_tips(
                limit=500)

        # ① 店铺区
        shop_dist = {
            s: 0 for s in (
                "applying",
                "prechecked",
                "claimed",
                "active",
                "suspended",
                "closed")}
        for s in shops:
            st = s.get("status") \
                or "applying"
            shop_dist[st] = \
                shop_dist.get(st, 0) + 1
        active_shops = [
            s for s in shops
            if s.get("status")
            == "active"]
        quota_dist = {}
        for s in active_shops:
            qt = s.get("quotaTier") \
                or "starter"
            quota_dist[qt] = \
                quota_dist.get(qt, 0) + 1
        shops_zone = {
            "total": len(shops),
            "statusDistribution":
                shop_dist,
            "active": len(active_shops),
            "quotaTierDistribution":
                quota_dist,
        }

        # ② 内容区
        draft_dist = {
            d: 0 for d in (
                "draft",
                "pending_review",
                "published",
                "rejected")}
        for d in drafts:
            st = d.get("status") \
                or "draft"
            draft_dist[st] = \
                draft_dist.get(st, 0) + 1
        published = [
            p for p in products
            if p.get("status")
            == "published"]
        flagged = [
            p for p in published
            if p.get(
                "complianceFlag")]
        word_hits = sum(
            int(d.get("wordHits")
                or 0) for d in drafts)
        content_zone = {
            "totalProducts":
                len(products),
            "published":
                len(published),
            "complianceFlagged":
                len(flagged),
            "drafts": len(drafts),
            "draftDistribution":
                draft_dist,
            "wordReplacements":
                word_hits,
            "complianceRate":
                round(1 - len(flagged)
                      / len(published),
                      4)
                if published
                else 1.0,
        }

        # ③ 营销区
        camp_dist = {
            c: 0 for c in (
                "active",
                "revoked",
                "expired")}
        for c in campaigns:
            st = c.get("status") \
                or "active"
            camp_dist[st] = \
                camp_dist.get(st, 0) + 1
        gmv_total = round(sum(
            float(c.get(
                "estimatedGmv")
                or 0.0)
            for c in campaigns), 2)
        trust_total = round(sum(
            float(c.get(
                "estimatedTrust")
                or 0.0)
            for c in campaigns), 2)
        exclusive_n = sum(
            1 for c in campaigns
            if c.get("exclusive"))
        campaigns_zone = {
            "total": len(campaigns),
            "statusDistribution":
                camp_dist,
            "exclusiveDeclared":
                exclusive_n,
            "estimatedGmvTotal":
                gmv_total,
            "estimatedTrustTotal":
                trust_total,
            "dualTrack":
                "现金 GMV+信值消耗"
                "双算汇总(S3 数字"
                "全来自计算层)",
        }

        # ④ 治理区
        hit_events = [
            e for e in compliance
            if (e.get("findings")
                or [])]
        line_dist = {}
        for e in compliance:
            ln = e.get("line") \
                or "unknown"
            line_dist[ln] = \
                line_dist.get(ln, 0) + 1
        pooled = [
            p for p in published
            if int(p.get(
                "pooledFeedbackId")
                or 0) > 0]
        # 平均健康度(active 店铺
        # 复用三组件口径——轻量
        # 聚合, 不调 shop_health
        # 避免逐店查询)
        governance_zone = {
            "complianceEvents":
                len(compliance),
            "complianceHits":
                len(hit_events),
            "defenseLineDistribution":
                line_dist,
            "pooledProducts":
                len(pooled),
            "coachTipsDelivered":
                len(tips),
            "note": "治理区——三道"
                    "防线分布+回流"
                    "实况+教练触达",
        }

        constitution = {
            "mode": current_mode(),
            "learnMode": learn_mode(),
            "llmMode": llm_mode(),
            "rules": "S1-S8(合规前置"
                     "/信值准入/服务端"
                     "权威/双轨定价/"
                     "撤销窗口/人工兜底"
                     "/赋能分级/溯源"
                     "水印)",
        }

        return {
            "success": True,
            "zones": {
                "shops": shops_zone,
                "content": content_zone,
                "campaigns":
                    campaigns_zone,
                "governance":
                    governance_zone,
            },
            "constitution":
                constitution,
            "note": "65号四区看板——"
                    "店铺/内容/营销/"
                    "治理实时聚合"
                    "(观测面不受开关"
                    "影响)",
            "generatedAt": ts(),
        }
