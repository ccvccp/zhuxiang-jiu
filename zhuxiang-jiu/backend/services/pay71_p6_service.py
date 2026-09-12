"""71号·AI智能支付端口大模型 审计透明服务
(pay71_p6_service, P6)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§七 P6 / §4.6):
    ① 决策链路图(治理/自愈/调配/核验/
       叙事五域——结构化只读导出)
    ② 监管证据链(按 payId/时间段聚合:
       意图→调配→熵→核验→叙事全链
       证据, sha256 哈希锚定防篡改)
    ③ 合规健康报告(五维确定性统计
       ——端口合规/自愈动作/对账结果/
       叙事判定/红线触碰恒 0)
    ④ 证据链导出(exported 留痕——
       只读, 永不修改原始留痕)

铁律(规划 §4.6/§1.2):
    - 证据链只读导出(永不修改原始
      留痕——组装副本+哈希锚定)
    - LLM 仅归因文案; 报告数字确定性
      生成(可复现公式)
    - 46号审批台账为唯一治理事实源
      (71号只读消费)
"""

import hashlib
import json
import logging

from core.helpers import ts

from repositories.pay71_repository import (
    Pay71Repository,
)
from services.pay71_registry import (
    EVIDENCE_HASH_FIELDS,
    EVIDENCE_NODES,
    REPORT_DIMENSIONS,
    REPORT_PERIODS,
    TRACEGRAPH_DOMAINS,
    MODEL_VERSION, current_mode,
)

logger = logging.getLogger("pay71_p6_service")


class Pay71P6Service:
    """71号审计透明(P6)"""

    def __init__(self):
        self.repo = Pay71Repository()

    # ============================================================
    # ① 决策链路图(五域只读导出)
    # ============================================================

    async def tracegraph(self) -> dict:
        """决策链路图(治理/自愈/调配/
        核验/叙事五域——结构化只读
        导出; 每域含节点计数+代表链)"""
        shadows = await \
            self.repo.list_shadows()
        proposals = await \
            self.repo.list_proposals(limit=200)
        external = await \
            self.repo.list_external(limit=200)
        traces = await \
            self.repo.list_traces(limit=200)
        allocations = await \
            self.repo.list_allocations(
                limit=200)
        verifies = await \
            self.repo.list_verifies(limit=200)
        recons = await \
            self.repo.list_recons(limit=200)
        narratives = await \
            self.repo.list_narratives(
                limit=200)
        graph = {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "domains": {
                "governance": {
                    "label":
                        "治理(纳管/建议书"
                        "/外部信号)",
                    "onboardedPorts":
                        len(shadows),
                    "proposals":
                        len(proposals),
                    "externalSignals":
                        len(external),
                    "sampleProposal": (
                        proposals[0]
                        if proposals
                        else None),
                },
                "selfheal": {
                    "label":
                        "自愈(端口态/轨迹)",
                    "transitionCount":
                        len(traces),
                    "actionCounts": {
                        a: sum(
                            1 for t in traces
                            if t.get("action")
                            == a)
                        for a in {
                            t.get("action")
                            for t in traces}
                    },
                    "sampleTrace": (
                        traces[0]
                        if traces
                        else None),
                },
                "allocation": {
                    "label":
                        "调配(四维/帕累托)",
                    "allocationCount":
                        len(allocations),
                    "contextCounts": {
                        c: sum(
                            1 for a
                            in allocations
                            if a.get(
                                "context")
                            == c)
                        for c in {
                            a.get("context")
                            for a in
                            allocations}
                    },
                    "sampleAllocation": (
                        allocations[0]
                        if allocations
                        else None),
                },
                "recon": {
                    "label":
                        "核验(三向/补单)",
                    "verifyCount":
                        len(verifies),
                    "matchedCount": sum(
                        1 for v in verifies
                        if v.get("state")
                        == "matched"),
                    "reconCount":
                        len(recons),
                    "sampleVerify": (
                        verifies[0]
                        if verifies
                        else None),
                },
                "narrative": {
                    "label":
                        "叙事(归因/案例)",
                    "narrativeCount":
                        len(narratives),
                    "verdictCounts": {
                        v: sum(
                            1 for n
                            in narratives
                            if (n.get(
                                "attribution"
                            ) or {}).get(
                                "causalVerdict")
                            == v)
                        for v in {
                            (n.get(
                                "attribution")
                             or {}).get(
                                "causalVerdict")
                            for n in
                            narratives}
                    },
                    "sampleNarrative": (
                        narratives[0]
                        if narratives
                        else None),
                },
            },
            "domainOrder": list(
                TRACEGRAPH_DOMAINS),
            "exportedAt": ts(),
        }
        return graph

    # ============================================================
    # ② 监管证据链(哈希锚定)
    # ============================================================

    @staticmethod
    def _node_hash(node: str,
                   record: dict) -> str:
        """节点哈希(锚字段确定性序列化
        ——sha256 防篡改)"""
        fields = EVIDENCE_HASH_FIELDS[
            node]
        payload = {f: record.get(f)
                   for f in fields}
        canonical = json.dumps(
            payload, sort_keys=True,
            ensure_ascii=False)
        return hashlib.sha256(
            canonical.encode()
        ).hexdigest()

    async def assemble_evidence(
            self, member_id: int,
            limit: int = 20) -> dict:
        """证据链组装(按会员聚合: 意图→
        调配→熵→核验→叙事; 哈希锚定
        ——组装副本, 永不改原始留痕)

        Raises:
            ValueError: 会员无证据链
                (无任何节点留痕)
        """
        member_id = int(member_id or 0)
        predictions = await \
            self.repo.list_predictions(
                member_id=member_id,
                limit=limit)
        allocations = [
            a for a in await
            self.repo.list_allocations(
                limit=200)
            if a.get("memberId")
            == member_id][:limit]
        # 69号熵留痕只读消费
        from repositories.pay69_repository import (
            Pay69Repository,
        )
        entropies = await \
            Pay69Repository().list_entropy(
                member_id=member_id,
                limit=limit)
        verifies = [
            v for v in await
            self.repo.list_verifies(
                limit=200)][:limit]
        narratives = await \
            self.repo.list_narratives(
                member_id=member_id,
                limit=limit)
        nodes = {
            "intent": predictions,
            "allocation": allocations,
            "entropy": entropies,
            "verify": verifies,
            "narrative": narratives,
        }
        # 空链校验: 会员专属节点
        # (intent/narrative)至少一非空
        # ——verify 为全站级节点
        # (核验无会员维度), 不作
        # 会员归属判据
        if not (nodes["intent"]
                or nodes["narrative"]):
            raise ValueError(
                f"会员 {member_id} 无证据"
                f"链节点(无任何留痕)")
        # 节点哈希(每记录锚字段)
        node_hashes = {}
        for node in EVIDENCE_NODES:
            node_hashes[node] = [
                self._node_hash(
                    node, rec)
                for rec in nodes[node]]
        # 链哈希(节点哈希串联——顺序
        # 按 EVIDENCE_NODES 确定性)
        chain_payload = "|".join(
            "-".join(hs)
            for hs in (
                node_hashes[n]
                for n in EVIDENCE_NODES))
        chain_hash = hashlib.sha256(
            chain_payload.encode()
        ).hexdigest()
        record = {
            "memberId": member_id,
            "state": "assembled",
            "nodes": {
                n: len(nodes[n])
                for n in EVIDENCE_NODES},
            "nodeHashes": node_hashes,
            "chainHash": chain_hash,
            "nodeOrder": list(
                EVIDENCE_NODES),
            "readonly": True,
            "assembledAt": ts(),
            "exportedAt": "",
        }
        await self.repo.save_evidence(
            record)
        await self.repo.save_event({
            "type": "evidence_assembled",
            "memberId": member_id,
            "detail": {
                "chainHash":
                    chain_hash[:12],
                "nodes": record["nodes"],
            },
            "at": ts(),
        })
        return record

    async def export_evidence(
            self, evidence_seq: int
            ) -> dict:
        """证据链导出(监管问询——
        exported 留痕; 只读, 永不修改
        原始留痕)

        Raises:
            KeyError: 证据链不存在
            ValueError: 已导出
        """
        record = await \
            self.repo.get_evidence(
                int(evidence_seq))
        if record is None:
            raise KeyError(
                f"证据链不存在("
                f"evidenceSeq="
                f"{evidence_seq})")
        if record.get("state") != \
                "assembled":
            raise ValueError(
                f"证据链已导出(state="
                f"{record.get('state')})")
        record["state"] = "exported"
        record["exportedAt"] = ts()
        await self.repo.update_evidence(
            int(evidence_seq), record)
        await self.repo.save_event({
            "type": "evidence_exported",
            "memberId": record.get(
                "memberId", 0),
            "detail": {
                "evidenceSeq":
                    int(evidence_seq),
            },
            "at": ts(),
        })
        return record

    async def verify_chain(
            self, evidence_seq: int
            ) -> dict:
        """证据链哈希校验(篡改可检出
        ——重组节点哈希对比 chainHash)

        Raises:
            KeyError: 证据链不存在
        """
        record = await \
            self.repo.get_evidence(
                int(evidence_seq))
        if record is None:
            raise KeyError(
                f"证据链不存在("
                f"evidenceSeq="
                f"{evidence_seq})")
        stored = record.get("chainHash")
        # 重组校验(节点哈希串联)
        rebuilt_payload = "|".join(
            "-".join(hs)
            for hs in (
                record["nodeHashes"][n]
                for n in
                record["nodeOrder"]))
        rebuilt = hashlib.sha256(
            rebuilt_payload.encode()
        ).hexdigest()
        return {
            "evidenceSeq":
                int(evidence_seq),
            "stored": stored,
            "rebuilt": rebuilt,
            "valid": stored == rebuilt,
            "verifiedAt": ts(),
        }

    async def evidence_view(
            self, state: str = None,
            limit: int = 50) -> dict:
        """证据链视图(观测面)"""
        records = await \
            self.repo.list_evidence(
                state=state, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "states": [
                "assembled",
                "exported"],
            "nodes": list(
                EVIDENCE_NODES),
            "count": len(records),
            "records": records,
        }

    # ============================================================
    # ③ 合规健康报告(五维确定性)
    # ============================================================

    async def generate_report(
            self, period: str = "daily"
            ) -> dict:
        """合规健康报告生成(五维确定性
        统计——数字可复现; 红线触碰
        恒 0 宪法断言)

        Raises:
            ValueError: 周期域外
        """
        if period not in REPORT_PERIODS:
            raise ValueError(
                f"报告周期域外(period="
                f"{period}, 域="
                f"{REPORT_PERIODS})")
        # ① 端口合规分布
        panorama = await \
            self._panorama_readonly()
        port_compliance = {
            "frozenCount":
                panorama["frozenCount"],
            "degradedCount":
                panorama["degradedCount"],
            "brokenCount":
                panorama["brokenCount"],
            "alertedCount":
                panorama["alertedCount"],
        }
        # ② 自愈动作分布
        traces = await \
            self.repo.list_traces(
                limit=500)
        selfheal_actions = {
            a: sum(1 for t in traces
                  if t.get("action") == a)
            for a in {
                t.get("action")
                for t in traces}}
        # ③ 对账结果分布
        verifies = await \
            self.repo.list_verifies(
                limit=500)
        recons = await \
            self.repo.list_recons(
                limit=500)
        recon_outcome = {
            "matched": sum(
                1 for v in verifies
                if v.get("state")
                == "matched"),
            "mismatch": sum(
                1 for v in verifies
                if v.get("state")
                == "mismatch"),
            "autoHealed": sum(
                1 for r in recons
                if r.get("state")
                == "auto_healed"),
            "manualReferral": sum(
                1 for r in recons
                if r.get("state")
                == "manual_referral"),
            "failed": sum(
                1 for r in recons
                if r.get("state")
                == "failed"),
        }
        # ④ 叙事判定分布
        narratives = await \
            self.repo.list_narratives(
                limit=500)
        verdicts = {}
        for n in narratives:
            verdict = (n.get(
                "attribution")
                or {}).get(
                "causalVerdict",
                "neutral")
            verdicts[verdict] = \
                verdicts.get(verdict, 0) + 1
        # ⑤ 红线触碰(宪法级恒 0——
        # 全链无资金自动/无 LLM 数字)
        red_line_touches = 0
        dimensions = {
            "portCompliance":
                port_compliance,
            "selfhealActions":
                selfheal_actions,
            "reconOutcome":
                recon_outcome,
            "narrativeVerdicts":
                verdicts,
            "redLineTouches":
                red_line_touches,
        }
        # 报告哈希(维度确定性序列化)
        canonical = json.dumps(
            dimensions, sort_keys=True,
            ensure_ascii=False)
        report_hash = hashlib.sha256(
            canonical.encode()
        ).hexdigest()
        record = {
            "period": period,
            "state": "drafted",
            "dimensions": dimensions,
            "reportHash": report_hash,
            "dimensionOrder": list(
                REPORT_DIMENSIONS),
            "generatedAt": ts(),
            "publishedAt": "",
        }
        await self.repo.save_report(record)
        await self.repo.save_event({
            "type": "report_generated",
            "detail": {
                "period": period,
                "reportHash":
                    report_hash[:12],
            },
            "at": ts(),
        })
        return record

    async def publish_report(
            self, report_seq: int) -> dict:
        """报告发布(drafted→published
        ——留痕非资金动作, 不受开关)

        Raises:
            KeyError: 报告不存在
            ValueError: 已发布
        """
        record = await \
            self.repo.get_report(
                int(report_seq))
        if record is None:
            raise KeyError(
                f"报告不存在(reportSeq="
                f"{report_seq})")
        if record.get("state") != "drafted":
            raise ValueError(
                f"报告已发布(state="
                f"{record.get('state')})")
        record["state"] = "published"
        record["publishedAt"] = ts()
        await self.repo.update_report(
            int(report_seq), record)
        await self.repo.save_event({
            "type": "report_published",
            "detail": {
                "reportSeq":
                    int(report_seq),
                "period":
                    record.get("period"),
            },
            "at": ts(),
        })
        return record

    async def report_view(
            self, period: str = None,
            limit: int = 50) -> dict:
        """报告视图(观测面)"""
        records = await \
            self.repo.list_reports(
                period=period, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "periods": list(
                REPORT_PERIODS),
            "dimensions": list(
                REPORT_DIMENSIONS),
            "count": len(records),
            "records": records,
        }

    # ============================================================
    # 只读消费辅助
    # ============================================================

    async def _panorama_readonly(self):
        """P0 全景只读复用(叠加铁律)"""
        from services.pay71_p0_service import (
            Pay71P0Service,
        )
        return await Pay71P0Service()\
            .panorama()

    # ============================================================
    # 字典(观测面)
    # ============================================================

    def dict_view(self) -> dict:
        """审计透明字典公示(证据链节点/
        锚字段/链路图域/报告维度
        ——观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "evidenceNodes": list(
                EVIDENCE_NODES),
            "evidenceHashFields": {
                k: list(v) for k, v in
                EVIDENCE_HASH_FIELDS
                .items()},
            "tracegraphDomains": list(
                TRACEGRAPH_DOMAINS),
            "reportPeriods": list(
                REPORT_PERIODS),
            "reportDimensions": list(
                REPORT_DIMENSIONS),
            "ironRules": {
                "readonly":
                    "证据链只读导出——"
                    "永不修改原始留痕"
                    "(组装副本+哈希锚定)",
                "numbers":
                    "报告数字确定性生成"
                    "(可复现公式; LLM"
                    " 仅归因文案)",
                "redLine":
                    "红线触碰恒 0"
                    "(宪法级断言——"
                    "全链无资金自动)",
            },
        }
