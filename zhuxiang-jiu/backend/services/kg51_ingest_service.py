"""51号·小竹可信知识图谱 采集管道(kg51_ingest_service)

计划(docs/51号_小竹可信知识图谱实施计划.md §五 阶段2+3
/§八 P1):
    SOP 六阶流程之阶段2(数据采集)+阶段3(知识抽取)落地。

三源分级(计划 §五 阶段2):
    - 系统源(50号 voice50_events): 高置信自动入
      · settled 事件 → confidence 0.98 → verified
      · pending 事件 → confidence 0.85 → unverified
        + 复核队列(confidence)——结算后重采可升
    - 权威源(18号 published 条款 + 01号产品):
      confidence 1.0 → verified(直接可信)
    - 用户自报源(48号 voice48_turns, rawText 已脱敏):
      confidence 0.6 → unverified + 复核队列——
      低置信仅候选(计划红线), rawText 不入图谱属性

抽取映射(阶段3 ETL 字段级):
    事件 → VoiceBehaviorEvent 实体 + performed_by Member
           + attested_by Evidence(证据链) +
           L2/L3 contributes_to_credit(45号九因子);
           L1 不映射(50号红线——防法治域污染)
    条款 → PolicyClause 实体(仅 published)
    产品 → Product 实体
    轮次 → VoiceAnswer 候选实体(仅元数据, 不存话语)

幂等与去重(47号 scan 幂等教训 + tripleId uuid 教训):
    - 实体: entityId 自然键 upsert, 已存在跳过
    - 三元组: 指纹 sha256(subject|predicate|object)
      O(1) 查重; 命中且新置信度更优 → 更新, 否则跳过
    - 事件状态迁移(pending→settled): 重采时置信度
      0.85→0.98, 指纹命中走更新路径

冲突解决(计划 §五 阶段4, P1 先行落地单值谓词):
    权威源 > 系统源 > 用户自报源(SOURCE_PRIORITY);
    同优先级取最新时间戳; 冲突进复核队列(conflict)

unverified 物理隔离:
    status 字段隔离 + verified 视图查询过滤——
    不参与信值计算(P3 溯源只走 verified)

KG_MODE 默认 off: 采集面拒绝(off=采集停铁律)。
"""

import hashlib
import logging
import uuid

from core.helpers import ts

from repositories.agreement_repository import (
    AgreementRepository,
)
from repositories.kg51_repository import Kg51Repository
from repositories.product_repository import (
    ProductRepository,
)
from repositories.voice50_repository import (
    Voice50Repository,
)
from repositories.xiaozhu_repository import (
    Xiaozhu48Repository,
)
from services.kg51_ontology import (
    ONTOLOGY_REGISTRY, current_mode,
)
from services.trust_scoring_service import TrustValueScorer

logger = logging.getLogger("kg51_ingest_service")

# 置信度分级(计划 §五 阶段3: <0.9 进复核队列)
CONFIDENCE_AUTHORITY = 1.0
CONFIDENCE_SYSTEM_SETTLED = 0.98
CONFIDENCE_SYSTEM_PENDING = 0.85
CONFIDENCE_USER = 0.6
REVIEW_THRESHOLD = 0.9

# 单值谓词(冲突解决先行落地——maxCard=1 的函数性关系)
SINGLE_VALUED_PREDICATES = {"performed_by",
                            "contributes_to_credit"}


def _sha16(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")).hexdigest()[:16]


def member_digest(member_id: int) -> str:
    """会员 digest 标识(digest-only 铁律——49号范式)"""
    return _sha16(f"member:{member_id}")


def evidence_digest(ref: str) -> str:
    """证据 evSha(47号指纹范式)"""
    return _sha16(f"evidence:{ref}")


def triple_fingerprint(subject: str, predicate: str,
                       object_id: str) -> str:
    """三元组指纹(去重键——独立于 tripleId 主键,
    教训继承)"""
    return _sha16(
        f"{subject}|{predicate}|{object_id}")


class Kg51IngestService:
    """51号三源采集管道"""

    def __init__(self):
        self.repo = Kg51Repository()

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------

    async def run_ingest(self,
                         sources: list = None) -> dict:
        """触发采集抽取(sources 可选子集, 默认全三源)

        Raises:
            ValueError: KG_MODE=off(采集停铁律)/非法源
        """
        mode = current_mode()
        if mode != "on":
            raise ValueError(
                f"KG_MODE={mode}(默认 off——采集面关闭, "
                f"off=采集停铁律; 开启请置 KG_MODE=on)")
        valid = ("system", "authority", "user")
        if sources is None:
            sources = list(valid)
        for s in sources:
            if s not in valid:
                raise ValueError(
                    f"非法数据源: {s}(合法值: {'/'.join(valid)})")

        report = {"success": True, "mode": mode,
                  "sources": {}, "ts": ts()}
        if "system" in sources:
            report["sources"]["system"] = \
                await self._ingest_system_source()
        if "authority" in sources:
            report["sources"]["authority"] = \
                await self._ingest_authority_source()
        if "user" in sources:
            report["sources"]["user"] = \
                await self._ingest_user_source()
        # P3 溯源链路(45号 deposit Evidence+RepairAction
        # +verified_with 互证——随系统源联动采集)
        if "system" in sources:
            report["sources"]["trace"] = \
                await self._ingest_trace_links()
        logger.info("kg51_ingest_done %s",
                    {k: {kk: vv for kk, vv in v.items()
                         if kk != "errors"}
                     for k, v in report["sources"].items()})
        return report

    async def status(self) -> dict:
        """采集状态视图(实时统计——管理观测不受 off 影响)"""
        entities = await self.repo.list_entities(
            limit=100000)
        triples = await self.repo.list_triples(
            limit=100000)
        reviews = await self.repo.list_reviews(limit=100000)
        by_entity: dict = {}
        for e in entities:
            t = e.get("entityType") or "?"
            by_entity[t] = by_entity.get(t, 0) + 1
        by_status: dict = {}
        for t in triples:
            s = t.get("status") or "unverified"
            by_status[s] = by_status.get(s, 0) + 1
        by_source: dict = {}
        for t in triples:
            s = t.get("sourceType") or "?"
            by_source[s] = by_source.get(s, 0) + 1
        by_reason: dict = {}
        for r in reviews:
            q = r.get("queueReason") or "?"
            by_reason[q] = by_reason.get(q, 0) + 1
        return {
            "success": True,
            "mode": current_mode(),
            "entityCount": len(entities),
            "tripleCount": len(triples),
            "reviewCount": len(reviews),
            "entitiesByType": by_entity,
            "triplesByStatus": by_status,
            "triplesBySource": by_source,
            "reviewsByReason": by_reason,
            "reviewThreshold": REVIEW_THRESHOLD,
            "note": "unverified 三元组物理隔离——"
                    "不参与信值计算(P3 溯源仅走 verified)",
        }

    # --------------------------------------------------------
    # 系统源(50号 voice50_events)
    # --------------------------------------------------------

    async def _ingest_system_source(self) -> dict:
        """50号事件采集: 实体+四类三元组+证据链"""
        stat = {"scanned": 0, "entities": 0,
                "triples": 0, "skipped": 0,
                "updated": 0, "reviews": 0}
        events = await Voice50Repository().list_events(
            limit=100000)
        for ev in events:
            stat["scanned"] += 1
            ev_id = ev.get("evId")
            member_id = ev.get("memberId")
            behavior = ev.get("behavior") or ""
            if not ev_id or not member_id or not behavior:
                continue

            settled = (ev.get("status") == "settled")
            confidence = (CONFIDENCE_SYSTEM_SETTLED
                          if settled
                          else CONFIDENCE_SYSTEM_PENDING)
            ev_ref = ev.get("ref") or \
                f"voice50:{ev_id}"
            subject = f"ev:voice50:{ev_id}"
            evidence_id = \
                f"evid:sha256:{evidence_digest(ev_ref)}"

            # 实体: VoiceBehaviorEvent
            if await self._upsert_entity(
                    "VoiceBehaviorEvent", subject,
                    {"behaviorKey": behavior,
                     "layer": ev.get("layer") or "",
                     "value": ev.get("finalScore") or 0.0,
                     "status": ev.get("status") or ""},
                    "system", ev_ref, confidence,
                    stat):
                pass
            # 实体: Member(digest-only)
            m_id = f"member:sha256:{member_digest(member_id)}"
            await self._upsert_entity(
                "Member", m_id,
                {"digest": member_digest(member_id),
                 "trustTier": "",
                 "memberSinceDay":
                     (ev.get("ts") or "")[:10]},
                "system", ev_ref, confidence, stat)
            # 实体: Evidence
            await self._upsert_entity(
                "Evidence", evidence_id,
                {"evSha": evidence_digest(ev_ref),
                 "bcHash": "", "kind": "voice50_event",
                 "sourceRef": ev_ref},
                "system", ev_ref, confidence, stat)

            # 三元组: performed_by
            await self._upsert_triple(
                subject, "performed_by", m_id,
                "system", confidence,
                self._evidence_bundle(
                    ev_ref=ev_ref, ev_id=ev_id,
                    verifier=("settle" if settled
                              else "system")),
                stat)
            # 三元组: attested_by(证据链核心)
            await self._upsert_triple(
                subject, "attested_by", evidence_id,
                "system", confidence,
                self._evidence_bundle(
                    ev_ref=ev_ref, ev_id=ev_id,
                    verifier=("settle" if settled
                              else "system")),
                stat)
            # 三元组: contributes_to_credit(仅 L2/L3——
            # L1 不映射 45号因子, 50号红线)
            factor = ev.get("targetFactor")
            if ev.get("layer") in ("L2", "L3") and factor \
                    in TrustValueScorer.LAYER_OF:
                f_id = f"factor:trust45:{factor}"
                await self._upsert_entity(
                    "TrustFactor", f_id,
                    {"factorKey": factor,
                     "layer": TrustValueScorer
                     .LAYER_OF[factor],
                     "weight": ""},
                    "system", "trust45:registry",
                    CONFIDENCE_SYSTEM_SETTLED, stat)
                await self._upsert_triple(
                    subject, "contributes_to_credit",
                    f_id, "system", confidence,
                    self._evidence_bundle(
                        ev_ref=ev_ref, ev_id=ev_id,
                        verifier="system"),
                    stat)
        return stat

    # --------------------------------------------------------
    # 权威源(18号 published 条款 + 01号产品)
    # --------------------------------------------------------

    async def _ingest_authority_source(self) -> dict:
        """权威源: PolicyClause(仅 published) + Product"""
        stat = {"scanned": 0, "entities": 0,
                "triples": 0, "skipped": 0,
                "updated": 0, "reviews": 0}
        # 18号条款(status=published 才入——draft/reviewing
        # 不具备权威效力)
        agreements = await AgreementRepository(
        ).list_agreements(limit=1000)
        for agr in agreements:
            stat["scanned"] += 1
            if agr.get("status") != "published":
                continue
            clause_id = agr.get("id")
            if not clause_id:
                continue
            e_id = f"clause:agr18:{clause_id}"
            await self._upsert_entity(
                "PolicyClause", e_id,
                {"clauseId": str(clause_id),
                 "title": agr.get("name") or "",
                 "version": agr.get("currentVersion")
                 or ""},
                "authority",
                f"agreement:{clause_id}",
                CONFIDENCE_AUTHORITY, stat)
        # 01号产品(种子库)
        products = await ProductRepository().list_all()
        for prod in products:
            stat["scanned"] += 1
            pid = prod.get("product_id")
            if not pid:
                continue
            e_id = f"product:sku:{pid}"
            await self._upsert_entity(
                "Product", e_id,
                {"productId": pid,
                 "name": prod.get("name") or "",
                 "sku": pid},
                "authority", f"product:{pid}",
                CONFIDENCE_AUTHORITY, stat)
        return stat

    # --------------------------------------------------------
    # 用户自报源(48号 voice48_turns——低置信仅候选)
    # --------------------------------------------------------

    async def _ingest_user_source(self) -> dict:
        """48号轮次: VoiceAnswer 候选实体(rawText 不入
        图谱属性——隐私最小化)+复核队列"""
        stat = {"scanned": 0, "entities": 0,
                "triples": 0, "skipped": 0,
                "updated": 0, "reviews": 0}
        turns = await Xiaozhu48Repository().scan_turns(
            limit=10000)
        for turn in turns:
            stat["scanned"] += 1
            turn_id = turn.get("turnId")
            if not turn_id:
                continue
            # 有效意图才入候选(噪音过滤——not_woken/
            # asr_failed 不入图谱)
            intent = turn.get("intent") or ""
            if intent in ("not_woken", "asr_failed",
                          "general", ""):
                continue
            e_id = f"answer:voice48:{turn_id}"
            created = await self._upsert_entity(
                "VoiceAnswer", e_id,
                {"turnId": turn_id,
                 "intent": intent,
                 "confidence": CONFIDENCE_USER},
                "user",
                f"voice48:turn:{turn_id}",
                CONFIDENCE_USER, stat)
            # 新建候选 → 复核队列(confidence)
            if created:
                await self._enqueue_review(
                    e_id, "confidence", CONFIDENCE_USER,
                    f"用户源候选(意图 {intent})——"
                    f"低置信仅候选, 人工采信后方可参与"
                    f"grounding", stat)
        return stat

    # --------------------------------------------------------
    # P3 溯源链路(45号 deposit Evidence + RepairAction
    # + verified_with 互证)
    # --------------------------------------------------------

    async def _ingest_trace_links(self) -> dict:
        """45号留痕事件采集(溯源链路图锚定——只读跨表)

        - deposit 事件(source=deposit|deposit_rejected)
          → Evidence 实体(evid:sha256:trust45:{eventId})
        - repair 事件(source=repair|repair_rejected)
          → RepairAction 实体(repair:trust45:{repairId})
        - 互证对(47号 extract_mutual_pairs 实时计算)
          → 双方 deposit Evidence 的 verified_with
            三元组(对象按字典序规范化防重复)
        """
        stat = {"scanned": 0, "entities": 0,
                "triples": 0, "skipped": 0,
                "updated": 0, "reviews": 0}
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        repo45 = TrustValue45Repository()
        # 近 90 日 deposit(互证窗——47号同口径)
        deposits = await repo45.list_deposit_events(days=90)
        # 全量 repair 留痕(独立扫描——45号事件表)
        events45 = await self._scan_trust45_events(
            repo45)

        # ① deposit Evidence 实体(含 rejected 轨)
        dep_by_id = {int(d.get("eventId") or 0): d
                     for d in deposits}
        for ev in events45:
            stat["scanned"] += 1
            source = ev.get("source") or ""
            ev_id = int(ev.get("eventId") or 0)
            if ev_id <= 0:
                continue
            if source in ("deposit", "deposit_rejected"):
                e_id = (f"evid:sha256:"
                        f"{evidence_digest(
                            f'trust45:deposit:{ev_id}')}")
                await self._upsert_entity(
                    "Evidence", e_id,
                    {"evSha": evidence_digest(
                        f"trust45:deposit:{ev_id}"),
                     "bcHash": "",
                     "kind": f"trust45_{source}",
                     "sourceRef":
                         f"trust45:event:{ev_id}"},
                    "system", f"trust45:event:{ev_id}",
                    (CONFIDENCE_SYSTEM_SETTLED
                     if source == "deposit"
                     else CONFIDENCE_SYSTEM_PENDING),
                    stat)
            elif source in ("repair", "repair_rejected"):
                e_id = f"repair:trust45:{ev_id}"
                await self._upsert_entity(
                    "RepairAction", e_id,
                    {"repairId": str(ev_id),
                     "channel": "trust45_submit_repair",
                     "status": ("applied"
                                if source == "repair"
                                else "rejected")},
                    "system", f"trust45:event:{ev_id}",
                    CONFIDENCE_SYSTEM_SETTLED, stat)

        # ② verified_with 互证三元组(47号纯函数复用
        # ——零改动只调用)
        try:
            from services.trust_risk_collusion_service \
                import extract_mutual_pairs
            pairs = extract_mutual_pairs(deposits)
            for pair in pairs.get("pairs") or []:
                timeline = pair.get("timeline") or []
                # 双方各自最近一条存证事件 → Evidence
                by_depositor: dict = {}
                for entry in timeline:
                    dep_id = int(entry.get("eventId")
                                 or 0)
                    depositor = int(entry.get("depositor")
                                   or 0)
                    by_depositor[depositor] = dep_id
                if len(by_depositor) < 2:
                    continue
                ev_ids = sorted(by_depositor.values())
                e_a = (f"evid:sha256:"
                       f"{evidence_digest(
                           f'trust45:deposit:{ev_ids[0]}')}")
                e_b = (f"evid:sha256:"
                       f"{evidence_digest(
                           f'trust45:deposit:{ev_ids[1]}')}")
                await self._upsert_triple(
                    e_a, "verified_with", e_b,
                    "system", CONFIDENCE_SYSTEM_SETTLED,
                    self._evidence_bundle(
                        ev_ref=f"mutual:{pair.get('a')}"
                               f"-{pair.get('b')}",
                        verifier="collusion_scan"),
                    stat)
        except Exception as exc:  # noqa: BLE001
            # fail-soft: 互证计算异常不阻断采集主链
            logger.debug("kg51_mutual_skip: %s", exc)
        return stat

    async def _scan_trust45_events(
            self, repo45) -> list[dict]:
        """45号事件全量扫描(内存态直接遍历;
        Redis 态走 profiles→events_by_trust 聚合)"""
        try:
            profiles = await repo45.list_profiles(
                limit=5000)
            result: list = []
            for p in profiles:
                evs = await repo45.list_events_by_trust(
                    int(p.get("trustId") or 0))
                result.extend(evs)
            # 去重(同 eventId 只留一份)
            seen: dict = {}
            for e in result:
                seen[int(e.get("eventId") or 0)] = e
            return list(seen.values())
        except Exception:  # noqa: BLE001
            return []

    # --------------------------------------------------------
    # 抽取基建(upsert + 去重 + 冲突)
    # --------------------------------------------------------

    async def _upsert_entity(
            self, entity_type: str, entity_id: str,
            attrs: dict, source_type: str,
            source_ref: str, confidence: float,
            stat: dict) -> bool:
        """实体 upsert(entityId 自然键; 已存在跳过——
        首次写入为准)。返回是否新建。"""
        meta = (ONTOLOGY_REGISTRY["entities"]
                .get(entity_type) or {})
        # 属性白名单过滤(本体约束——禁入字段直接丢弃)
        allowed = set(meta.get("allowedAttrs") or [])
        filtered = {k: v for k, v in attrs.items()
                    if k in allowed}
        existing = await self.repo.get_entity(entity_id)
        if existing is not None:
            stat["skipped"] += 1
            return False
        record = {
            "entityId": entity_id,
            "entityType": entity_type,
            "label": meta.get("label") or entity_type,
            "attrs": filtered,
            "sourceType": source_type,
            "sourceRef": source_ref,
            "sensitivity": meta.get("sensitivity") or "L2",
            "confidence": confidence,
            "status": "active",
            "firstSeenAt": ts(),
        }
        await self.repo.save_entity(record)
        stat["entities"] += 1
        # 写事件联动缓存失效(P2 查询缓存——fail-soft)
        from services import kg51_query_cache
        kg51_query_cache.invalidate_all()
        return True

    async def _upsert_triple(
            self, subject: str, predicate: str,
            object_id: str, source_type: str,
            confidence: float,
            evidence: dict, stat: dict) -> None:
        """三元组 upsert(指纹查重; 置信度更优更新;
        单值谓词冲突按源优先级裁决)"""
        fp = triple_fingerprint(subject, predicate,
                               object_id)
        existing = await self.repo.find_triple_by_fp(fp)
        if existing is not None:
            old_conf = float(
                existing.get("confidence") or 0.0)
            if confidence > old_conf:
                # 置信度更优(如 pending→settled 重采)——
                # 更新证据链与置信度, 状态随置信重判
                await self.repo.update_triple_fields(
                    existing.get("tripleId"), {
                        "confidence": confidence,
                        "evidence": evidence,
                        "sourceType": source_type,
                        "status": ("verified"
                                   if confidence
                                   >= REVIEW_THRESHOLD
                                   else "unverified"),
                        "reviewedAt": "",
                    })
                stat["updated"] += 1
            else:
                stat["skipped"] += 1
            return

        # 单值谓词冲突检查(同 subject+predicate 异 object)
        if predicate in SINGLE_VALUED_PREDICATES:
            siblings = await self.repo.list_triples(
                predicate=predicate, subject=subject,
                limit=10)
            for sib in siblings:
                if sib.get("object") == object_id:
                    continue
                old_prio = _source_priority(
                    sib.get("sourceType"))
                new_prio = _source_priority(source_type)
                if new_prio > old_prio:
                    # 新源优先级高——旧三元组退役
                    await self.repo.update_triple_fields(
                        sib.get("tripleId"),
                        {"status": "retired"})
                else:
                    # 新源优先级低/同级——新三元组
                    # 退役留痕 + 冲突进复核
                    await self._enqueue_review(
                        f"{subject}|{predicate}"
                        f"|{object_id}",
                        "conflict", confidence,
                        f"单值谓词冲突: 既有 "
                        f"{sib.get('object')}"
                        f"({sib.get('sourceType')}) "
                        f"优先级不低于新值",
                        stat)
                    return

        status = ("verified"
                  if confidence >= REVIEW_THRESHOLD
                  else "unverified")
        triple_id = f"t-{uuid.uuid4().hex[:12]}"
        record = {
            "tripleId": triple_id,
            "subject": subject,
            "predicate": predicate,
            "object": object_id,
            "evidence": evidence,
            "sourceType": source_type,
            "sourceRef": evidence.get("sourceRef") or "",
            "confidence": confidence,
            "status": status,
            "version": 1,
            "createdAt": ts(),
            "reviewedAt": "",
        }
        await self.repo.save_triple(record)
        await self.repo.index_fp(fp, triple_id)
        stat["triples"] += 1
        # 写事件联动缓存失效(P2 查询缓存——fail-soft)
        from services import kg51_query_cache
        kg51_query_cache.invalidate_all()
        # 低置信三元组 → 复核队列(confidence)
        if confidence < REVIEW_THRESHOLD:
            await self._enqueue_review(
                f"{subject}|{predicate}|{object_id}",
                "confidence", confidence,
                f"置信度 {confidence} < {REVIEW_THRESHOLD}"
                f"——人工复核采信后转 verified",
                stat)

    @staticmethod
    def _evidence_bundle(**kwargs) -> dict:
        """证据链构造(SOP evidence_bundle 直译——
        无证据即 unverified, 计分路径只认 verified)"""
        bundle = {
            "verifier": kwargs.get("verifier") or "system",
            "ts": ts(),
        }
        if kwargs.get("ev_ref"):
            bundle["sourceRef"] = kwargs["ev_ref"]
        if kwargs.get("ev_id"):
            bundle["evId"] = kwargs["ev_id"]
        return bundle

    async def _enqueue_review(
            self, target: str, reason: str,
            confidence: float, note: str,
            stat: dict) -> None:
        """复核入队(同 target 不重复入队)"""
        pending = await self.repo.list_reviews(
            status="pending", limit=1000)
        for p in pending:
            if p.get("target") == target:
                return
        review_id = await self.repo.next_review_id()
        record = {
            "reviewId": review_id,
            "target": target,
            "queueReason": reason,
            "note": (note or "")[:200],
            "confidence": confidence,
            "status": "pending",
            "decidedBy": "",
            "decisionNote": "",
            "createdAt": ts(),
            "decidedAt": "",
        }
        await self.repo.save_review(record)
        stat["reviews"] += 1


def _source_priority(source_type: str) -> int:
    """源优先级(计划 §五 阶段4: 权威>系统>用户)"""
    return {"authority": 3, "system": 2,
            "user": 1}.get(source_type or "", 0)


class Kg51ReviewService:
    """51号复核队列裁决(采集质量闭环——SOP 人工审核)"""

    def __init__(self):
        self.repo = Kg51Repository()

    async def list_reviews(self, status: str = None,
                           reason: str = None) -> dict:
        """复核队列/历史(最新在前)"""
        reviews = await self.repo.list_reviews(
            status=status, reason=reason, limit=500)
        all_r = await self.repo.list_reviews(limit=1000)
        by_status = {"pending": 0, "approved": 0,
                     "rejected": 0}
        by_reason: dict = {}
        for r in all_r:
            s = r.get("status") or "pending"
            by_status[s] = by_status.get(s, 0) + 1
            q = r.get("queueReason") or "?"
            by_reason[q] = by_reason.get(q, 0) + 1
        return {"success": True, "total": len(reviews),
                "reviews": reviews,
                "byStatus": by_status,
                "byReason": by_reason}

    async def decide_review(self, review_id: int,
                            approve: bool,
                            decided_by: str = "admin",
                            decision_note: str = "") -> dict:
        """人工裁决(approve→目标转 verified; reject→retired)

        target 语义:
            三元组目标 "s|p|o" → 指纹定位三元组
            实体目标 entityId → 实体状态翻转

        Raises:
            KeyError: 复核不存在
            ValueError: 已裁决/目标不存在
        """
        review = await self.repo.get_review(review_id)
        if review is None:
            raise KeyError(f"复核 {review_id} 不存在")
        if review.get("status") != "pending":
            raise ValueError(
                f"复核已裁决({review.get('status')}), "
                f"不可重复裁决")

        target = review.get("target") or ""
        parts = target.split("|")
        flipped = None
        if len(parts) == 3:
            # 三元组目标(指纹定位)
            fp = triple_fingerprint(*parts)
            triple = await self.repo.find_triple_by_fp(fp)
            if triple is None:
                raise ValueError(
                    f"目标三元组不存在({target})——"
                    f"可能已被退役清理")
            new_status = ("verified" if approve
                          else "retired")
            await self.repo.update_triple_fields(
                triple.get("tripleId"), {
                    "status": new_status,
                    "reviewedAt": ts(),
                })
            flipped = {"tripleId":
                       triple.get("tripleId"),
                       "status": new_status}
        else:
            # 实体目标
            entity = await self.repo.get_entity(target)
            if entity is None:
                raise ValueError(
                    f"目标实体不存在({target})")
            new_status = ("active" if approve
                          else "retired")
            entity["status"] = new_status
            await self.repo.save_entity(entity)
            flipped = {"entityId": target,
                       "status": new_status}

        status = "approved" if approve else "rejected"
        await self.repo.update_review_fields(review_id, {
            "status": status,
            "decidedBy": decided_by,
            "decisionNote": (decision_note or "")[:500],
            "decidedAt": ts(),
        })
        logger.info("kg51_review_%s reviewId=%s target=%s",
                    status, review_id, target)
        return {"success": True, "reviewId": review_id,
                "status": status,
                "flipped": flipped,
                "note": ("已采信——目标转 verified/active"
                         if approve else
                         "已驳回——目标转 retired(留痕)")}

    async def query_triples(
            self, status: str = None, predicate: str = None,
            source_type: str = None,
            subject: str = None) -> dict:
        """三元组查询(管理观测; unverified 隔离口径——
        默认不过滤, 信值计算路径只取 verified)"""
        triples = await self.repo.list_triples(
            status=status, predicate=predicate,
            source_type=source_type, subject=subject,
            limit=2000)
        by_status = await self.repo.count_by_status()
        return {"success": True, "total": len(triples),
                "triples": triples,
                "byStatus": by_status,
                "note": "计分路径只取 verified——"
                        "unverified 物理隔离"}
