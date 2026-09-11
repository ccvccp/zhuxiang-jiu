"""智运·AI智能物流大模型 P3 分析与进化(zw_analysis_service)

创新规划 §二 P3: 设计文档§六(成本)+§十(分析预测)落地
    - 成本分析: 物流商费率对比 + 月度成本趋势(数据织物聚合)
    - 运量预测: 加权移动平均(0.6 近期+0.4 全期, 复用智启元范式)
    - 反馈闭环: 路由时效权重 etaWeight ±0.1(clamp [0.4, 0.8] 安全阀)
    - 孪生总览: 四引擎健康度 + 三率观测(签收率/准时率/破损率)

铁律: 全链确定性; 建议书模式; 既有结算/订单零改动。
"""

import logging
from collections import defaultdict

from services.zw_fabric_service import (
    _ZwStore, ZwFabricService, _round2, _now_iso)

logger = logging.getLogger(__name__)

# 进化参数安全阀(对齐智启元 trendWeight 范式)
ETA_WEIGHT_CLAMP = (0.4, 0.8)
ETA_WEIGHT_STEP = 0.1
ETA_WEIGHT_DEFAULT = 0.6

# 运量预测权重(复用智启元 RECENT/FULL 范式)
RECENT_WEIGHT = 0.6
FULL_WEIGHT = 0.4

FEEDBACK_VERDICTS = ("adopted", "corrected", "rejected")
FEEDBACK_TARGETS = ("route_decision", "eta", "risk_assess", "claim")


class ZwAnalysisService:
    """P3: 成本分析 + 运量预测 + 反馈进化 + 孪生总览"""

    def __init__(self, store: _ZwStore = None,
                 fabric: ZwFabricService = None):
        self.store = store or _ZwStore()
        self.fabric = fabric or ZwFabricService(self.store)

    # ============================================================
    # 成本分析(物流商对比 + 月度趋势)
    # ============================================================

    async def cost_analysis(self) -> dict:
        """物流成本分析: 费率对比 + 月度趋势

        - 物流商对比: 均费/总费/单量(数据织物聚合)
        - 月度趋势: 按月聚合 totalFee
        - 优化建议: 均费最高物流商 → 议价建议(建议书)
        """
        orders = await self.fabric.repo.list_orders(limit=500)
        by_carrier: dict[str, dict] = defaultdict(
            lambda: {"count": 0, "totalFee": 0.0})
        by_month: dict[str, float] = defaultdict(float)
        for o in orders:
            carrier = str(o.get("carrier", "") or "")
            fee = float(o.get("totalFee", 0) or 0)
            if carrier:
                by_carrier[carrier]["count"] += 1
                by_carrier[carrier]["totalFee"] += fee
            month = str(o.get("createdAt", "") or "")[:7]
            if month:
                by_month[month] += fee

        carriers = [
            {"carrier": c, "count": d["count"],
             "totalFee": _round2(d["totalFee"]),
             "avgFee": _round2(d["totalFee"] / d["count"])}
            for c, d in by_carrier.items()
        ]
        carriers.sort(key=lambda x: x["avgFee"])

        suggestions = []
        if carriers:
            expensive = carriers[-1]
            cheap = carriers[0]
            if expensive["avgFee"] > cheap["avgFee"] * 1.5 \
                    and expensive["count"] >= 3:
                suggestions.append(
                    f"{expensive['carrier']} 均费 ¥{expensive['avgFee']} "
                    f"高于 {cheap['carrier']} ¥{cheap['avgFee']} 50%+, "
                    "建议评估流量再平衡或议价(须人工)")

        return {
            "byCarrier": carriers,
            "byMonth": [{"month": m, "totalFee": _round2(f)}
                        for m, f in sorted(by_month.items())],
            "totalFee": _round2(sum(d["totalFee"]
                                     for d in by_carrier.values())),
            "suggestions": suggestions or ["成本结构均衡, 无议价建议"],
            "note": "确定性聚合; 优化建议书, 调整须人工",
            "analyzedAt": _now_iso(),
        }

    # ============================================================
    # 运量预测(加权移动平均)
    # ============================================================

    async def volume_forecast(self, horizon: int = 1) -> dict:
        """未来 N 期运量预测(0.6 近期+0.4 全期+趋势)

        口径(复用智启元预测范式): 近 3 期均值×0.6 + 全期均值×0.4
        + 线性趋势项(斜率>0 追加)。
        """
        if not 1 <= horizon <= 6:
            raise ValueError("预测期数须在 [1, 6]")
        orders = await self.fabric.repo.list_orders(limit=500)
        by_month: dict[str, int] = defaultdict(int)
        for o in orders:
            month = str(o.get("createdAt", "") or "")[:7]
            if month:
                by_month[month] += 1
        months = sorted(by_month)
        if not months:
            raise ValueError("暂无物流订单, 无法预测运量")
        values = [by_month[m] for m in months]

        recent = values[-3:]
        recent_avg = sum(recent) / len(recent)
        full_avg = sum(values) / len(values)
        base = RECENT_WEIGHT * recent_avg + FULL_WEIGHT * full_avg

        # 线性趋势(最近 3 期一阶差分均值)
        if len(recent) >= 2:
            diffs = [recent[i + 1] - recent[i]
                     for i in range(len(recent) - 1)]
            slope = sum(diffs) / len(diffs)
        else:
            slope = 0.0
        # 趋势仅用于上浮预测(运力规划宁多勿少), 且减半防过冲
        trend_used = slope > 0

        rows = []
        level = base
        for step in range(1, horizon + 1):
            if trend_used:
                level = level + slope * 0.5
            rows.append({
                "step": step,
                "predictedOrders": max(0, round(level)),
            })

        return {
            "horizon": horizon,
            "historyMonths": len(months),
            "recentAvg": _round2(recent_avg),
            "fullAvg": _round2(full_avg),
            "trendSlope": _round2(slope),
            "trendApplied": trend_used,
            "rows": rows,
            "weightScheme": f"{RECENT_WEIGHT} 近期 + {FULL_WEIGHT} 全期",
            "determinismNote": "同输入同输出(加权移动平均+线性趋势)",
            "note": "运力规划参考; 排班决策须人工",
            "generatedAt": _now_iso(),
        }

    # ============================================================
    # 反馈闭环(etaWeight 进化, 安全阀)
    # ============================================================

    async def feedback(self, target_type: str, verdict: str,
                       note: str = "") -> dict:
        """反馈闭环: adopted→etaWeight+0.1 / rejected→-0.1(安全阀)

        etaWeight ∈ [0.4, 0.8]: >0.6 更信近期时效(预测更灵敏),
        <0.6 更信全期(预测更稳健)。
        """
        if target_type not in FEEDBACK_TARGETS:
            raise ValueError(f"反馈目标无效({target_type}); "
                             f"支持: {'/'.join(FEEDBACK_TARGETS)}")
        if verdict not in FEEDBACK_VERDICTS:
            raise ValueError(f"裁决须为 {'/'.join(FEEDBACK_VERDICTS)}"
                             f"(当前 {verdict})")

        params = await self.get_params()
        delta = 0.0
        if verdict != "corrected":
            step = (ETA_WEIGHT_STEP if verdict == "adopted"
                    else -ETA_WEIGHT_STEP)
            new_val = max(ETA_WEIGHT_CLAMP[0],
                          min(ETA_WEIGHT_CLAMP[1],
                              params["etaWeight"] + step))
            await self.store.save_params({
                **params, "etaWeight": _round2(new_val)})
            delta = _round2(new_val - params["etaWeight"])
            params = await self.get_params()

        fid = await self.store.next_id("feedback")
        record = {
            "feedbackId": fid, "targetType": target_type,
            "verdict": verdict, "note": note,
            "etaWeightDelta": delta,
            "etaWeightAfter": params["etaWeight"],
            "createdAt": _now_iso(),
        }
        await self.store.save("feedbacks", fid, record)
        return record

    async def get_params(self) -> dict:
        params = await self.store.get_params()
        return params or {"etaWeight": ETA_WEIGHT_DEFAULT,
                          "updatedAt": ""}

    async def feedbacks(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("feedbacks")
        return sorted(rows, key=lambda r: r.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 孪生总览(四引擎健康度 + 三率观测)
    # ============================================================

    async def twin(self) -> dict:
        """智运孪生总览(观测面)

        - 三率: 签收率/在途率/失败退回率(数据织物)
        - 引擎覆盖: 路由决策数/ETA 记录数/风控评分数/理赔数
        - 进化参数: etaWeight
        """
        lake = await self.fabric.lake_overview()
        route_count = len(await self.store.list("route_decisions"))
        eta_count = len(await self.store.list("eta_records"))
        risk_count = len(await self.store.list("risk_assess"))
        claim_count = len(await self.store.list("claims"))
        params = await self.get_params()

        return {
            "lake": lake,
            "engines": {
                "routeDecisions": route_count,
                "etaRecords": eta_count,
                "riskAssess": risk_count,
                "claims": claim_count,
            },
            "evolution": {
                "etaWeight": params["etaWeight"],
                "clamp": list(ETA_WEIGHT_CLAMP),
                "updatedAt": params.get("updatedAt", ""),
            },
            "note": "孪生为观测面; 四引擎全确定性, 处置永不自动",
            "computedAt": _now_iso(),
        }

    # ============================================================
    # 大模型总览
    # ============================================================

    async def status(self) -> dict:
        params = await self.get_params()
        feedbacks = await self.store.list("feedbacks")
        lake = await self.fabric.lake_overview()
        return {
            "module": "智运·AI智能物流大模型",
            "orders": lake["orders"]["total"],
            "signRate": lake["orders"]["signRate"],
            "avgSignHours": lake["timeliness"]["avgSignHours"],
            "evolution": {
                "feedbacks": len(feedbacks),
                "etaWeight": params["etaWeight"],
            },
            "note": "智能调度中枢; 叠加既有物流 18 端点零改动",
            "updatedAt": _now_iso(),
        }
