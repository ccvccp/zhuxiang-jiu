"""61号·AI智能系统升级决策 决策图谱+归因报告
(dm61_graph_service, P3)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§3.1/§3.4/§七 P3):
    ① ATTRIBUTION_SCHEMA 决策归因报告
       ——每决策完整推理链 JSON(语义→
       影响面→环境→评估→先验→沙箱→
       推荐→裁决→反对意见——因子快照
       +规则命中+先验引用; LLM 仅润色
       不产数字)
    ② 决策图谱(CASE_LIBRARY——标签×
       结果×因果三元组: 从终态决策派生
       案例库+相似检索+先验概率)
    ③ 治理观测面(总览+分布)

QC 铁律:
    - 案例库封闭写入(仅从本模块终态
      决策派生——回流经人工; 红队
      RT-04 先验投毒防御)
    - 全程确定性聚合不发 LLM
"""

import logging

from core.helpers import ts

from repositories.dm61_repository import (
    Dm61Repository,
)

logger = logging.getLogger("dm61_graph")

MODEL_VERSION = "v1-dm61-graph"

# 相似检索风险带(±15 分同带)
RISK_BAND = 15.0

# 失败结果域(先验失败口径——rejected
# + dissent_confirmed)
FAILED_OUTCOMES = (
    "rejected", "dissent_confirmed")


class Dm61GraphService:
    """61号决策图谱+归因报告(P3)"""

    def __init__(self):
        self.repo = Dm61Repository()

    # ============================================================
    # ① 决策归因报告(ATTRIBUTION_SCHEMA)
    # ============================================================

    async def attribution_report(self,
                                 decision_id: int
                                 ) -> dict:
        """完整推理链 JSON(因子快照+规则
        命中+先验引用——确定性组装)

        Raises:
            KeyError: 决策不存在
        """
        decision = await self.repo.get_decision(
            int(decision_id))
        if not decision:
            raise KeyError(
                f"决策记录 {decision_id} 不存在")
        request_id = int(
            decision.get("requestId") or 0)
        request = await self.repo.get_request(
            request_id)
        assessments = await (
            self.repo.list_assessments(
                request_id=request_id))
        sims = await (
            self.repo.list_simulations(
                request_id=request_id))
        assess = assessments[0] \
            if assessments else None
        sim = sims[0] if sims else None

        semantic = (request
                    or {}).get(
            "semantic") or {}
        impact = (request
                  or {}).get(
            "impact") or {}
        environment = (request
                       or {}).get(
            "environment") or {}

        chain = {
            "semantic": {
                "tag": semantic.get("tag"),
                "sensitivity":
                    semantic.get(
                        "sensitivity"),
                "source": semantic.get(
                    "source"),
                "matchedRule":
                    semantic.get(
                        "matchedRule"),
            },
            "impact": {
                "impactPct":
                    impact.get(
                        "impactPct"),
                "roles": impact.get(
                    "roles"),
                "trustElements":
                    impact.get(
                        "trustElements"),
            },
            "environment": {
                "level": environment.get(
                    "level"),
                "penalties":
                    environment.get(
                        "penalties"),
            },
            "assess": {
                "riskScore":
                    (assess or {}).get(
                        "riskScore"),
                "level": (assess
                          or {}).get(
                    "level"),
                "factors": (assess
                            or {}).get(
                    "factors"),
            },
            "prior": (assess
                      or {}).get("prior"),
            "simulation": {
                "verdict": (sim
                            or {}).get(
                    "verdict"),
                "staticGate":
                    (sim or {}).get(
                        "staticGate"),
                "rollback":
                    (sim or {}).get(
                        "rollback"),
            } if sim else None,
            "recommendation": {
                "options":
                    decision.get(
                        "options"),
                "recommendedIndex":
                    decision.get(
                        "recommendedIndex"),
            },
            "decision": {
                "status": decision.get(
                    "status"),
                "outcome": decision.get(
                    "outcome"),
                "chosenIndex":
                    decision.get(
                        "chosenIndex"),
                "decidedBy": decision.get(
                    "decidedBy"),
                "changeId": decision.get(
                    "changeId"),
            },
            "dissent": decision.get(
                "dissent") or None,
        }
        return {
            "success": True,
            "decisionId": int(decision_id),
            "requestId": request_id,
            "schema": "ATTRIBUTION_SCHEMA",
            "chain": chain,
            "explainability":
                "完整推理链——因子快照+规则"
                "命中+先验引用(确定性组装; "
                "LLM assist 仅润色不产数字)",
            "generatedAt": ts(),
        }

    # ============================================================
    # ② 决策图谱(案例库+相似检索+先验)
    # ============================================================

    async def _case_pool(self) -> list:
        """案例池(从终态决策派生——封闭
        写入: 仅本模块决策记录)"""
        decisions = await (
            self.repo.list_decisions(
                limit=500))
        requests = {
            int(r.get("requestId") or 0): r
            for r in await
            self.repo.list_requests(
                limit=500)}
        cases = []
        for d in decisions:
            outcome = d.get("outcome")
            if not outcome:
                continue
            request = requests.get(
                int(d.get("requestId")
                    or 0)) or {}
            semantic = request.get(
                "semantic") or {}
            chosen_index = int(
                d.get("chosenIndex") or 0)
            options = d.get(
                "options") or []
            chosen = next(
                (o for o in options
                 if int(o.get("index")
                        or 0)
                 == chosen_index), None)
            # 因果三元组(标签×结果×因果)
            chosen_name = (chosen or {}).get(
                "name") or "未选择"
            cause_chain = {
                "factor": "{}/{}".format(
                    d.get("tag"),
                    semantic.get(
                        "sensitivity")),
                "action": "{}→{}".format(
                    d.get("level"),
                    chosen_name),
                "result": outcome,
            }
            cases.append({
                "caseId": int(
                    d.get("decisionId")
                    or 0),
                "tag": d.get("tag"),
                "sensitivity":
                    semantic.get(
                        "sensitivity"),
                "riskScore": float(
                    d.get("riskScore")
                    or 0.0),
                "level": d.get("level"),
                "outcome": outcome,
                "dissentResolved":
                    (d.get("dissent")
                     or {}).get(
                        "status") or "",
                "causeChain": cause_chain,
                "decidedAt":
                    d.get("updatedAt"),
            })
        return cases

    async def similar_cases(self,
                            tag: str = None,
                            sensitivity: str = None,
                            level: str = None,
                            outcome: str = None,
                            risk: float = None,
                            limit: int = 10
                            ) -> dict:
        """相似案例检索(标签×敏感级×
        风险带×结果——确定性过滤)

        risk 传入时按 ±RISK_BAND 同带
        匹配。
        """
        cases = await self._case_pool()
        result = cases
        if tag:
            result = [c for c in result
                      if c.get("tag")
                      == tag]
        if sensitivity:
            result = [c for c in result
                      if c.get(
                          "sensitivity")
                      == sensitivity]
        if level:
            result = [c for c in result
                      if c.get("level")
                      == level]
        if outcome:
            result = [c for c in result
                      if c.get("outcome")
                      == outcome]
        if risk is not None:
            try:
                risk = float(risk)
                result = [
                    c for c in result
                    if abs(c.get(
                        "riskScore")
                        - risk)
                    <= RISK_BAND]
            except (TypeError,
                    ValueError):
                pass
        result = sorted(
            result,
            key=lambda c: -c.get(
                "caseId"))[:limit]
        return {
            "success": True,
            "total": len(result),
            "cases": result,
            "riskBand": RISK_BAND,
            "note": "相似案例检索——决策图谱"
                    "派生案例库(封闭写入: "
                    "仅本模块终态决策)",
            "queriedAt": ts(),
        }

    async def prior_probability(self,
                                tag: str) -> dict:
        """先验概率(同标签历史失败率——
        确定性聚合)

        失败口径: rejected+
        dissent_confirmed。
        """
        tag = str(tag or "").strip()
        cases = await self._case_pool()
        same = [c for c in cases
                if c.get("tag") == tag]
        if not same:
            return {
                "success": True,
                "tag": tag,
                "sampleSize": 0,
                "failed": 0,
                "failRate": 0.0,
                "successRate": 0.0,
                "note": "无同标签历史案例"
                        "——先验中性",
            }
        failed = sum(
            1 for c in same
            if c.get("outcome")
            in FAILED_OUTCOMES)
        fail_rate = round(
            failed / len(same), 4)
        return {
            "success": True,
            "tag": tag,
            "sampleSize": len(same),
            "failed": failed,
            "failRate": fail_rate,
            "successRate": round(
                1 - fail_rate, 4),
            "failedOutcomes":
                list(FAILED_OUTCOMES),
            "note": "先验概率——同标签历史"
                    "失败率(决策图谱聚合)",
        }

    # ============================================================
    # ③ 治理观测面
    # ============================================================

    async def cases_view(self) -> dict:
        """决策图谱观测面(总览+分布)"""
        cases = await self._case_pool()
        by_tag: dict = {}
        by_outcome: dict = {}
        for c in cases:
            by_tag[c.get("tag")] = \
                by_tag.get(
                    c.get("tag"), 0) + 1
            by_outcome[
                c.get("outcome")] = \
                by_outcome.get(
                    c.get("outcome"),
                    0) + 1
        return {
            "success": True,
            "total": len(cases),
            "byTag": by_tag,
            "byOutcome": by_outcome,
            "recent": cases[:5],
            "note": "决策图谱——案例库派生"
                    "自终态决策(标签×结果×"
                    "因果三元组)",
        }
