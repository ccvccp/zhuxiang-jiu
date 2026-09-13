"""74号·NexusFlow(智枢·流)AI智能全域发布
大模型 P6 发布后复盘服务
(nexus74_p6_service)

规划(docs/74号_NexusFlow智枢流_AI智能全域
发布大模型_创新规划方案.md §六 P6):
    ① 单篇复盘(平台终态记录——published/
       rejected/throttled: 指标面+审核面+
       合规面+形式面四维合成 → 结论五态
       [effective/neutral/ineffective/
       blocked/pending_data]+确定性建议)
    ② 分组复盘(平台×意图聚合: 终态数/
       互动率均值/状态与审核分布/最优最差
       →组合策略三态+样本积累提示)
    ③ 复盘留痕列表(观测面)
    ④ 复盘字典公示(结论/建议/阈值域)

铁律(规划 §九):
    - 复盘=观测面(不受 MODE——分析常开;
      可重复执行——数据更新后重跑刷新)
    - LLM 禁入复盘判定链(结论=确定性
      阈值[复用 P4 互动率线]; 建议=模板
      拼接+数字插值——73号 hint 范式)
    - 非平台终态(shadowed/awaiting_manual/
      failed)不可复盘(409——未到达平台
      无复盘意义)
    - 结论优先级: 阻断(rejected/throttled)
      > 数据缺失 > 互动率线

异常约定(71号口径):
    KeyError → 404(发布不存在)
    ValueError → 409(状态机/域外)
"""

import logging

from core.helpers import ts

from repositories.nexus74_repository import (
    Nexus74Repository,
)
from services.nexus74_registry import (
    AUDIT_RESULTS,
    HIGH_ENGAGEMENT_LINE,
    INTENT_TYPES,
    LOW_ENGAGEMENT_LINE,
    PLATFORMS, PLATFORM_NAMES,
    RETRO_ADVICE_CODES,
    RETRO_GROUP_MIN_PUBLISHED,
    RETRO_SCOPES,
    RETRO_VERDICT_LABELS,
)

logger = logging.getLogger("nexus74_p6_service")

_TERMINAL_STATES = ("published", "rejected",
                   "throttled")


class Nexus74P6Service:
    """74号 P6 发布后复盘(单篇/分组——
    观测面常开)"""

    def __init__(self):
        self.repo = Nexus74Repository()

    # ============================================================
    # ① 单篇复盘(观测面——常开)
    # ============================================================

    async def retro_publication(
            self, publication_id: int) -> dict:
        """单篇发布复盘(四维合成——
        结论五态+建议)

        Raises:
            KeyError: 发布不存在
            ValueError: 非平台终态
        """
        pub = await self.repo.get_publication(
            publication_id)
        if not pub:
            raise KeyError(publication_id)
        status = pub.get("status")
        if status not in _TERMINAL_STATES:
            raise ValueError(
                "复盘对象须为平台终态("
                f"{'/'.join(_TERMINAL_STATES)})"
                f"——当前 {status}")

        adaptation = await self.repo \
            .get_adaptation(
                pub.get("adaptationId") or 0) \
            or {}
        metrics = await self.repo.get_metrics(
            publication_id)
        audits = [a for a in await
                  self.repo.list_audits(
                      limit=500)
                  if a.get("publicationId")
                  == publication_id]
        audit = audits[0] if audits else {}

        # --- 结论判定(确定性优先级:
        # 阻断 > 数据缺失 > 互动率线) ---
        blocked_kind = ""
        if status in ("rejected",
                      "throttled"):
            verdict = "blocked"
            blocked_kind = "receipt_" + status
        elif audit.get("result") in (
                "rejected", "throttled"):
            verdict = "blocked"
            blocked_kind = ("audit_"
                            + audit["result"])
        elif not metrics:
            verdict = "pending_data"
        else:
            rate = float(metrics.get(
                "engagementRate") or 0.0)
            if rate >= HIGH_ENGAGEMENT_LINE:
                verdict = "effective"
            elif rate < LOW_ENGAGEMENT_LINE:
                verdict = "ineffective"
            else:
                verdict = "neutral"
        rate = (float(metrics.get(
                     "engagementRate") or 0.0)
                if metrics else 0.0)

        advices = self._single_advices(
            verdict, blocked_kind, pub,
            audit, rate, publication_id)
        related = await self._related_learnings(
            pub, publication_id)

        retro_id = await self.repo.next_id(
            "retro")
        record = {
            "retroId": retro_id,
            "scope": "single",
            "publicationId": publication_id,
            "platform": pub["platform"],
            "platformName":
                pub["platformName"],
            "intent": pub.get("intent", ""),
            "intentLabel": pub.get(
                "intentLabel", ""),
            "verdict": verdict,
            "verdictLabel":
                RETRO_VERDICT_LABELS[verdict],
            "advices": advices,
            "engagementRate": rate,
            "detail": {
                "status": status,
                "title": adaptation.get(
                    "title", ""),
                "personaState":
                    adaptation.get(
                        "personaState", ""),
                "tags": adaptation.get(
                    "tags", []),
                "metrics": metrics or {},
                "audit": audit,
                "receipt": pub.get(
                    "receipt", {}),
                "complianceState":
                    adaptation.get(
                        "complianceState", ""),
                "warningInjected":
                    adaptation.get(
                        "warningInjected",
                        False),
                "blockedKind": blocked_kind,
                "relatedLearningIds":
                    related,
                "thresholds": {
                    "high":
                        HIGH_ENGAGEMENT_LINE,
                    "low":
                        LOW_ENGAGEMENT_LINE},
            },
            "createdAt": ts(),
        }
        await self.repo.save_retro(record)
        logger.info(
            "nexus74_retro single id=%s "
            "pub=%s verdict=%s rate=%s",
            retro_id, publication_id,
            verdict, rate)
        return record

    def _single_advices(
            self, verdict: str,
            blocked_kind: str,
            pub: dict, audit: dict,
            rate: float,
            publication_id: int) -> list[dict]:
        """单篇建议生成(确定性模板——
        LLM 禁入, 数字 100% 插值)"""
        high = HIGH_ENGAGEMENT_LINE
        low = LOW_ENGAGEMENT_LINE
        advices = []
        if verdict == "effective":
            advices.append({
                "code": "reinforce_form",
                "text": (
                    f"互动率 {rate} ≥ {high}"
                    "——强化该形式组合"
                    "(平台×意图×人设保持"
                    "标题/摘要/标签结构)")})
        elif verdict == "neutral":
            advices.append({
                "code": "keep_observing",
                "text": (
                    f"互动率 {rate} 处中性区间"
                    f"({low}–{high})——继续"
                    "积累样本观察")})
        elif verdict == "ineffective":
            advices.append({
                "code": "adjust_form_ab",
                "text": (
                    f"互动率 {rate} < {low}"
                    "——下一轮 A/B: 更换标题"
                    "钩子/切换人设状态/调整"
                    "标签组合")})
        elif verdict == "pending_data":
            advices.append({
                "code": "reflow_metrics",
                "text": (
                    "数据未回流——先 POST "
                    f"/metrics/{publication_id}"
                    " 登记 read/like/comment/"
                    "share")})
        else:  # blocked
            if "rejected" in blocked_kind:
                receipt = pub.get(
                    "receipt") or {}
                message = (
                    receipt.get("message")
                    or audit.get("message")
                    or "")
                advices.append({
                    "code":
                        "adjust_content_"
                        "direction",
                    "text": (
                        f"平台驳回({message})"
                        "——内容方向调整; "
                        "负样本已入 P4 学习"
                        "(连续驳回达线将提"
                        " 46号 规则强化)")})
            else:
                advices.append({
                    "code":
                        "adjust_timing_"
                        "frequency",
                    "text": (
                        "平台限流——调整发布"
                        "时机(静默窗)与频次"
                        "(每日封顶)策略")})
        return advices

    async def _related_learnings(
            self, pub: dict,
            publication_id: int) -> list[int]:
        """关联学习留痕(形式学习[平台]+
        负样本[本篇])"""
        learnings = await self.repo \
            .list_learnings(limit=200)
        ids = []
        for l in learnings:
            if l["kind"] == "form_learning" \
                    and l["platform"] \
                    == pub["platform"]:
                ids.append(l["learningId"])
            elif l["kind"] \
                    == "negative_feedback":
                detail = l.get("detail") or {}
                if detail.get(
                        "publicationId") \
                        == publication_id:
                    ids.append(
                        l["learningId"])
        return ids

    # ============================================================
    # ② 分组复盘(平台×意图——观测面)
    # ============================================================

    async def retro_group(
            self, platform: str = "",
            intent: str = "") -> dict:
        """分组复盘(聚合统计+组合策略
        三态——平台与意图至少其一)

        Raises:
            ValueError: 维度缺失/域外
        """
        if not platform and not intent:
            raise ValueError(
                "platform 与 intent 至少其一"
                "(分组维度)")
        if platform and platform \
                not in PLATFORMS:
            raise ValueError(
                f"平台域外({platform})")
        if intent and intent \
                not in INTENT_TYPES:
            raise ValueError(
                f"意图域外({intent})")

        pubs = [p for p in await
                self.repo.list_publications(
                    limit=500)
                if p.get("status")
                in _TERMINAL_STATES]
        if platform:
            pubs = [p for p in pubs
                    if p["platform"]
                    == platform]
        if intent:
            pubs = [p for p in pubs
                    if p.get("intent")
                    == intent]
        published = [p for p in pubs
                     if p["status"]
                     == "published"]
        metrics_all = await self.repo \
            .list_metrics(limit=500)
        m_by_pub = {
            m["publicationId"]: m
            for m in metrics_all}
        with_m = [
            (p, m_by_pub[p["publicationId"]])
            for p in published
            if p["publicationId"]
            in m_by_pub]
        rates = [float(m.get(
                       "engagementRate")
                       or 0.0)
                 for _, m in with_m]
        avg = (round(sum(rates)
                     / len(rates), 4)
               if rates else 0.0)

        audits = await self.repo \
            .list_audits(limit=500)
        if platform:
            audits = [a for a in audits
                      if a["platform"]
                      == platform]
        audit_dist = {
            r: sum(1 for a in audits
                   if a.get("result") == r)
            for r in AUDIT_RESULTS}
        status_dist = {
            s: sum(1 for p in pubs
                   if p["status"] == s)
            for s in _TERMINAL_STATES}

        # 最优/最差(按互动率——标题取适配)
        adaptations = {
            a["adaptationId"]: a
            for a in await self.repo
            .list_adaptations(limit=500)}

        def _brief(pair):
            p, m = pair
            a = adaptations.get(
                p.get("adaptationId")
                or 0) or {}
            return {
                "publicationId":
                    p["publicationId"],
                "title": a.get(
                    "title", ""),
                "engagementRate": float(
                    m.get(
                        "engagementRate")
                    or 0.0)}

        top = bottom = None
        if with_m:
            ranked = sorted(
                with_m,
                key=lambda pm: -float(
                    pm[1].get(
                        "engagementRate")
                    or 0.0))
            top = _brief(ranked[0])
            bottom = _brief(ranked[-1])

        # 分组结论(复用互动率线)
        if not with_m:
            verdict = "pending_data"
        elif avg >= HIGH_ENGAGEMENT_LINE:
            verdict = "effective"
        elif avg < LOW_ENGAGEMENT_LINE:
            verdict = "ineffective"
        else:
            verdict = "neutral"

        advices = self._group_advices(
            verdict, avg, len(with_m))

        retro_id = await self.repo.next_id(
            "retro")
        record = {
            "retroId": retro_id,
            "scope": "group",
            "publicationId": 0,
            "platform": platform or "all",
            "platformName": (
                PLATFORM_NAMES[platform]
                if platform else "全平台"),
            "intent": intent or "",
            "verdict": verdict,
            "verdictLabel":
                RETRO_VERDICT_LABELS[verdict],
            "advices": advices,
            "engagementRate": avg,
            "detail": {
                "terminalTotal": len(pubs),
                "publishedTotal":
                    len(published),
                "withMetrics":
                    len(with_m),
                "statusDistribution":
                    status_dist,
                "auditDistribution":
                    audit_dist,
                "topPerformer": top,
                "bottomPerformer":
                    bottom,
                "thresholds": {
                    "high":
                        HIGH_ENGAGEMENT_LINE,
                    "low":
                        LOW_ENGAGEMENT_LINE,
                    "groupMinPublished":
                        RETRO_GROUP_MIN_PUBLISHED},
            },
            "createdAt": ts(),
        }
        await self.repo.save_retro(record)
        logger.info(
            "nexus74_retro group id=%s "
            "platform=%s intent=%s "
            "verdict=%s avg=%s",
            retro_id, platform or "all",
            intent or "-", verdict, avg)
        return record

    def _group_advices(
            self, verdict: str, avg: float,
            samples: int) -> list[dict]:
        """分组策略建议(确定性模板)"""
        high = HIGH_ENGAGEMENT_LINE
        low = LOW_ENGAGEMENT_LINE
        advices = []
        if verdict == "effective":
            advices.append({
                "code":
                    "scale_up_combination",
                "text": (
                    f"组合平均互动率 {avg} "
                    f"≥ {high}——放大该组合"
                    "(平台×意图优先级提升)")})
        elif verdict == "ineffective":
            advices.append({
                "code":
                    "change_form_strategy",
                "text": (
                    f"组合平均互动率 {avg} "
                    f"< {low}——形式变革: "
                    "标题模板/人设状态/标签"
                    "结构整体调整")})
        elif verdict == "neutral":
            advices.append({
                "code":
                    "maintain_observation",
                "text": (
                    f"组合平均互动率 {avg} "
                    f"中性({low}–{high})"
                    "——维持当前策略继续"
                    "观察")})
        else:
            advices.append({
                "code": "reflow_metrics",
                "text": (
                    "已发布内容无指标回流"
                    "——先 POST /metrics/{id}"
                    " 登记")})
        if (verdict != "pending_data"
                and samples
                < RETRO_GROUP_MIN_PUBLISHED):
            advices.append({
                "code": "keep_observing",
                "text": (
                    f"指标样本 {samples} < "
                    f"{RETRO_GROUP_MIN_PUBLISHED}"
                    "——结论待样本积累")})
        return advices

    # ============================================================
    # ③④ 留痕列表+字典(观测面)
    # ============================================================

    async def list_retrospects(
            self, scope: str = "",
            limit: int = 100) -> list[dict]:
        """复盘留痕列表(观测面)

        Raises:
            ValueError: scope 域外
        """
        if scope and scope \
                not in RETRO_SCOPES:
            raise ValueError(
                f"复盘范围域外({scope})")
        return await self.repo \
            .list_retrospects(
                scope=scope or None,
                limit=limit)

    def retro_dict(self) -> dict:
        """复盘字典公示(结论/建议/阈值)"""
        return {
            "scopes": list(RETRO_SCOPES),
            "verdicts": dict(
                RETRO_VERDICT_LABELS),
            "adviceCodes": list(
                RETRO_ADVICE_CODES),
            "thresholds": {
                "highEngagement":
                    HIGH_ENGAGEMENT_LINE,
                "lowEngagement":
                    LOW_ENGAGEMENT_LINE,
                "groupMinPublished":
                    RETRO_GROUP_MIN_PUBLISHED,
            },
            "note": ("复盘=观测面常开; 结论="
                     "确定性阈值(复用 P4 "
                     "互动率线); 建议=模板"
                     "拼接+数字插值"
                     "(LLM 禁入)"),
        }
