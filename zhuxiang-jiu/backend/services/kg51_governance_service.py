"""51号·小竹可信知识图谱 治理与演进(kg51_governance_service)

计划(docs/51号_小竹可信知识图谱实施计划.md §五 阶段6
/§八 P4):
    SOP 治理与演进落地——日度巡检+周度版本快照+
    公平桥→46号+48号纠错反馈→修订队列+Institution
    接入+看板。

巡检三指标(SOP 阶段6: 每日巡检三元组完整性/
一致性/时效性):
    - 完整性 completeness: attested_by minCard=1
      证据链违规数(0-1 评分)+ 实体悬挂(无三元组关联
      的 active 实体占比)
    - 一致性 consistency: 冲突复核积压(conflict
      pending 归一)+ 指纹索引一致性(fp 索引指向
      存在性)
    - 时效性 freshness: 最近采集距今时长(>48h 衰减)
      + unverified 存量占比

版本快照(SOP: 图谱按周发布版本号, 支持回溯查询):
    versionId/统计五元组/分组分布——只追加。

公平桥(50号 fairness-bridge 同范式):
    side-door 档案 "kg51_verified_coverage" 入册
    46号(28 档案断言零改动红线)→ 按 sourceType
    三组统计 verified 三元组占比 → MIN_GROUP_SAMPLES=5
    不足组不上报 → submit_samples(source=report)。

反馈闭环(SOP: "小竹"交互中的纠错反馈自动进入
修订队列):
    POST feedback(turnId+targetTriple) →
    kg51_feedback 台账 + reviews(reason=feedback)
    入队——admin 裁决走既有 /reviews/{id}/decide。

看板: 规模/verified 占比/复核积压/预算消耗/
    版本五分区。
"""

import logging
from datetime import UTC, datetime

from core.helpers import ts

from repositories.kg51_repository import Kg51Repository
from services import kg51_query_cache
from services.kg51_ontology import current_mode

logger = logging.getLogger("kg51_governance_service")

# 巡检阈值
STALE_HOURS_WARN = 48.0        # 时效性告警线
UNVERIFIED_RATIO_CAP = 0.5     # unverified 占比容忍

# 公平桥(46号最小采样口径——47号教训继承)
MIN_GROUP_SAMPLES = 5
FAIRNESS_SCORER_ID = "kg51_verified_coverage"


class Kg51GovernanceService:
    """51号治理与演进(巡检/版本/公平桥/反馈/看板)"""

    def __init__(self):
        self.repo = Kg51Repository()

    # --------------------------------------------------------
    # 日度巡检(三指标)
    # --------------------------------------------------------

    async def run_inspection(self) -> dict:
        """巡检执行(完整性/一致性/时效性三指标快照)"""
        entities = await self.repo.list_entities(
            limit=100000)
        triples = await self.repo.list_triples(
            limit=100000)
        reviews = await self.repo.list_reviews(
            status="pending", limit=100000)

        # ① 完整性: 证据链违规(事件类 subject 无
        # attested_by verified 三元组)+ 实体悬挂
        ev_subjects = {e.get("entityId") for e in
                       entities
                       if e.get("entityType")
                       == "VoiceBehaviorEvent"}
        attested_subjects = {
            t.get("subject") for t in triples
            if t.get("predicate") == "attested_by"
            and t.get("status") == "verified"}
        ev_total = len(ev_subjects) or 1
        missing_evidence = len(
            ev_subjects - attested_subjects)
        linked_ids = set()
        for t in triples:
            linked_ids.add(t.get("subject"))
            linked_ids.add(t.get("object"))
        active_entities = [e for e in entities
                          if e.get("status")
                          == "active"]
        dangling = sum(
            1 for e in active_entities
            if e.get("entityId") not in linked_ids)
        ent_total = len(active_entities) or 1
        completeness = round(max(0.0, 1.0
            - (missing_evidence / ev_total
               + dangling / ent_total) / 2), 4)

        # ② 一致性: 冲突积压 + 指纹索引有效性
        conflict_pending = sum(
            1 for r in reviews
            if r.get("queueReason") == "conflict")
        fp_ok, fp_total = 0, 0
        for t in triples[:500]:
            if t.get("predicate"):
                fp_total += 1
                probe = await self.repo.find_triple_by_fp(
                    _fp_of(t))
                if probe is not None \
                        and probe.get("tripleId") \
                        == t.get("tripleId"):
                    fp_ok += 1
        fp_ratio = (fp_ok / fp_total) if fp_total \
            else 1.0
        consistency = round(max(0.0, 1.0
            - conflict_pending / 20.0) * fp_ratio, 4)

        # ③ 时效性: 最近采集距今 + unverified 占比
        last_ingest_at = ""
        for t in triples:
            if t.get("createdAt", "") > last_ingest_at:
                last_ingest_at = t.get("createdAt")
        age_hours = 0.0
        if last_ingest_at:
            try:
                last_dt = datetime.fromisoformat(
                    last_ingest_at)
                now_dt = datetime.now(UTC)
                if last_dt.tzinfo is None:
                    from datetime import timezone
                    last_dt = last_dt.replace(
                        tzinfo=timezone.utc)
                age_hours = max(
                    0.0, (now_dt - last_dt
                          ).total_seconds() / 3600)
            except ValueError:
                age_hours = 0.0
        freshness_age = max(0.0, 1.0
            - age_hours / (STALE_HOURS_WARN * 2))
        unv = sum(1 for t in triples
                  if t.get("status") == "unverified")
        tri_total = len(triples) or 1
        unv_penalty = max(
            0.0, (unv / tri_total
                  - UNVERIFIED_RATIO_CAP))
        freshness = round(max(
            0.0, freshness_age - unv_penalty), 4)

        issues = []
        if missing_evidence:
            issues.append(
                f"completeness: {missing_evidence} 个"
                f"事件缺证据链(违规 unverified)")
        if dangling:
            issues.append(
                f"completeness: {dangling} 个 active"
                f"实体无三元组关联(悬挂)")
        if conflict_pending:
            issues.append(
                f"consistency: {conflict_pending} 条"
                f"冲突复核积压")
        if age_hours > STALE_HOURS_WARN:
            issues.append(
                f"freshness: 最近采集距今 "
                f"{round(age_hours, 1)}h(>48h)")
        if unv / tri_total > UNVERIFIED_RATIO_CAP:
            issues.append(
                f"freshness: unverified 占比 "
                f"{round(unv / tri_total, 2)} 超容忍")

        inspection_id = await \
            self.repo.next_inspection_id()
        record = {
            "inspectionId": inspection_id,
            "mode": current_mode(),
            "entityCount": len(entities),
            "tripleCount": len(triples),
            "completeness": completeness,
            "consistency": consistency,
            "freshness": freshness,
            "lastIngestAgeHours": round(age_hours, 2),
            "pendingConflicts": conflict_pending,
            "issues": issues,
            "createdAt": ts(),
        }
        await self.repo.save_inspection(record)
        logger.info("kg51_inspection id=%s "
                    "completeness=%s consistency=%s "
                    "freshness=%s issues=%d",
                    inspection_id, completeness,
                    consistency, freshness, len(issues))
        return record

    async def latest_inspection(self) -> dict:
        """最近一次巡检结果(无则触发一次)"""
        records = await self.repo.list_inspections(
            limit=1)
        if records:
            return {"success": True,
                    "inspection": records[0]}
        record = await self.run_inspection()
        return {"success": True, "inspection": record}

    # --------------------------------------------------------
    # 版本快照(周度版本, 回溯查询)
    # --------------------------------------------------------

    async def snapshot_version(self,
                               label: str = "") -> dict:
        """版本快照(只追加——统计五元组+分组分布)"""
        entities = await self.repo.list_entities(
            limit=100000)
        triples = await self.repo.list_triples(
            limit=100000)
        by_status: dict = {}
        for t in triples:
            s = t.get("status") or "unverified"
            by_status[s] = by_status.get(s, 0) + 1
        by_source: dict = {}
        for t in triples:
            s = t.get("sourceType") or "?"
            by_source[s] = by_source.get(s, 0) + 1
        by_entity: dict = {}
        for e in entities:
            k = e.get("entityType") or "?"
            by_entity[k] = by_entity.get(k, 0) + 1

        version_id = await self.repo.next_version_id()
        record = {
            "versionId": version_id,
            "versionLabel": (label or f"v{version_id}")
            [:50],
            "entityCount": len(entities),
            "tripleCount": len(triples),
            "verifiedCount": by_status.get(
                "verified", 0),
            "unverifiedCount": by_status.get(
                "unverified", 0),
            "retiredCount": by_status.get("retired", 0),
            "verifiedRatio": round(
                by_status.get("verified", 0)
                / len(triples), 4) if triples else 1.0,
            "bySourceType": by_source,
            "byEntityType": by_entity,
            "createdAt": ts(),
        }
        await self.repo.save_version(record)
        return {"success": True, "version": record}

    async def list_versions(self) -> dict:
        """版本列表(最新在前——回溯查询)"""
        versions = await self.repo.list_versions(
            limit=50)
        return {"success": True,
                "total": len(versions),
                "versions": versions}

    # --------------------------------------------------------
    # 公平桥(→46号, 50号 fairness-bridge 同范式)
    # --------------------------------------------------------

    async def bridge_fairness(self) -> dict:
        """图谱公平采样上报(SOP 阶段6 公平性审计——
        不同群体在图谱中的覆盖率与准确性差异)

        分组维度: 实体 sourceType 三组(authority/
        system/user); 指标: 组内实体平均 confidence
        ×100(准确性)——衡量不同数据源群体在图谱中
        的可信度分布。
        """
        entities = await self.repo.list_entities(
            status="active", limit=100000)
        buckets: dict = {"authority": [],
                         "system": [], "user": []}
        for e in entities:
            st = e.get("sourceType")
            if st in buckets:
                buckets[st].append(e)

        samples = []
        for group, items in sorted(buckets.items()):
            if len(items) < MIN_GROUP_SAMPLES:
                continue
            avg_conf = sum(
                float(e.get("confidence") or 0)
                for e in items) / len(items)
            samples.append({
                "group": group,
                "score": round(avg_conf * 100, 1),
                "passed": None,
            })
        if not samples:
            return {"success": True, "bridged": 0,
                    "groups": [],
                    "note": "各分组样本 "
                            f"<{MIN_GROUP_SAMPLES}"
                            "(46号最小采样口径)"
                            "——暂不上报"}

        # side-door 档案入册(46号 28 档案断言零改动)
        from core.helpers import ts as _ts
        from repositories.ai_governance_repository \
            import AiGovernance46Repository
        gov_repo = AiGovernance46Repository()
        gov = await gov_repo.get_gov(
            FAIRNESS_SCORER_ID)
        if gov is None:
            gov = {
                "govId": await gov_repo.next_gov_id(),
                "scorerId": FAIRNESS_SCORER_ID,
                "label": "图谱验证覆盖分布采样",
                "module": "51可信知识图谱",
                "batch": 14, "status": "active",
                "ownerNote": "51号公平性桥接专属档案"
                            "(side-door 入册)",
                "frozenAt": "", "frozenBy": "",
                "firstSeenAt": _ts(),
                "createdAt": _ts(),
                "lastSyncedAt": _ts(),
            }
        else:
            gov["status"] = "active"
            gov["lastSyncedAt"] = _ts()
        await gov_repo.save_gov(gov)

        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        result = await (
            AiGovernanceFairnessService()
        ).submit_samples(
            FAIRNESS_SCORER_ID, samples,
            source="report")
        return {"success": True,
                "bridged": result.get("accepted"),
                "groups": [s["group"]
                           for s in samples]}

    # --------------------------------------------------------
    # 纠错反馈(48号→修订队列)
    # --------------------------------------------------------

    async def submit_feedback(self, member_id: int,
                              turn_id: str,
                              target_triple: str,
                              note: str = "") -> dict:
        """纠错反馈(fromTurnId → targetTriple →
        reviews reason=feedback 修订队列)

        Raises:
            ValueError: 参数非法
        """
        turn_id = (turn_id or "").strip()
        target_triple = (target_triple or "").strip()
        if not turn_id or not turn_id.startswith("t-"):
            raise ValueError(
                "turnId 必填(t- 前缀——48号轮次标识)")
        if not target_triple \
                or len(target_triple.split("|")) != 3:
            raise ValueError(
                "targetTriple 必填(s|p|o 格式)")
        note = (note or "").strip()[:200]

        feedback_id = await self.repo.next_feedback_id()
        record = {
            "feedbackId": feedback_id,
            "memberId": member_id,
            "fromTurnId": turn_id,
            "targetTriple": target_triple,
            "note": note,
            "status": "pending",
            "reviewId": 0,
            "createdAt": ts(),
            "decidedAt": "",
        }
        await self.repo.save_feedback(record)

        # 修订队列入队(reviews reason=feedback——
        # 与 confidence/conflict 同一裁决通道)
        from services.kg51_ingest_service import (
            Kg51IngestService,
        )
        stat = {"reviews": 0}
        ingest = Kg51IngestService()
        await ingest._enqueue_review(
            target_triple, "feedback", 0.5,
            f"48号纠错反馈(turn {turn_id})"
            f"{'——' + note if note else ''}",
            stat)
        # 回填 reviewId(台账关联)
        pending = await self.repo.list_reviews(
            status="pending", reason="feedback",
            limit=500)
        for p in pending:
            if p.get("target") == target_triple:
                record["reviewId"] = int(
                    p.get("reviewId") or 0)
                break
        await self.repo.update_feedback_fields(
            feedback_id, {"reviewId":
                          record["reviewId"]})
        return {"success": True,
                "feedbackId": feedback_id,
                "reviewId": record["reviewId"],
                "status": "pending",
                "note": "反馈已入修订队列"
                        "(admin 裁决通道处理)"}

    async def list_feedback(self, status: str = None
                            ) -> dict:
        """反馈台账视图"""
        feedback = await self.repo.list_feedback(
            status=status, limit=200)
        all_f = await self.repo.list_feedback(
            limit=10000)
        by_status = {"pending": 0, "resolved": 0,
                     "rejected": 0}
        for f in all_f:
            s = f.get("status") or "pending"
            by_status[s] = by_status.get(s, 0) + 1
        return {"success": True,
                "total": len(feedback),
                "feedback": feedback,
                "byStatus": by_status}

    # --------------------------------------------------------
    # 看板(五分区)
    # --------------------------------------------------------

    async def dashboard(self) -> dict:
        """治理看板(规模/verified 占比/复核积压/
        预算消耗/版本)"""
        entities = await self.repo.list_entities(
            limit=100000)
        triples = await self.repo.list_triples(
            limit=100000)
        reviews = await self.repo.list_reviews(
            limit=100000)
        versions = await self.repo.list_versions(
            limit=5)
        inspections = await self.repo.list_inspections(
            limit=1)

        by_status: dict = {}
        for t in triples:
            s = t.get("status") or "unverified"
            by_status[s] = by_status.get(s, 0) + 1
        verified = by_status.get("verified", 0)
        pending_reviews = sum(
            1 for r in reviews
            if r.get("status") == "pending")
        by_reason: dict = {}
        for r in reviews:
            if r.get("status") == "pending":
                q = r.get("queueReason") or "?"
                by_reason[q] = by_reason.get(q, 0) + 1

        # 预算消耗分区(49号表只读直扫——trace 同范式
        # fail-soft)
        budget = await self._budget_zone()

        return {
            "success": True,
            "mode": current_mode(),
            "scale": {
                "entityCount": len(entities),
                "tripleCount": len(triples),
                "reviewCount": len(reviews),
                "feedbackCount": len(
                    await self.repo.list_feedback(
                        limit=100000)),
            },
            "verified": {
                "verified": verified,
                "unverified": by_status.get(
                    "unverified", 0),
                "retired": by_status.get("retired", 0),
                "verifiedRatio": round(
                    verified / len(triples), 4)
                if triples else 1.0,
            },
            "reviewBacklog": {
                "pending": pending_reviews,
                "byReason": by_reason,
            },
            "budget": budget,
            "versions": versions,
            "lastInspection":
                inspections[0] if inspections else None,
            "cache": kg51_query_cache.cache_stats(),
        }

    async def _budget_zone(self) -> dict:
        """预算消耗分区(49号 voice48_privacy_budget
        只读直扫——内存直读/Redis keys 扫描, fail-soft)"""
        try:
            from repositories.backend import (
                is_redis_mode, get_redis_client, _k,
            )
            from repositories.xiaozhu_repository import (
                Xiaozhu48Repository,
            )
            xrepo = Xiaozhu48Repository()
            accounts = []
            if is_redis_mode():
                client = await get_redis_client()
                keys = await client.keys(_k(
                    "voice48", xrepo.TABLE_PRIVACY, "*"))
                for i in range(0, len(keys), 500):
                    pipe = client.pipeline(
                        transaction=False)
                    for k in keys[i:i + 500]:
                        pipe.hgetall(k)
                    for data in await pipe.execute():
                        if data:
                            accounts.append(data)
            else:
                xrepo._ensure_store()
                accounts = list(
                    xrepo.store.get(
                        xrepo.TABLE_PRIVACY,
                        {}).values())
            if not accounts:
                return {"accounts": 0,
                        "note": "无预算账户"}
            used = [float(a.get("usedToday") or 0)
                    for a in accounts]
            exhausted = 0
            for a in accounts:
                limit = float(
                    a.get("dailyBudget") or 1.0) * \
                    float(a.get("preference") or 1.0)
                if float(a.get("usedToday") or 0) \
                        >= limit:
                    exhausted += 1
            return {
                "accounts": len(accounts),
                "avgUsedToday": round(
                    sum(used) / len(used), 2),
                "exhaustedToday": exhausted,
                "note": "49号隐私预算只读聚合"
                        "(与信值等级零挂钩)",
            }
        except Exception as exc:  # noqa: BLE001
            return {"accounts": 0,
                    "note": f"预算分区降级: {exc}"}


def _fp_of(triple: dict) -> str:
    """三元组指纹(巡检一致性探针)"""
    from services.kg51_ingest_service import (
        triple_fingerprint,
    )
    return triple_fingerprint(
        triple.get("subject") or "",
        triple.get("predicate") or "",
        triple.get("object") or "")
