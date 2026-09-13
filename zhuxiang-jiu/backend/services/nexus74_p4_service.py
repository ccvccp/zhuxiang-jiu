"""74号·NexusFlow(智枢·流)AI智能全域发布
大模型 P4 数据回流与学习进化服务
(nexus74_p4_service)

规划(docs/74号_NexusFlow智枢流_AI智能全域
发布大模型_创新规划方案.md §三 ⑤/§六 P4):
    ① 指标回流登记(read/like/comment/
       share——仅 published 记录;
       互动率=(like+comment+share)/read)
    ② 形式学习(平台×意图分组——样本
       ≥3 且互动率越线: ≥15% 正样本/
       <3% 负样本留痕; 中性噪声不留痕)
    ③ 审核结果回流(passed/rejected/
       throttled——发布记录首次审核
       幂等; 驳回/限流→负样本学习)
    ④ 负反馈强化(同平台连续驳回/限流
       ≥2 → 规则强化提案提交 46号
       审批链[pending 人工签核——
       进化不自动生效铁律]; passed
       自然复位)
    ⑤ 全域数据汇总(平台聚合+审核
       分布——观测面)

铁律(规划 §九):
    - 数据回流=观测面(不受 MODE——
      学习进化常开)
    - 进化不自动生效: 规则强化提案
      走 46号 submit_change(kind=
      config, pending); LLM 禁入
      学习判定链(阈值公式确定性)
    - 46号互斥: 档案已有 pending 时
      新提案降级留痕(proposedChangeId=0
      + note), 不阻塞回流

异常约定(71号口径):
    KeyError → 404(发布/指标不存在)
    ValueError → 409(状态机/域外/阈值)
"""

import contextlib
import logging

from core.helpers import ts

from repositories.nexus74_repository import (
    Nexus74Repository,
)
from services.nexus74_registry import (
    AUDIT_RESULTS,
    FORM_LEARNING_MIN_SAMPLES,
    GOVERNANCE_SCORER_ID,
    HIGH_ENGAGEMENT_LINE,
    LEARNING_KINDS,
    LOW_ENGAGEMENT_LINE,
    METRIC_TYPES,
    NEGATIVE_TRIGGER_CONSECUTIVE,
    PLATFORMS, PLATFORM_NAMES,
    current_mode,
)

logger = logging.getLogger("nexus74_p4_service")


def _rate(m: dict) -> float:
    """互动率=(like+comment+share)/read
    (read=0 视为 1——防除零)"""
    reads = max(int(m.get("readCount")
                    or 0), 1)
    engaged = (int(m.get("likeCount")
                   or 0)
               + int(m.get("commentCount")
                     or 0)
               + int(m.get("shareCount")
                     or 0))
    return round(engaged / reads, 4)


class Nexus74P4Service:
    """74号 P4 数据回流与学习进化"""

    def __init__(self):
        self.repo = Nexus74Repository()

    # ============================================================
    # ① 指标回流登记(观测面——不受 MODE)
    # ============================================================

    async def register_metrics(
            self, publication_id: int,
            metrics: dict) -> dict:
        """指标回流登记(published 记录
        快照式更新+触发形式学习)

        Raises:
            KeyError: 发布记录不存在
            ValueError: 计数负值/状态机
        """
        pub = await self.repo \
            .get_publication(publication_id)
        if not pub:
            raise KeyError(publication_id)
        if pub.get("status") != "published":
            raise ValueError(
                "仅 published 记录可回流指标"
                f"(当前 {pub.get('status')})")
        counts = {}
        for t in METRIC_TYPES:
            key = f"{t}Count"
            v = metrics.get(key, 0)
            if not isinstance(v, int) \
                    or v < 0:
                raise ValueError(
                    f"{key} 须为非负整数")
            counts[key] = v
        record = {
            "publicationId": publication_id,
            "platform": pub["platform"],
            "platformName": pub[
                "platformName"],
            "intent": pub.get("intent",
                              ""),
            **counts,
            "engagementRate": _rate(counts),
            "mode": current_mode(),
            "updatedAt": ts(),
        }
        await self.repo.save_metrics(
            record)
        form_learning = await \
            self._evaluate_form_learning(
                pub["platform"],
                record["intent"])
        logger.info(
            "nexus74_metrics id=%s "
            "platform=%s rate=%s",
            publication_id,
            pub["platform"],
            record["engagementRate"])
        return {
            "metrics": record,
            "formLearning": form_learning,
        }

    # ============================================================
    # ② 形式学习(平台×意图——阈值公式)
    # ============================================================

    async def _evaluate_form_learning(
            self, platform: str,
            intent: str) -> dict | None:
        """形式学习评估(样本≥3 且互动率
        越线才留痕——中性噪声不记)"""
        pubs = await self.repo \
            .list_publications(
                platform=platform,
                status="published",
                limit=200)
        ids = {
            p["publicationId"]
            for p in pubs
            if p.get("intent") == intent}
        if len(ids) \
                < FORM_LEARNING_MIN_SAMPLES:
            return None
        all_metrics = await self.repo \
            .list_metrics(limit=500)
        samples = [m for m in all_metrics
                   if m["publicationId"]
                   in ids]
        if len(samples) \
                < FORM_LEARNING_MIN_SAMPLES:
            return None
        rates = [m.get("engagementRate")
                 or 0.0
                 for m in samples]
        avg = round(
            sum(rates) / len(rates), 4)
        if avg >= HIGH_ENGAGEMENT_LINE:
            verdict = "positive"
        elif avg < LOW_ENGAGEMENT_LINE:
            verdict = "negative"
        else:
            return None  # 中性——噪声
        learning_id = await self.repo \
            .next_id("learning")
        record = {
            "learningId": learning_id,
            "kind": "form_learning",
            "platform": platform,
            "intent": intent,
            "verdict": verdict,
            "detail": {
                "samples":
                    len(samples),
                "avgEngagement": avg,
                "highLine":
                    HIGH_ENGAGEMENT_LINE,
                "lowLine":
                    LOW_ENGAGEMENT_LINE,
                "publicationIds": sorted(
                    ids)[:20],
            },
            "proposedChangeId": 0,
            "at": ts(),
        }
        await self.repo.save_learning(
            record)
        logger.info(
            "nexus74_form_learning "
            "platform=%s intent=%s "
            "verdict=%s avg=%s",
            platform, intent,
            verdict, avg)
        return record

    # ============================================================
    # ③ 审核结果回流(负样本学习)
    # ============================================================

    async def audit_reflow(
            self, publication_id: int,
            result: str,
            message: str = "") -> dict:
        """审核结果回流(发布记录首次
        审核——幂等状态机; 驳回/限流→
        负样本+连续计数达线提 46号)

        Raises:
            KeyError: 发布记录不存在
            ValueError: 结果域外/状态机
        """
        if result not in AUDIT_RESULTS:
            raise ValueError(
                f"审核结果域外({result}): "
                f"{'/'.join(AUDIT_RESULTS)}")
        pub = await self.repo \
            .get_publication(publication_id)
        if not pub:
            raise KeyError(publication_id)
        if pub.get("status") != "published":
            raise ValueError(
                "仅 published 记录可回流审核"
                f"(当前 {pub.get('status')})")
        if pub.get("auditResult"):
            raise ValueError(
                "已登记审核结果("
                f"{pub.get('auditResult')})"
                "——勿重复")

        audit_id = await self.repo \
            .next_id("audit")
        audit = {
            "auditId": audit_id,
            "publicationId": publication_id,
            "platform": pub["platform"],
            "platformName": pub[
                "platformName"],
            "result": result,
            "message": message,
            "at": ts(),
        }
        await self.repo.save_audit(audit)
        pub["auditResult"] = result
        await self.repo.save_publication(
            pub)

        learning = None
        proposal = None
        if result in ("rejected",
                      "throttled"):
            learning = await \
                self._negative_learning(
                    audit)
            consecutive = await \
                self._consecutive_negative(
                    pub["platform"])
            if consecutive \
                    >= NEGATIVE_TRIGGER_CONSECUTIVE:
                proposal = await \
                    self._propose_rule_reinforce(
                        platform=pub["platform"],
                        consecutive=consecutive,
                        audit=audit)
        return {
            "audit": audit,
            "learning": learning,
            "proposal": proposal,
        }

    async def _negative_learning(
            self, audit: dict) -> dict:
        """负样本学习留痕"""
        learning_id = await self.repo \
            .next_id("learning")
        record = {
            "learningId": learning_id,
            "kind": "negative_feedback",
            "platform": audit["platform"],
            "intent": "",
            "verdict": "negative",
            "detail": {
                "result": audit["result"],
                "message":
                    audit["message"],
                "publicationId":
                    audit[
                        "publicationId"],
            },
            "proposedChangeId": 0,
            "at": ts(),
        }
        await self.repo.save_learning(
            record)
        return record

    async def _consecutive_negative(
            self, platform: str) -> int:
        """同平台连续驳回/限流计数
        (最新向旧——遇 passed 归零)"""
        audits = await self.repo \
            .list_audits(
                platform=platform,
                limit=500)
        audits.sort(
            key=lambda a: -int(
                a.get("auditId") or 0))
        count = 0
        for a in audits:
            if a.get("result") == "passed":
                break
            count += 1
        return count

    # ============================================================
    # ④ 规则强化提案(46号审批链——
    #    进化不自动生效铁律)
    # ============================================================

    async def _propose_rule_reinforce(
            self, platform: str,
            consecutive: int,
            audit: dict) -> dict:
        """规则强化提案(46号 submit_change
        kind=config——pending 人工签核;
        已有 pending 时降级留痕不阻塞)"""
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        gov = AiGovernanceService()
        change_id = 0
        note = ""
        payload = {
            "proposal": "rule_reinforce",
            "platform": platform,
            "consecutiveNegative":
                consecutive,
            "evidence": {
                "latestAuditId":
                    audit["auditId"],
                "latestResult":
                    audit["result"],
                "latestMessage":
                    audit["message"],
            },
            "suggestion": (
                "强化该平台合规词表与"
                "适配形式(连续驳回/"
                "限流达线)"),
        }
        try:
            with contextlib.suppress(Exception):
                await gov.sync_registry()
            result = await gov.submit_change(
                scorer_id=(
                    GOVERNANCE_SCORER_ID),
                kind="config",
                payload=payload,
                reason=(
                    f"{platform} 连续 "
                    f"{consecutive} 次驳回/"
                    f"限流——规则强化提案"
                    f"(74号 P4 负反馈学习)"))
            change_id = result.get(
                "changeId", 0)
            note = ("已提交 46号审批链"
                    "(pending 人工签核——"
                    "进化不自动生效铁律)")
        except ValueError as exc:
            note = (f"46号互斥降级: {exc}"
                    "(proposedChangeId=0"
                    "——提案待既有 pending "
                    "处置后重提)")
        learning_id = await self.repo \
            .next_id("learning")
        record = {
            "learningId": learning_id,
            "kind": "rule_reinforce",
            "platform": platform,
            "intent": "",
            "verdict": "proposal",
            "detail": {
                "consecutiveNegative":
                    consecutive,
                "payload": payload,
                "note": note,
            },
            "proposedChangeId": change_id,
            "at": ts(),
        }
        await self.repo.save_learning(
            record)
        logger.info(
            "nexus74_rule_reinforce "
            "platform=%s consecutive=%s "
            "changeId=%s",
            platform, consecutive,
            change_id)
        return record

    # ============================================================
    # ⑤ 全域数据汇总+学习留痕(观测面)
    # ============================================================

    async def metrics_summary(self) -> dict:
        """全域数据汇总(平台聚合+审核
        分布——观测面常开)"""
        pubs = await self.repo \
            .list_publications(limit=500)
        metrics = await self.repo \
            .list_metrics(limit=500)
        audits = await self.repo \
            .list_audits(limit=500)
        by_pub = {m["publicationId"]: m
                  for m in metrics}
        platforms = []
        for p in PLATFORMS:
            p_pubs = [x for x in pubs
                      if x["platform"]
                      == p
                      and x.get("status")
                      == "published"]
            p_mets = [by_pub[x[
                "publicationId"]]
                for x in p_pubs
                if x["publicationId"]
                in by_pub]
            tr = sum(m.get("readCount")
                     or 0 for m in p_mets)
            tl = sum(m.get("likeCount")
                     or 0 for m in p_mets)
            tc = sum(m.get("commentCount")
                     or 0 for m in p_mets)
            tsh = sum(m.get("shareCount")
                      or 0 for m in p_mets)
            avg = (round(
                       sum(m.get(
                           "engagementRate")
                           or 0.0
                           for m in p_mets)
                       / len(p_mets), 4)
                   if p_mets else 0.0)
            platforms.append({
                "platform": p,
                "platformName":
                    PLATFORM_NAMES[p],
                "published": len(p_pubs),
                "withMetrics":
                    len(p_mets),
                "totalRead": tr,
                "totalLike": tl,
                "totalComment": tc,
                "totalShare": tsh,
                "avgEngagement": avg,
            })
        audit_dist = {
            r: sum(1 for a in audits
                   if a.get("result") == r)
            for r in AUDIT_RESULTS}
        published_all = [x for x in pubs
                         if x.get("status")
                         == "published"]
        all_mets = [by_pub[x[
            "publicationId"]]
            for x in published_all
            if x["publicationId"]
            in by_pub]
        return {
            "mode": current_mode(),
            "global": {
                "publications":
                    len(pubs),
                "published":
                    len(published_all),
                "withMetrics":
                    len(all_mets),
                "avgEngagement": (
                    round(sum(
                              m.get(
                                  "engagementRate")
                              or 0.0
                              for m in
                              all_mets)
                          / len(all_mets),
                          4)
                    if all_mets else 0.0),
            },
            "auditDistribution":
                audit_dist,
            "platforms": platforms,
            "note": ("数据回流=观测面"
                     "(不受 MODE); 学习判定"
                     "=确定性阈值公式"),
        }

    async def learnings(
            self, kind: str = "",
            limit: int = 100
    ) -> list[dict]:
        """学习留痕列表(观测面)

        Raises:
            ValueError: 学习域外
        """
        if kind and kind \
                not in LEARNING_KINDS:
            raise ValueError(
                f"学习域外({kind}): "
                f"{'/'.join(LEARNING_KINDS)}")
        return await self.repo \
            .list_learnings(
                kind=kind or None,
                limit=limit)
