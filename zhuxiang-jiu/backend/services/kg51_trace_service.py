"""51号·小竹可信知识图谱 溯源服务(kg51_trace_service)

计划(docs/51号_小竹可信知识图谱实施计划.md §五 阶段4
+§八 P3):
    信值溯源 API——45号 deposit → 50号 settlement →
    50号 events → 证据链(attested_by triples)→
    48号 turn 全路径渲染; SOP"信值溯源"落地。

溯源链跨表拓扑(45/50/48号既有表——零改动只读):
    45号 trust45_events(source=deposit|deposit_rejected)
        ↑ depositId(=eventId)
    50号 voice50_settlement(batchId)
        ↑ settledBatchId
    50号 voice50_events(evId)
        ↑ subject= ev:voice50:{evId}
    51号 kg51_triples(attested_by → Evidence)
        ↑ sessionId/turnSeq 非零时
    48号 voice48_turns(语音来源轮次)

权限(计划 §七 会员/服务面):
    - trace/credit: 会员自查(X-Member-Id 只查自身)
    - trace/event/{evId}: 事件属主或 admin
    (他人事件 → 409 越权语义——P2 同款口径)

QC(计划 §八 P3):
    溯源完整率 100%——settled 事件任意 evId 可渲染
    证据链(settlement+deposit+evidence 三段齐备)。

互证关联(verified_with 视图):
    45号 deposit 事件 sources 含 "trust:{id}" 引用
    → 渲染互证对象; 图上 verified_with 三元组由
    采集侧(P3 _ingest_trace_links)落库, 本服务只读。

KG_MODE=off: 溯源只读跨表——不受数据面开关影响
(off 态亦可溯源, 图内三元组为空时链段降级 skipped)。
"""

import logging

from repositories.trust_value_repository import (
    TrustValue45Repository,
)
from repositories.voice50_repository import (
    Voice50Repository,
)
from repositories.xiaozhu_repository import (
    Xiaozhu48Repository,
)
from services.kg51_ingest_service import evidence_digest
from services.kg51_ontology import current_mode

logger = logging.getLogger("kg51_trace_service")

# 溯源渲染规模上限(防爆)
MAX_TRACE_DEPOSITS = 200
MAX_TRACE_EVENTS = 1000

# 45号事件 source 分类
DEPOSIT_SOURCES = ("deposit", "deposit_rejected")
REPAIR_SOURCES = ("repair", "repair_rejected")


def deposit_evidence_id(event_id: int) -> str:
    """45号 deposit 事件的 Evidence 实体标识
    (与采集侧 _ingest_trace_links 同口径)"""
    return (f"evid:sha256:"
            f"{evidence_digest(f'trust45:deposit:{event_id}')}")


class Kg51TraceService:
    """51号信值溯源(跨表只读渲染)"""

    def __init__(self):
        from repositories.kg51_repository import (
            Kg51Repository,
        )
        self.repo = Kg51Repository()

    # --------------------------------------------------------
    # 信值溯源路径(factor → deposit → settlement → events
    # → evidence → turn)
    # --------------------------------------------------------

    async def trace_credit(self, member_id: int) -> dict:
        """会员自查信值溯源路径(按 factor 分组)

        Raises:
            ValueError: 未绑定居值档案
        """
        binding = await Xiaozhu48Repository().get_binding(
            member_id)
        if binding is None:
            raise ValueError(
                "尚未绑定居值档案——无可溯源路径"
                "(409 语义)")
        trust_id = int(binding.get("trustId") or 0)

        # 45号 deposit 事件(验真入账+拒收双轨)
        events45 = await TrustValue45Repository(
        ).list_events_by_trust(trust_id)
        deposits = [e for e in events45
                    if (e.get("source") or "")
                    in DEPOSIT_SOURCES][:MAX_TRACE_DEPOSITS]

        # 50号结算批次与事件索引(memberId 过滤)
        settlements = await Voice50Repository(
        ).list_settlements(member_id=member_id, limit=500)
        by_deposit = {
            int(s.get("depositId") or 0): s
            for s in settlements
            if int(s.get("depositId") or 0) > 0}
        events50 = await Voice50Repository().list_events(
            member_id=member_id, limit=10000)
        by_batch: dict = {}
        for ev in events50:
            batch = int(ev.get("settledBatchId") or 0)
            if batch > 0:
                by_batch.setdefault(batch, []).append(ev)

        # 渲染: factor 分组
        factors: dict = {}
        completeness = {"deposits": len(deposits),
                       "withSettlement": 0,
                       "withEvents": 0,
                       "withEvidence": 0}
        for dep in deposits:
            factor = dep.get("factor") or "?"
            entry = {
                "deposit": self._deposit_view(dep),
                "settlement": None,
                "events": [],
            }
            settlement = by_deposit.get(
                int(dep.get("eventId") or 0))
            if settlement is not None:
                completeness["withSettlement"] += 1
                entry["settlement"] = \
                    self._settlement_view(settlement)
                evs = by_batch.get(
                    int(settlement.get("batchId") or 0)
                ) or []
                for ev in evs[:MAX_TRACE_EVENTS]:
                    chain = await self._event_chain(ev)
                    entry["events"].append(chain)
                    if chain["evidence"]:
                        completeness["withEvidence"] += 1
                if evs:
                    completeness["withEvents"] += 1
            factors.setdefault(factor, []).append(entry)

        # 完整率口径: 有 settlement 的 deposit 必须链到
        # events+evidence(100% QC)
        chained = completeness["withSettlement"]
        full = sum(
            1 for entries in factors.values()
            for e in entries
            if e["settlement"] is not None
            and e["events"]
            and all(x["evidence"]
                    for x in e["events"]))
        completeness["fullChain"] = full
        completeness["completeness"] = round(
            full / chained, 4) if chained else 1.0

        return {
            "success": True,
            "mode": current_mode(),
            "memberId": member_id,
            "trustId": trust_id,
            "factorCount": len(factors),
            "factors": factors,
            "completeness": completeness,
            "note": "溯源链: factor→deposit→settlement→"
                    "events→evidence(→turn)——计分路径"
                    "只渲染 verified 三元组",
        }

    # --------------------------------------------------------
    # 事件证据链渲染(单事件全路径)
    # --------------------------------------------------------

    async def trace_event(self, ev_id: int,
                          member_id: int = None,
                          admin: bool = False) -> dict:
        """事件证据链渲染(evId → settlement → deposit →
        证据链 → turn → 互证)

        Raises:
            KeyError: 事件不存在
            ValueError: 越权(非属主非 admin)
        """
        events = await Voice50Repository().list_events(
            limit=100000)
        ev = next((e for e in events
                   if int(e.get("evId") or 0)
                   == int(ev_id)), None)
        if ev is None:
            raise KeyError(f"事件 {ev_id} 不存在")
        owner = int(ev.get("memberId") or 0)
        if not admin and member_id is not None \
                and int(member_id) != owner:
            raise ValueError(
                "仅可溯源自身事件(他人事件越权 409 语义)")

        result = {
            "success": True,
            "mode": current_mode(),
            "event": self._event_view(ev),
            "settlement": None,
            "deposit": None,
            "evidence": None,
            "turn": None,
            "mutualAttestations": [],
            "note": "",
        }

        # settlement 段
        batch = int(ev.get("settledBatchId") or 0)
        if batch > 0:
            settlements = await Voice50Repository(
            ).list_settlements(limit=10000)
            settlement = next(
                (s for s in settlements
                 if int(s.get("batchId") or 0) == batch),
                None)
            if settlement is not None:
                result["settlement"] = \
                    self._settlement_view(settlement)
                # deposit 段(45号事件直查)
                dep_id = int(settlement.get("depositId")
                             or 0)
                if dep_id > 0:
                    dep = await self._find_event45(dep_id)
                    if dep is not None:
                        result["deposit"] = \
                            self._deposit_view(dep)
                        # 互证对象(45号 sources 引用)
                        result[
                            "mutualAttestations"] = [
                                ref for ref
                                in (dep.get("sources")
                                    or [])
                                if str(ref).startswith(
                                    "trust:")]

        # 证据链段(图内 verified attested_by)
        chain = await self._event_chain(ev)
        result["evidence"] = chain["evidence"]

        # turn 段(语音来源)
        session_id = int(ev.get("sessionId") or 0)
        if session_id > 0:
            turns = await Xiaozhu48Repository(
            ).list_turns(session_id, limit=200)
            seq = int(ev.get("turnSeq") or 0)
            turn = next((t for t in turns
                         if int(t.get("seq") or 0) == seq),
                        None)
            if turn is not None:
                # 脱敏视图(rawText 已 mask——隐私最小化)
                result["turn"] = {
                    "turnId": turn.get("turnId"),
                    "sessionId": session_id,
                    "seq": seq,
                    "channel": turn.get("channel"),
                    "intent": turn.get("intent"),
                    "rawTextMasked":
                        (turn.get("rawText") or "")[:60],
                    "ts": turn.get("ts"),
                }

        # 完整性判定(100% QC 的单事件口径)
        result["note"] = (
            "全链齐备" if result["settlement"]
            and result["deposit"] and result["evidence"]
            else "链段降级(未结算/图内无证据——"
                 "off 或未采集)")
        return result

    # --------------------------------------------------------
    # 内部渲染
    # --------------------------------------------------------

    async def _event_chain(self, ev: dict) -> dict:
        """单事件证据链(图内 attested_by verified
        triples + attested 实体)"""
        subject = (f"ev:voice50:"
                   f"{int(ev.get('evId') or 0)}")
        triples = await self.repo.list_triples(
            subject=subject, status="verified",
            predicate="attested_by", limit=10)
        evidence = []
        for t in triples:
            entity = await self.repo.get_entity(
                t.get("object") or "")
            evidence.append({
                "tripleId": t.get("tripleId"),
                "evidenceId": t.get("object"),
                "evSha": (entity or {}).get(
                    "attrs", {}).get("evSha")
                    if entity else None,
                "sourceRef": (t.get("evidence")
                              or {}).get("sourceRef"),
                "verifier": (t.get("evidence")
                             or {}).get("verifier"),
            })
        return {"event": self._event_view(ev),
                "evidence": evidence}

    async def _find_event45(self,
                           event_id: int) -> dict | None:
        """45号事件直查(跨表只读——deposit 近窗扫描)"""
        deps = await TrustValue45Repository(
        ).list_deposit_events(days=3650)
        return next((e for e in deps
                     if int(e.get("eventId") or 0)
                     == int(event_id)), None)

    @staticmethod
    def _event_view(ev: dict) -> dict:
        return {
            "evId": int(ev.get("evId") or 0),
            "memberId": int(ev.get("memberId") or 0),
            "behavior": ev.get("behavior"),
            "layer": ev.get("layer"),
            "targetFactor": ev.get("targetFactor"),
            "finalScore": float(
                ev.get("finalScore") or 0),
            "cappedScore": float(
                ev.get("cappedScore") or 0),
            "status": ev.get("status"),
            "settledBatchId": int(
                ev.get("settledBatchId") or 0),
            "ts": ev.get("ts"),
        }

    @staticmethod
    def _settlement_view(s: dict) -> dict:
        return {
            "batchId": int(s.get("batchId") or 0),
            "dayKey": s.get("dayKey"),
            "layer": s.get("layer"),
            "factor": s.get("factor"),
            "credits": float(s.get("credits") or 0),
            "eventCount": int(s.get("eventCount") or 0),
            "status": s.get("status"),
            "depositId": int(s.get("depositId") or 0),
            "depositVerified":
                str(s.get("depositVerified"))
                == "1" or s.get("depositVerified")
                is True,
            "depositDelta": float(
                s.get("depositDelta") or 0),
        }

    @staticmethod
    def _deposit_view(d: dict) -> dict:
        return {
            "depositId": int(d.get("eventId") or 0),
            "trustId": int(d.get("trustId") or 0),
            "layer": d.get("layer"),
            "factor": d.get("factor"),
            "delta": float(d.get("delta") or 0),
            "source": d.get("source"),
            "sources": d.get("sources") or [],
            "summary": (d.get("summary") or "")[:120],
            "evidenceId":
                deposit_evidence_id(
                    int(d.get("eventId") or 0)),
            "ts": d.get("ts"),
        }
