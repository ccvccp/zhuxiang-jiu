"""71号·AI智能支付端口大模型 帕累托调配服务
(pay71_p3_service, P3)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§七 P3 / §4.3):
    ① 四维向量评分(成本/成功率/体验/
       合规——确定性查表, LLM 禁入)
    ② 帕累托支配分析(非支配解集=
       帕累托前沿——确定性, 同输入
       同前沿可复现)
    ③ 情境权重规则表选解(价格敏感/
       高价值/大额/均衡四档——查表)
    ④ 外部信号注入(费率/汇率/政策→
       事件登记+影响分析+权重建议书
       →admin 终审→覆盖激活——权重
       变更永不自动铁律)

铁律(规划 §4.3/§1.2):
    - 调配永不直接执行路由(69号 P1
      唯一资金路由责任; 71号结果仅作
      建议注入 69号路由情境参考)
    - 四维评分与支配分析确定性可复现
      (向量数值全留痕)
    - 外部信号→建议书→admin 终审;
      "多智能体协同博弈"=确定性向量
      评分+规则表合成(非 LLM 博弈)
"""

import logging

from core.helpers import ts

from repositories.pay71_repository import (
    Pay71Repository,
)
from services.pay71_registry import (
    ALLOCATION_COMPLIANCE,
    ALLOCATION_CONTEXTS,
    ALLOCATION_CONTEXT_WEIGHTS,
    ALLOCATION_DIMENSIONS,
    ALLOCATION_SETTLE_CAP_HOURS,
    EXTERNAL_SIGNAL_KINDS,
    EXTERNAL_SIGNAL_SHIFTS,
    EXTERNAL_STATES,
    PORT_STATE_FACTORS,
    _port_registry, MODEL_VERSION,
    current_mode,
)

logger = logging.getLogger("pay71_p3_service")

# 69号健康度→成功率维乘数(只读消费
# 口径——确定性)
_HEALTH_MULTIPLIERS = {
    "healthy": 1.0,
    "degraded": 0.85,
    "critical": 0.60,
    "frozen": 0.0,
}


class Pay71P3Service:
    """71号帕累托调配(P3)"""

    def __init__(self):
        self.repo = Pay71Repository()

    # ============================================================
    # ① 四维向量评分(确定性查表)
    # ============================================================

    async def _scored_ports(self, amount: float,
                            tv_eligible: bool
                            ) -> list[dict]:
        """四维评分(frozen/broken/限额/
        credit_tv 资格过滤后打分)

        Raises:
            ValueError: 金额非法
        """
        amount = round(float(amount or 0), 2)
        if amount <= 0:
            raise ValueError(
                f"金额非法: {amount}(须>0)")
        # 只读消费 69号健康度(叠加铁律)
        from services.pay69_p0_service import (
            Pay69P0Service,
        )
        base = await Pay69P0Service()\
            .health_view()
        health = {
            c["channelId"]: c.get(
                "state", "healthy")
            for c in base.get("channels", [])
        }
        frozen = {
            c["channelId"] for c in
            base.get("channels", [])
            if c.get("frozen")}
        # 71号端口态(broken 摘除)
        ports_state = {
            p["portId"]: p.get(
                "portState", "healthy")
            for p in await
            self.repo.list_ports()}
        from services.pay69_registry import (
            MAX_FEE_RATE,
        )
        scored = []
        for pid, meta in _port_registry()\
                .items():
            if pid in frozen:
                continue
            if ports_state.get(
                    pid) == "broken":
                continue
            if pid == "credit_tv" \
                    and not tv_eligible:
                continue
            if amount > meta["singleLimit"]:
                continue
            cost = round(
                1 - meta["feeRate"]
                / MAX_FEE_RATE, 4)
            success = round(
                PORT_STATE_FACTORS.get(
                    ports_state.get(
                        pid, "healthy"),
                    1.0)
                * _HEALTH_MULTIPLIERS.get(
                    health.get(pid,
                               "healthy"), 1.0),
                4)
            experience = round(max(
                1 - meta["settleHours"]
                / ALLOCATION_SETTLE_CAP_HOURS,
                0.0), 4)
            compliance = \
                ALLOCATION_COMPLIANCE[pid]
            scored.append({
                "portId": pid,
                "scores": {
                    "cost": cost,
                    "success": success,
                    "experience": experience,
                    "compliance":
                        compliance,
                },
                "onFront": False,
            })
        return scored

    # ============================================================
    # ② 帕累托支配分析(确定性)
    # ============================================================

    @staticmethod
    def _dominates(a: dict, b: dict) -> bool:
        """A 支配 B(四维全≥且至少一维>)
        ——确定性定义"""
        ge = all(
            a["scores"][d] >= b["scores"][d]
            for d in ALLOCATION_DIMENSIONS)
        gt = any(
            a["scores"][d] > b["scores"][d]
            for d in ALLOCATION_DIMENSIONS)
        return ge and gt

    def _pareto_front(self,
                      scored: list[dict]
                      ) -> list[dict]:
        """帕累托前沿(非支配解集——确定性,
        同输入同前沿可复现)"""
        front = []
        for p in scored:
            dominated = any(
                self._dominates(q, p)
                for q in scored
                if q is not p)
            if not dominated:
                p["onFront"] = True
                front.append(p)
            else:
                p["onFront"] = False
        return front

    # ============================================================
    # ③ 情境权重选解(调配主入口)
    # ============================================================

    async def _active_weights(
            self, context: str) -> tuple:
        """激活权重(覆盖优先→出厂回退)

        Returns:
            (weights, source)
        """
        override = await self.repo.get_override(
            context)
        if override and override.get("weights"):
            return (dict(override["weights"]),
                    "override")
        return (dict(ALLOCATION_CONTEXT_WEIGHTS[
            context]), "factory")

    async def allocate(self, member_id: int,
                       amount: float,
                       context: str = "balanced",
                       tv_eligible: bool = False
                       ) -> dict:
        """帕累托调配主入口(评分→前沿→
        情境选解——建议注入 69号 P1 路由
        情境参考, 永不直接执行路由铁律)

        Raises:
            ValueError: 金额非法/情境域外/
                无可用端口
        """
        if context not in \
                ALLOCATION_CONTEXT_WEIGHTS:
            raise ValueError(
                f"情境域外(context={context}, "
                f"域={ALLOCATION_CONTEXTS})")
        member_id = int(member_id or 0)
        scored = await self._scored_ports(
            amount, tv_eligible)
        if not scored:
            raise ValueError(
                "无可用端口(frozen/broken/"
                "限额过滤后为空——保护态)")
        front = self._pareto_front(scored)
        weights, source = \
            await self._active_weights(context)
        # 前沿成员情境加权选解(确定性:
        # 最大加权分; 平分取注册表序)
        weighted = []
        for p in front:
            total = round(sum(
                weights[d] * p["scores"][d]
                for d in ALLOCATION_DIMENSIONS),
                4)
            weighted.append({
                "portId": p["portId"],
                "weightedScore": total,
            })
        best = max(
            weighted,
            key=lambda w: w["weightedScore"])
        record = {
            "memberId": member_id,
            "amount": amount,
            "context": context,
            "scores": {
                p["portId"]: p["scores"]
                for p in scored},
            "paretoFront": [
                p["portId"] for p in front],
            "weighted": weighted,
            "weightsUsed": weights,
            "weightsSource": source,
            "recommended": best["portId"],
            "advisoryOnly": True,
            "injection": {
                "target":
                    "pay69 P1 route context",
                "note":
                    "建议注入(69号 P1 唯一"
                    "资金路由责任——71号"
                    "永不直接执行路由)",
            },
            "engine": "rule_based",
            "mode": current_mode(),
            "allocatedAt": ts(),
        }
        await self.repo.save_allocation(record)
        await self.repo.save_event({
            "type": "allocation_made",
            "memberId": member_id,
            "detail": {
                "context": context,
                "recommended":
                    best["portId"],
                "frontSize": len(front),
            },
            "at": ts(),
        })
        return record

    async def records_view(
            self, context: str = None,
            limit: int = 50) -> dict:
        """调配留痕视图(观测面)"""
        records = await \
            self.repo.list_allocations(
                context=context, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(records),
            "records": records,
        }

    async def weights_view(self) -> dict:
        """激活权重视图(出厂+覆盖全量
        ——观测面)"""
        overrides = await \
            self.repo.list_overrides()
        override_map = {
            o["context"]: o
            for o in overrides}
        contexts = []
        for ctx in ALLOCATION_CONTEXTS:
            weights, source = \
                await self._active_weights(ctx)
            contexts.append({
                "context": ctx,
                "weights": weights,
                "source": source,
                "overrideFrom": (
                    override_map[ctx].get(
                        "externalSeq")
                    if ctx in override_map
                    else None),
            })
        return {
            "modelVersion": MODEL_VERSION,
            "dimensions": list(
                ALLOCATION_DIMENSIONS),
            "contexts": contexts,
            "overrideCount": len(overrides),
        }

    # ============================================================
    # ④ 外部信号注入(建议书→admin 终审)
    # ============================================================

    async def external_report(
            self, kind: str,
            note: str = "",
            affected: tuple = ()
            ) -> dict:
        """外部信号登记+影响分析+权重
        建议书(观测摄取——不受 PAY71_MODE
        影响; 权重变更永不自动, 仅生成
        proposed 建议书)

        Args:
            kind: fee_change/fx_fluctuation/
                policy_change
            note: 信号备注
            affected: 受影响端口(观测留痕)

        Raises:
            ValueError: 种类域外
            KeyError: 受影响端口域外
        """
        if kind not in EXTERNAL_SIGNAL_KINDS:
            raise ValueError(
                f"外部信号种类域外(kind="
                f"{kind}, 域="
                f"{EXTERNAL_SIGNAL_KINDS})")
        for pid in affected:
            if pid not in _port_registry():
                raise KeyError(
                    f"受影响端口域外("
                    f"portId={pid})")
        context, dim, delta = \
            EXTERNAL_SIGNAL_SHIFTS[kind]
        # 确定性建议: 目标维+delta 且从
        # 最大其他维扣减(权重和守恒)
        base = dict(
            ALLOCATION_CONTEXT_WEIGHTS[
                context])
        suggested = dict(base)
        suggested[dim] = round(
            suggested[dim] + delta, 4)
        largest_other = max(
            (d for d in
             ALLOCATION_DIMENSIONS
             if d != dim),
            key=lambda d: base[d])
        suggested[largest_other] = round(
            suggested[largest_other]
            - delta, 4)
        total = round(sum(
            suggested.values()), 4)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"建议权重和≠1.0: {total}")
        record = {
            "kind": kind,
            "context": context,
            "affectedPorts": list(affected),
            "note": str(note or ""),
            "state": "proposed",
            "suggestedWeights": suggested,
            "impact": {
                "dimension": dim,
                "delta": delta,
                "reducedFrom":
                    largest_other,
                "rationale":
                    "外部信号→情境维度"
                    "偏移(确定性映射)",
            },
            "disposition": {
                "proposedAction":
                    f"{context} 情境权重"
                    f"调整({dim}+{delta})",
                "expectedGain":
                    "调配权重响应外部"
                    "信号(费率/汇率/政策)",
                "riskAssessment": "low"
                "(admin 终审+可回滚"
                "删除覆盖回出厂)",
                "rollbackPlan":
                    "delete override 即回"
                    "出厂权重",
            },
            "reportedAt": ts(),
            "decidedAt": "",
            "decidedBy": "",
            "engine": "rule_based",
        }
        await self.repo.save_external(record)
        await self.repo.save_event({
            "type": "external_signal",
            "detail": {
                "kind": kind,
                "context": context,
                "affected": list(affected),
            },
            "at": ts(),
        })
        return record

    async def external_decide(
            self, external_seq: int,
            approve: bool) -> dict:
        """外部信号建议书终审(admin 人工
        ——权重覆盖激活唯一入口, 不受
        开关影响)

        Raises:
            KeyError: 建议书不存在
            ValueError: 已终态
        """
        record = await self.repo.get_external(
            int(external_seq))
        if record is None:
            raise KeyError(
                f"外部信号建议书不存在("
                f"externalSeq={external_seq})")
        if record.get("state") != "proposed":
            raise ValueError(
                f"建议书已终态(state="
                f"{record.get('state')})")
        if approve:
            context = record["context"]
            override = {
                "weights": dict(
                    record["suggestedWeights"]),
                "externalSeq":
                    int(external_seq),
                "kind": record["kind"],
                "activatedAt": ts(),
            }
            await self.repo.save_override(
                context, override)
            record["state"] = "approved"
            record["overrideActivated"] = True
        else:
            record["state"] = "rejected"
            record["overrideActivated"] = False
        record["decidedAt"] = ts()
        record["decidedBy"] = "admin(human)"
        await self.repo.update_external(
            int(external_seq), record)
        await self.repo.save_event({
            "type": "external_decided",
            "detail": {
                "externalSeq":
                    int(external_seq),
                "approve": bool(approve),
                "state": record["state"],
            },
            "at": ts(),
        })
        return record

    async def external_view(
            self, context: str = None,
            limit: int = 50) -> dict:
        """外部信号建议书视图(观测面)"""
        records = await \
            self.repo.list_external(
                context=context, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "signalKinds": list(
                EXTERNAL_SIGNAL_KINDS),
            "signalStates": list(
                EXTERNAL_STATES),
            "shifts": {
                k: {
                    "context": v[0],
                    "dimension": v[1],
                    "delta": v[2]}
                for k, v in
                EXTERNAL_SIGNAL_SHIFTS.items()},
            "count": len(records),
            "records": records,
        }

    # ============================================================
    # 字典(观测面)
    # ============================================================

    def dict_view(self) -> dict:
        """调配字典公示(维度/情境权重/
        合规分/外部信号口径——观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "dimensions": list(
                ALLOCATION_DIMENSIONS),
            "contexts": {
                ctx: dict(weights)
                for ctx, weights in
                ALLOCATION_CONTEXT_WEIGHTS
                .items()},
            "compliance": dict(
                ALLOCATION_COMPLIANCE),
            "settleCapHours":
                ALLOCATION_SETTLE_CAP_HOURS,
            "dominance":
                "A 支配 B ⟺ 四维全≥且"
                "至少一维>(确定性)",
            "fundsIronRule":
                "调配结果仅作建议注入 69号"
                " P1 路由情境参考——永不"
                "直接执行路由(69号 P1 唯一"
                "资金路由责任)",
            "externalIronRule":
                "外部信号→建议书→admin 终审"
                "——权重变更永不自动",
        }
