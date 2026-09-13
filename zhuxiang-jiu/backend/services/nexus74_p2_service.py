"""74号·NexusFlow(智枢·流)AI智能全域发布
大模型 P2 内容适配管线服务
(nexus74_p2_service)

规划(docs/74号_NexusFlow智枢流_AI智能全域
发布大模型_创新规划方案.md §三/§六 P2):
    ① 源内容登记(intent 域: 资讯/教程/
       种草/观点——感知分类)
    ② 平台选择矩阵(intent×平台适配分
       ——确定性查表, Top-N 建议)
    ③ 内容适配管线(单平台: 标题/摘要/
       标签/话术包——确定性模板拼接,
       零 LLM; 人设状态机驱动话术)
    ④ 多平台批量适配(矩阵驱动 Top-N)
    ⑤ 适配版本列表(观测面)

铁律(规划 §九):
    - 合规前置: 适配输出前必过六红线
      引擎; block/legal_risk 拒绝适配
      (409); R6 可修复自动警示语注入
    - LLM 禁入适配判定链(模板=确定性
      拼接+源字段插值——73号 hint 范式)
    - 适配面随 MODE 门控: off=409
      (决策面关闭); shadow=留痕不交付
      (delivered=False); assist/full=
      交付(delivered=True, assist 期
      含 needsReview 人工确认位)
    - 源内容登记/矩阵查询=观测面常开

异常约定(71号口径):
    KeyError → 404(源内容不存在)
    ValueError → 409(参数/域外/合规)
"""

import logging

from core.helpers import ts

from repositories.nexus74_repository import (
    Nexus74Repository,
)
from services.nexus74_registry import (
    CATEGORY_WORDS,
    DIGEST_LEN,
    EMOJI_SETS,
    EXCLAIM_MARK,
    HOOK_WORDS,
    INTENT_LABELS,
    INTENT_PLATFORM_SCORES,
    INTENT_TYPES,
    PERSONA_STATE_LABELS,
    PLATFORM_NAMES, PLATFORMS,
    PLATFORM_TOP_N,
    SUMMARY_TEMPLATES,
    TAG_BASES, TAG_LIMITS,
    TAG_PREFIX,
    TALKING_POINTS,
    TITLE_MAX_LEN,
    TITLE_TEMPLATES,
    WARNING_TEXT,
    current_mode,
)
from services.nexus74_p1_service import (
    Nexus74P1Service,
)

logger = logging.getLogger("nexus74_p2_service")


def _truncate(text: str, limit: int) -> str:
    """确定性截断(超限补省略号)"""
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)] + "…"


class Nexus74P2Service:
    """74号 P2 内容适配管线"""

    def __init__(self):
        self.repo = Nexus74Repository()
        self.p1 = Nexus74P1Service()

    # ============================================================
    # ① 源内容登记(观测面——常开)
    # ============================================================

    async def create_source(
            self, payload: dict) -> dict:
        """源内容登记(intent 感知分类)

        Raises:
            ValueError: intent 域外/字段空
        """
        intent = payload.get("intent")
        if intent not in INTENT_TYPES:
            raise ValueError(
                f"意图域外({intent}): "
                f"{'/'.join(INTENT_TYPES)}")
        title = (payload.get("title")
                 or "").strip()
        body = (payload.get("body")
                or "").strip()
        if not title:
            raise ValueError("标题不可为空")
        if not body:
            raise ValueError("正文不可为空")
        keywords = [k.strip() for k in
                    (payload.get("keywords")
                     or [])
                    if k and k.strip()]
        if len(keywords) > 10:
            raise ValueError(
                "关键词上限 10 个")
        source_id = await self.repo \
            .next_id("source")
        record = {
            "sourceId": source_id,
            "title": title,
            "body": body,
            "intent": intent,
            "intentLabel": INTENT_LABELS[
                intent],
            "keywords": keywords,
            "hasImage": bool(payload
                             .get("hasImage")),
            "hasVideo": bool(payload
                             .get("hasVideo")),
            "createdAt": ts(),
        }
        await self.repo.save_source(record)
        logger.info(
            "nexus74_source id=%s intent=%s",
            source_id, intent)
        return record

    async def get_source(
            self, source_id: int) -> dict:
        """源内容详情

        Raises:
            KeyError: 源内容不存在
        """
        source = await self.repo.get_source(
            source_id)
        if not source:
            raise KeyError(source_id)
        return source

    async def list_sources(
            self, limit: int = 50
    ) -> list[dict]:
        """源内容列表(观测面)"""
        return await self.repo.list_sources(
            limit=limit)

    # ============================================================
    # ② 平台选择矩阵(观测面——常开)
    # ============================================================

    def platform_matrix(
            self, intent: str = ""
    ) -> dict:
        """平台选择矩阵(intent×平台
        适配分——Top-N 建议降序)"""
        intents = ([intent]
                   if intent
                   in INTENT_TYPES
                   else list(INTENT_TYPES))
        matrix = {}
        for it in intents:
            scores = dict(
                INTENT_PLATFORM_SCORES[it])
            ranked = sorted(
                scores.items(),
                key=lambda kv: -kv[1])
            matrix[it] = {
                "intentLabel":
                    INTENT_LABELS[it],
                "scores": scores,
                "topN": [
                    {"platform": p,
                     "platformName":
                         PLATFORM_NAMES[p],
                     "score": s}
                    for p, s
                    in ranked[:PLATFORM_TOP_N]],
            }
        return {
            "topN": PLATFORM_TOP_N,
            "matrix": matrix,
            "note": ("矩阵=确定性查表"
                     "(意图×平台适配分); "
                     "LLM 禁入判定链"),
        }

    # ============================================================
    # ③ 内容适配管线(决策面——MODE 门控)
    # ============================================================

    async def adapt(
            self, source_id: int,
            platform: str,
            persona_state: str = ""
    ) -> dict:
        """单平台适配(标题/摘要/标签/
        话术包——确定性模板)

        流程: 合规前置(六红线)→
        R6 自动警示语注入→模板拼接→
        交付位随 MODE

        Raises:
            KeyError: 源内容不存在
            ValueError: 平台域外/
                人设域外/合规拒绝
        """
        source = await self.get_source(
            source_id)
        if platform not in PLATFORMS:
            raise ValueError(
                f"平台域外({platform})")
        if persona_state \
                and persona_state \
                not in TALKING_POINTS:
            raise ValueError(
                f"人设状态域外("
                f"{persona_state})")

        # --- 合规前置(六红线引擎) ---
        compliance = await \
            self.p1.compliance_check(
                text=source["title"]
                    + "\n"
                    + source["body"],
                platform=platform)
        if compliance["state"] \
                in ("block",
                    "legal_risk"):
            hit_labels = [
                h["label"]
                for h in
                compliance["hits"]]
            raise ValueError(
                "合规前置未过("
                f"{compliance['state']}"
                f": {hit_labels})"
                "——红线内容永不适配")
        warning_injected = False
        if (compliance["state"]
                == "review_required"
                and compliance["fixable"]):
            fix = await self.p1 \
                .warning_inject(
                    text=source["body"])
            source["body"] = \
                fix["injectedText"]
            warning_injected = True
            compliance = await \
                self.p1.compliance_check(
                    text=source["title"]
                        + "\n"
                        + source["body"],
                    platform=platform)
        needs_review = (
            compliance["state"]
            == "review_required")

        # --- 人设状态机 ---
        if persona_state:
            state = persona_state
        else:
            persona_info = await \
                self.p1.persona_state(
                    platform)
            state = persona_info["state"]
        points = TALKING_POINTS[state]

        # --- 标题(模板拼接+截断) ---
        title_tpl = TITLE_TEMPLATES[
            platform]
        raw_title = source["title"]
        emoji = ""
        if "{emoji}" in title_tpl:
            emojis = EMOJI_SETS[
                platform]
            if emojis:
                emoji = emojis[0]
        rendered_title = title_tpl.format(
            title=raw_title,
            hook=HOOK_WORDS[
                source["intent"]],
            emoji=emoji,
            exclaim=EXCLAIM_MARK,
            category=CATEGORY_WORDS[
                source["intent"]],
            keypoint=HOOK_WORDS[
                source["intent"]],
        )
        title = _truncate(
            rendered_title,
            TITLE_MAX_LEN[platform])

        # --- 摘要(digest 截取) ---
        body = source["body"]
        digest = _truncate(
            body.replace("\n", " "),
            DIGEST_LEN)
        summary = SUMMARY_TEMPLATES[
            platform].format(
            digest=digest)
        if warning_injected \
                and WARNING_TEXT \
                not in summary:
            summary += (
                f"\n\n——{WARNING_TEXT}——"
                )

        # --- 标签(关键词+意图基底) ---
        tag_limit = TAG_LIMITS[platform]
        tags = []
        if tag_limit > 0:
            prefix = TAG_PREFIX.get(
                platform, "")
            pool = (list(
                        source["keywords"])
                    + list(TAG_BASES[
                        source["intent"]]))
            seen = set()
            for kw in pool:
                if kw not in seen:
                    seen.add(kw)
                    tags.append(
                        prefix + kw)
                if len(tags) >= tag_limit:
                    break

        # --- 交付位(MODE 门控) ---
        mode = current_mode()
        delivered = mode in ("assist",
                             "full")
        shadow = mode == "shadow"

        adaptation_id = await self.repo \
            .next_id("adaptation")
        record = {
            "adaptationId":
                adaptation_id,
            "sourceId": source_id,
            "platform": platform,
            "platformName": PLATFORM_NAMES[
                platform],
            "intent": source["intent"],
            "intentLabel":
                source["intentLabel"],
            "personaState": state,
            "personaStateLabel":
                PERSONA_STATE_LABELS[
                    state],
            "title": title,
            "summary": summary,
            "tags": tags,
            "talkingPoints": {
                "opening":
                    points["opening"],
                "closing":
                    points["closing"],
                "interactionGuide":
                    "评论区互动：留言"
                    "分享你的看法。",
            },
            "compliance": compliance,
            "complianceState":
                compliance["state"],
            "needsReview": needs_review,
            "warningInjected":
                warning_injected,
            "delivered": delivered,
            "shadow": shadow,
            "mode": mode,
            "createdAt": ts(),
        }
        await self.repo.save_adaptation(
            record)
        logger.info(
            "nexus74_adapt id=%s src=%s "
            "platform=%s state=%s "
            "delivered=%s",
            adaptation_id, source_id,
            platform,
            compliance["state"],
            delivered)
        return record

    # ============================================================
    # ④ 多平台批量适配(矩阵驱动 Top-N)
    # ============================================================

    async def adapt_batch(
            self, source_id: int,
            persona_state: str = "",
            top_n: int = 0
    ) -> dict:
        """批量适配(矩阵 Top-N——
        按适配分降序)

        Raises:
            KeyError: 源内容不存在
            ValueError: top_n 域外
        """
        source = await self.get_source(
            source_id)
        if top_n and not (0 < top_n
                           <= len(PLATFORMS)):
            raise ValueError(
                f"top_n 域外({top_n})")
        n = top_n or PLATFORM_TOP_N
        scores = INTENT_PLATFORM_SCORES[
            source["intent"]]
        ranked = sorted(
            scores.items(),
            key=lambda kv: -kv[1])[:n]
        adaptations = []
        rejected = []
        for platform, score in ranked:
            try:
                record = await self.adapt(
                    source_id=source_id,
                    platform=platform,
                    persona_state=(
                        persona_state))
                record["matrixScore"] = \
                    score
                adaptations.append(
                    record)
            except ValueError as exc:
                rejected.append({
                    "platform": platform,
                    "score": score,
                    "reason": str(exc)})
        return {
            "sourceId": source_id,
            "intent": source["intent"],
            "intentLabel":
                source["intentLabel"],
            "selectedCount":
                len(adaptations),
            "adaptations":
                adaptations,
            "rejected": rejected,
            "note": ("矩阵驱动 Top-N"
                     "确定性降序"),
        }

    # ============================================================
    # ⑤ 适配版本列表(观测面)
    # ============================================================

    async def list_adaptations(
            self, source_id: int = 0
    ) -> list[dict]:
        """适配版本列表(观测面)"""
        if source_id:
            await self.get_source(
                source_id)
        return await self.repo \
            .list_adaptations(
                source_id=source_id
                or None,
                limit=200)
