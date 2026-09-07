"""治理类三模块 AI 决策门
(ai_enforcement_governance, 全站批次四)

《全站AI智能混合架构升级总计划》批次四:
    22追溯/24合规/25市级网店——治理类
    三门(对齐批次一~三五段式):
        ① trace_integrity → activate_life_code
          首扫激活门(不破坏 orderId 供
          65号分润取数契约)
        ② compliance_inspection →
          monitor_behavior 行为巡检门
          (riskLevel 自动判定辅助——
          observe 模式不覆盖调用方传参)
        ③ citystore_health →
          run_assessment 月度考核门
          (双达标硬规则不变, AI 门
          为叠加观测层)

铁律(对齐批次一~三):
    - AI_ENFORCE_MODE 默认 observe
      ——评分+快照供学习闭环, 决策
      不生效(行为 100% 兼容)
    - 各模块硬规则(状态机/一瓶一
      激活/双达标判定)保留为合规
      底线, AI 门为叠加层不替代
    - enforce_decision fail-open 兜底
"""

import logging

logger = logging.getLogger(
    "ai_enforcement_governance")

MODEL_VERSION = "v1-governance-gates"


# ============================================================
# ① 22追溯·首扫激活门(trace_integrity)
# ============================================================

async def enrich_trace_activation(
        life: dict, user_id: int = None,
        order_id: str = "",
        purchase_channel: str = "",
        purchase_price: float = 0) -> dict:
    """激活输入富化(生命码+所属箱
    现成变量——确定性聚合; 本次激活
    传入的渠道/价格/订单优先)"""
    from repositories.trace_repository import (
        TraceRepository,
    )
    repo = TraceRepository()

    box = None
    box_code_id = life.get("boxCodeId")
    if box_code_id:
        try:
            box = await repo.get_box_code(
                int(box_code_id))
        except Exception:  # pragma: no cover
            pass

    # 同用户激活深度(历史激活扫码数)
    user_activation_count = 1
    if user_id:
        try:
            scans = await repo.list_scan_logs(
                user_id=int(user_id)) or []
            user_activation_count = max(
                1, sum(1 for s in scans
                       if s.get("scanType")
                       == "activate"))
        except Exception:  # pragma: no cover
            pass

    return {
        "lifeCode": life.get("lifeCode"),
        "orderId": order_id
        or life.get("orderId") or "",
        "crossRegion": bool(
            (box or {}).get("isCrossRegion")),
        "purchaseChannel": purchase_channel
        or life.get("purchaseChannel") or "",
        "purchasePrice": float(
            purchase_price
            or life.get("purchasePrice")
            or 0),
        "expectedPrice": float(
            life.get("expectedPrice") or 0),
        "boxCode": (box or {}).get("boxCode"),
        "boxOpened": (box or {}).get("status")
        == "opened",
        "userActivationCount":
            user_activation_count,
        "batchRisk": 0.0,
    }


async def enforce_trace_activation(
        life_code: str, ctx: dict) -> dict:
    """首扫激活门(blocked → 拒绝激活)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        "trace_integrity",
        f"activate:{life_code}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_activate_blocked life=%r "
            "score=%s mode=%s", life_code,
            decision.get("score"),
            decision.get("mode"))
        raise ValueError(
            "追溯码激活被风控拦截, 请核对"
            "购买渠道或联系客服")
    return {
        "lifeCode": life_code,
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }


# ============================================================
# ② 24合规·行为巡检门(compliance_inspection)
# ============================================================

async def enrich_behavior_monitor(
        module_name: str, behavior_type: str,
        behavior_data: dict = None) -> dict:
    """巡检输入富化(行为数据+同域
    历史记录——确定性聚合)"""
    from repositories.compliance_repository import (
        ComplianceRepository,
    )
    repo = ComplianceRepository()
    data = behavior_data or {}

    # 同模块+类型历史(违规计数/频次)
    violations = 0
    frequency = 1
    try:
        records = await repo \
            .list_behavior_monitors(
                module_name=module_name,
                limit=200) or []
        same_type = [
            r for r in records
            if r.get("behaviorType")
            == behavior_type]
        violations = sum(
            1 for r in same_type
            if r.get("riskLevel")
            in ("high", "extreme"))
        frequency = max(1, len(same_type) + 1)
    except Exception:  # pragma: no cover
        pass

    amount = 0.0
    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        pass

    return {
        "moduleName": module_name,
        "behaviorType": behavior_type,
        "amount": amount,
        "dailyTotal": amount,
        "largeSingle": 50000.0,
        "largeDaily": 200000.0,
        "entityViolations": violations,
        "action": behavior_type,
        "frequency": frequency,
        "hasEvidence": bool(
            data.get("evidenceId")),
        "manualFlag": bool(
            data.get("manualFlag")),
        "escalationTrend": 0.0,
    }


async def enforce_behavior_monitor(
        business_key: str, ctx: dict) -> dict:
    """行为巡检门(blocked → 拒绝记录
    ——enforce 模式下高危行为不落监)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        "compliance_inspection",
        f"behavior:{business_key}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_behavior_blocked key=%r "
            "score=%s mode=%s", business_key,
            decision.get("score"),
            decision.get("mode"))
        raise ValueError(
            "行为巡检被风控拦截(高危行为"
            "不落监), 请先完成人工核实")
    return {
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }


# ============================================================
# ③ 25市级网店·月度考核门(citystore_health)
# ============================================================

async def enrich_citystore_health(
        store: dict, monthly_purchase: float,
        monthly_sales: float) -> dict:
    """考核输入富化(月度汇总+店铺
    档案——确定性聚合)"""
    return {
        "storeCode": store.get("storeCode"),
        "monthlyPurchase":
            float(monthly_purchase or 0),
        "monthlySales":
            float(monthly_sales or 0),
        "purchaseTarget": 9000.0,
        "salesTarget": 5000.0,
        "consecutiveBelow": max(
            int(store.get(
                "consecutiveBelowPurchase")
                or 0),
            int(store.get(
                "consecutiveBelowSales")
                or 0)),
        "currentDiscount": int(
            store.get("currentDiscount")
            or 90),
        "status": int(
            store.get("status") or 1),
        "channelCount": 2,
        "orderCount": int(
            store.get("monthlyOrderCount")
            or 0),
        "trendDown": 0.0,
    }


async def enforce_citystore_assessment(
        store_code: str, month: str,
        ctx: dict) -> dict:
    """月度考核门(observe 观测层——
    双达标硬规则不变; enforce 模式
    high 拦截异常考核)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        "citystore_health",
        f"assessment:{store_code}:{month}",
        ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_assessment_blocked store=%r "
            "month=%s score=%s mode=%s",
            store_code, month,
            decision.get("score"),
            decision.get("mode"))
        raise ValueError(
            "月度考核被风控拦截, 请先核对"
            "店铺经营数据")
    return {
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }
