"""58号·AI智能优化意图识别 四区看板
(ii58_dashboard_service, P5)

计划(docs/58号_AI智能优化意图识别算法模块实施计划.md
§九 P5):
    度量区: 任务完成率(resolved 占比)/纠错率
    (标注修正回流占比)/澄清接受率(partial
    转化占比——标注回流真值)/信值增益(池
    reward 加权和)
    意图区: 识别意图分布+top 意图+三态占比
    语料区: 四类样本+来源分布+人工验证率
    防御区: 越界拦截+对抗否决+红队状态+
    阈值来源健康

观测面——不受 II58_MODE 影响。
"""

import logging
import os

from core.helpers import ts

from repositories.ii58_repository import (
    Ii58Repository,
)

logger = logging.getLogger(
    "ii58_dashboard_service")

MODEL_VERSION = "v1-ii58-dashboard"


class Ii58DashboardService:
    """58号四区看板(P5)"""

    def __init__(self):
        self.repo = Ii58Repository()

    async def dashboard(self) -> dict:
        """四区看板(度量+意图+语料+防御)"""
        evaluations = await \
            self.repo.list_evaluations(
                limit=1000)
        corpus = await self.repo.list_corpus(
            limit=1000)
        labels = await self.repo.list_labels(
            limit=1000)

        # ---- 度量区 ----
        metrics = self._metrics(
            evaluations, labels)

        # ---- 意图区 ----
        intents = self._intents(evaluations)

        # ---- 语料区 ----
        corpus_zone = self._corpus_zone(corpus)

        # ---- 防御区 ----
        defense = await self._defense(
            evaluations)

        return {
            "success": True,
            "mode": os.environ.get(
                "II58_MODE", "off"),
            "metrics": metrics,
            "intents": intents,
            "corpus": corpus_zone,
            "defense": defense,
            "note": "四区看板——度量(完成率/纠错"
                    "率/澄清接受率/信值增益)+意图"
                    "+语料+防御",
            "generatedAt": ts(),
        }

    # ============================================================
    # 度量区
    # ============================================================

    @staticmethod
    def _metrics(evaluations: list,
                 labels: list) -> dict:
        total = len(evaluations)
        if total == 0:
            return {
                "total": 0,
                "taskCompletionRate": None,
                "correctionRate": None,
                "clarifyAcceptRate": None,
                "trustGain": 0.0,
                "note": "空态(无识别记录)",
            }
        resolved = sum(
            1 for e in evaluations
            if e.get("state") == "resolved")
        # 纠错率: approved 标注回流(修正)占
        # 识别总量
        approved = sum(
            1 for lb in labels
            if lb.get("status") == "approved")
        # 澄清接受率: partial 态经人工标注
        # 回流完成(真值转化)占 partial 总量
        partial = sum(
            1 for e in evaluations
            if e.get("state") == "partial")
        partial_pooled = sum(
            1 for e in evaluations
            if e.get("state") == "partial"
            and int(e.get("pooledFeedbackId")
                    or 0) > 0)
        # 信值增益: 池 reward 加权和
        trust_gain = round(sum(
            float(e.get("poolReward") or 0)
            for e in evaluations), 2)
        return {
            "total": total,
            "taskCompletionRate": round(
                resolved / total, 4),
            "correctionRate": round(
                approved / total, 4),
            "clarifyAcceptRate": round(
                partial_pooled / partial, 4)
            if partial else None,
            "trustGain": trust_gain,
            "note": "任务完成率=resolved 占比; "
                    "纠错率=标注回流/总量; 澄清"
                    "接受率=partial 转化占比; "
                    "信值增益=池 reward 和",
        }

    # ============================================================
    # 意图区
    # ============================================================

    @staticmethod
    def _intents(evaluations: list) -> dict:
        by_intent: dict = {}
        by_state: dict = {}
        for e in evaluations:
            iid = str(e.get("intentId")
                      or "unknown")
            by_intent[iid] = \
                by_intent.get(iid, 0) + 1
            st = str(e.get("state") or "unknown")
            by_state[st] = \
                by_state.get(st, 0) + 1
        top = sorted(
            by_intent.items(),
            key=lambda kv: -kv[1])[:5]
        return {
            "distinct": len(by_intent),
            "byState": by_state,
            "topIntents": [
                {"intentId": k, "count": v}
                for k, v in top],
            "note": "意图分布——识别记录聚合"
                    "(观测面纯读取)",
        }

    # ============================================================
    # 语料区
    # ============================================================

    @staticmethod
    def _corpus_zone(corpus: list) -> dict:
        by_type: dict = {}
        by_source: dict = {}
        verified = 0
        for c in corpus:
            st = str(c.get("status") or "")
            if st != "active":
                continue
            t = str(c.get("sampleType")
                    or "unknown")
            by_type[t] = by_type.get(t, 0) + 1
            src = str(c.get("source")
                      or "unknown")
            by_source[src] = \
                by_source.get(src, 0) + 1
            if c.get("humanVerified"):
                verified += 1
        active = sum(by_type.values())
        return {
            "active": active,
            "byType": by_type,
            "bySource": by_source,
            "humanVerifiedRate": round(
                verified / active, 4)
            if active else None,
            "note": "语料四类样本+来源分布"
                    "+人工验证率",
        }

    # ============================================================
    # 防御区
    # ============================================================

    async def _defense(self,
                       evaluations: list) -> dict:
        intercepted = sum(
            1 for e in evaluations
            if e.get("boundaryIntercepted"))
        adversarial = sum(
            1 for e in evaluations
            if (e.get("attribution") or {})
            .get("adversarialPenalty"))
        # 阈值来源健康(镜像 pending 积压告警)
        mirror = await self.repo.get_threshold(
            "baseline")
        mirror_status = (mirror or {}).get(
            "status") or "none"
        # 44号第33档案在册(宪法断言域)
        scorer_in_reg = False
        try:
            from services.ai_learning_service \
                import SCORER_REGISTRY
            scorer_in_reg = (
                "intent_orchestration"
                in SCORER_REGISTRY)
        except Exception:  # noqa: BLE001
            scorer_in_reg = False
        # 48/55号零改动(宪法断言域)
        cmd_actions = 0
        try:
            from services.xiaozhu_service \
                import COMMAND_ACTIONS
            cmd_actions = len(COMMAND_ACTIONS)
        except Exception:  # noqa: BLE001
            cmd_actions = 0
        return {
            "boundaryIntercepted": intercepted,
            "adversarialPenalized": adversarial,
            "thresholdMirror": mirror_status,
            "mirrorPendingAlert":
                mirror_status == "pending",
            "scorer33InRegistry": scorer_in_reg,
            "xiaozhuCommandActions": cmd_actions,
            "constitution": {
                "scorer33": scorer_in_reg,
                "xiaozhuZeroChange":
                    cmd_actions >= 15,
                "note": "44号33档案在册+48号"
                        "COMMAND_ACTIONS 零改动",
            },
            "note": "防御区——越界拦截+对抗"
                    "否决+阈值镜像健康+宪法"
                    "断言",
        }
