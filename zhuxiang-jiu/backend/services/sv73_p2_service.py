"""73号(sv)·短视频智能模型 P2——数据闭环服务(44号池)

职责: 发布效果回流(content published + 引流指标 → 44号
      sv73_storyboard 档案风格六因子反馈) + 学习状态观测
链路: content 发布(published+receipt) → 本服务 submit_feedback
      (factors=决策时风格快照, correct=clicks>0) → 44号
      ai_learning_feedback 池 → run_learning_cycle(≥10 条
      Hedge 乘性更新+护栏) → 风格权重冠军演化
范式: 36号 submit_learning_feedback 同款(promo_hotspot);
      幂等闸门 sv73.learningFed(单 content 终身一次)

变更链:
    - 2026-09-30 P2 立项(规划 §三: 数据闭环)
"""

from __future__ import annotations

import logging

from services.sv73_script_service import current_mode
from services.promo_service import PromoService

logger = logging.getLogger(__name__)


class Sv73P2Service:
    """73号 数据闭环(回流 44号 sv73_storyboard 档案)"""

    def __init__(self, promo: PromoService = None):
        self.promo = promo or PromoService()
        self.repo = self.promo.repo

    async def submit_learning_feedback(
            self, content_id: int, clicks: int = None,
            registrations: int = None,
            orders: int = None) -> dict:
        """发布效果回流(幂等: sv73.learningFed 单次闸门)

        correct 语义(36号同款): clicks>0 即该风格获引流。
        factors 从 content.sv73 决策时快照重建(确定性——
        风格六因子与 sv73_scorer.factors_from_content 同源)。
        """
        content = await self.repo.get_content(content_id)
        if content is None:
            raise KeyError(f"内容不存在(contentId={content_id})")
        sv = content.get("sv73") or {}
        if not sv.get("storyboardId"):
            raise ValueError(
                f"contentId={content_id} 非 73号 sv 产物"
                "(无 sv73 段——回流仅面向短视频内容)")
        if content.get("status") != "published":
            raise ValueError(
                f"内容未发布(当前 {content.get('status')})"
                "——回流仅面向已发布内容")
        if sv.get("learningFed"):
            raise ValueError("已回流过, 幂等不重复提交")

        # 引流指标: 缺省经 36号 attract 归因聚合(shortCode)
        if clicks is None:
            clicks = (await self.promo._link_metrics(
                [content.get("shortCode", "")])
            ).get("clicks", 0)
        clicks = int(clicks or 0)

        from services.sv73_scorer import factors_from_content
        factors = factors_from_content(sv)
        # 44号 factors 契约: contribution=weight×score 归一(:767-770)
        from services.ai_learning_service import (
            default_weights,
        )
        weights = default_weights("sv73_storyboard")
        for f in factors:
            weight = round(float(weights.get(f["name"], 0)), 4)
            f["weight"] = weight
            f["contribution"] = round(
                weight * float(f["score"]) / 100.0, 4)

        correct = clicks > 0
        from services.ai_learning_service import submit_feedback
        result = await submit_feedback({
            "scorerId": "sv73_storyboard",
            "factors": factors,
            "scoreAtDecision": round(
                float(content.get("complianceScore") or 0), 1),
            "actualAction": ("engage" if correct
                             else "no_traffic"),
            "correct": correct,
            "note": (f"contentId={content_id} clicks={clicks} "
                     f"platform={content.get('platform')} "
                     f"script={sv.get('storyboardId')}"),
            "source": "sv73",
        })

        # 幂等闸门 + 指标留痕(36号 learningFed 惯例)
        sv.update({
            "learningFed": True,
            "learningMetrics": {
                "clicks": clicks,
                "registrations": int(registrations or 0),
                "orders": int(orders or 0),
            },
            "learningFedAt": _now_iso(),
        })
        content["sv73"] = sv
        await self.repo.save_content(content)
        logger.info(
            "sv73_feedback_fed content=%s clicks=%s "
            "feedbackId=%s", content_id, clicks,
            result.get("feedbackId"))
        return {
            "success": True,
            "contentId": content_id,
            "clicks": clicks,
            "correct": correct,
            "feedbackId": result.get("feedbackId"),
            "factors": factors,
        }

    async def learning_status(self) -> dict:
        """学习状态观测(44号 sv73_storyboard 档案——只读)"""
        from services.ai_learning_service import (
            SCORER_REGISTRY, default_weights, is_learnable,
        )
        meta = SCORER_REGISTRY.get("sv73_storyboard") or {}
        weights = {}
        try:
            weights = default_weights("sv73_storyboard")
        except KeyError:
            pass
        champion = {}
        pending = 0
        try:
            from repositories.ai_learning_repository import (
                AiLearningRepository,
            )
            repo = AiLearningRepository()
            profile = await repo.get_profile(
                "sv73_storyboard") or {}
            champion = profile.get("weights") or {}
            pending = await repo.count_feedback(
                "sv73_storyboard", status="pending")
        except Exception:
            champion = {}
        return {
            "success": True,
            "scorerId": "sv73_storyboard",
            "registry": meta,
            "learnable": is_learnable("sv73_storyboard"),
            "defaultWeights": weights,
            "championWeights": champion,
            "pendingFeedback": pending,
            "mode": current_mode(),
            "note": "回流 POST /api/sv73/learning/feedback; "
                    "学习经 44号 run_learning_cycle"
                    "(≥10 条 Hedge 乘性更新, 护栏[1/2,2])",
        }


def _now_iso() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat(timespec="seconds")
