"""批次六·长尾七模块 AI 决策门
(ai_enforcement_longtail, 全站批次六)

《全站AI智能混合架构升级总计划》批次六:
    07客服/15合作/16代理/20位置/21酒店/
    26监控/27维护——七门(对齐批次一~五
    五段式): enrich(确定性聚合)+enforce
    (通用门: business_key 贯穿快照与终态)。

铁律:
    - AI_ENFORCE_MODE 默认 observe
      ——评分+快照供学习闭环, 决策
      不生效(行为 100% 兼容)
    - 各模块硬规则(状态机/资格校验/
      SLA/保证金阶梯)保留为合规底线,
      AI 门为叠加层不替代
    - enforce_decision fail-open 兜底
"""

import logging

logger = logging.getLogger(
    "ai_enforcement_longtail")

MODEL_VERSION = "v1-longtail-gates"


async def _gate(scorer_id: str, key: str,
                ctx: dict, block_msg: str) -> dict:
    """通用决策门(enforce 模式 high 拦截)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        scorer_id, key, ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_longtail_blocked scorer=%s "
            "key=%r score=%s mode=%s",
            scorer_id, key,
            decision.get("score"),
            decision.get("mode"))
        raise ValueError(block_msg)
    return {
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }


# ============================================================
# 33·07客服工单门(ticket_quality)
# ============================================================

async def enrich_ticket_create(
        user_level: int, ticket_type: str,
        priority: str) -> dict:
    """工单创建输入富化(创建入参+用户
    近期工单口径——确定性聚合)"""
    return {
        "ticketType": ticket_type,
        "priority": priority,
        "userLevel": int(user_level or 1),
        "slaOverdueHours": 0.0,
        "compensationLevel": "none",
        "escalated": False,
        "reopened": False,
        "satisfaction": None,
    }


async def enforce_ticket_create(
        ticket_no: str, ctx: dict) -> dict:
    return await _gate(
        "ticket_quality",
        f"ticket:{ticket_no}", ctx,
        "工单提交被风控拦截, 请核对"
        "工单类型或联系客服专线")


# ============================================================
# 34·15合作审核门(partner_review)
# ============================================================

async def enrich_partner_review(
        partner: dict = None) -> dict:
    """合作审核输入富化(合作方档案
    确定性聚合)"""
    p = partner or {}
    return {
        "partnerType": p.get("partnerType")
        or "supply",
        "partnerLevel": p.get("level")
        or "bronze",
        "partnerViolations": int(
            p.get("violations") or 0),
        "contractTerminated": bool(
            p.get("terminated")),
        "qualificationGap": 0,
        "reviewScore": 100.0,
        "estimatedAmount": 0.0,
        "multiRegion": False,
    }


async def enforce_partner_review(
        application_no: str, ctx: dict) -> dict:
    return await _gate(
        "partner_review",
        f"app:{application_no}", ctx,
        "合作审核被风控拦截(合作方资质"
        "异常), 请人工核实后重审")


# ============================================================
# 35·16代理审核门(agent_risk)
# ============================================================

async def enrich_agent_audit(
        agent: dict = None) -> dict:
    """代理审核输入富化(代理商档案
    现成信用口径——确定性聚合)"""
    a = agent or {}
    quota = float(a.get("wallet") or 0)
    return {
        "creditScore": float(
            a.get("creditScore") or 60),
        "returnRate": float(
            a.get("returnRate") or 0),
        "paymentDelayRate": float(
            a.get("paymentDelayRate") or 0),
        "level": a.get("level") or "C",
        "totalPurchases": float(
            a.get("totalPurchases") or 0),
        "crossRegion": bool(
            a.get("crossRegion")),
        "walletRatio": 0.0 if quota > 0 else 0.0,
        "activeMonths": int(
            a.get("activeMonths") or 6),
    }


async def enforce_agent_audit(
        apply_id: int, ctx: dict) -> dict:
    return await _gate(
        "agent_risk", f"audit:{apply_id}", ctx,
        "代理审核被风控拦截(代理商风险"
        "画像异常), 请人工复核资质")


# ============================================================
# 36·20配送点门(delivery_zone)
# ============================================================

async def enrich_delivery_point(
        zone: dict, distance_km: float) -> dict:
    """配送点判定输入富化(区域档案
    +haversine 距离——确定性聚合)"""
    return {
        "distanceKm": float(distance_km or 0),
        "radiusKm": float(
            (zone or {}).get("radius") or 0),
        "zoneStatus": (zone or {}).get(
            "status") or "active",
        "shippingFee": float(
            (zone or {}).get("shippingFee") or 0),
        "thresholdGap": 0.0,
        "addressCount": 3,
        "polygonPoints": 0,
        "hasEvidence": False,
    }


async def enforce_delivery_point(
        point_key: str, ctx: dict) -> dict:
    return await _gate(
        "delivery_zone",
        f"point:{point_key}", ctx,
        "配送点判定被风控拦截(范围外/"
        "区域异常), 请人工确认配送")


# ============================================================
# 37·21合作商审核门(venue_partner)
# ============================================================

async def enrich_venue_audit(
        partner: dict) -> dict:
    """合作商审核输入富化(合作商档案
    确定性聚合)"""
    p = partner or {}
    quota = float(p.get("paylaterQuota") or 0)
    used = float(p.get("paylaterUsed") or 0)
    return {
        "partnerType": p.get("partnerType")
        or "hotel",
        "partnerLevel": p.get("partnerLevel")
        or "C",
        "starLevel": int(
            p.get("starLevel") or 0),
        "supplyMode": p.get("supplyMode")
        or "purchase",
        "agentId": p.get("agentId"),
        "unsettledAmount": float(
            p.get("unsettledAmount") or 0),
        "paylaterQuota": quota,
        "paylaterUsed": used,
        "suspendedCount": int(
            p.get("suspendedCount") or 0),
    }


async def enforce_venue_audit(
        partner_id: int, ctx: dict) -> dict:
    return await _gate(
        "venue_partner",
        f"partner:{partner_id}", ctx,
        "合作商审核被风控拦截(合作商"
        "风险画像异常), 请人工复核")


# ============================================================
# 38·26告警门(ops_alert)
# ============================================================

async def enrich_ops_alert(
        alert_level: str, current_value: float,
        threshold: float, source: str = "") -> dict:
    """告警上报输入富化(上报入参
    确定性组装)"""
    return {
        "alertLevel": alert_level,
        "currentValue": float(
            current_value or 0),
        "threshold": float(threshold or 0),
        "source": source,
        "unresolvedCount": 0,
        "ackDelayMinutes": 0.0,
        "incidentLinked": False,
        "nightWindow": False,
        "suppressed": False,
    }


async def enforce_ops_alert(
        alert_id: int, ctx: dict) -> dict:
    return await _gate(
        "ops_alert", f"alert:{alert_id}", ctx,
        "告警上报被风控拦截(异常告警"
        "风暴), 请核实指标来源")


# ============================================================
# 39·27自愈门(self_healing)
# ============================================================

async def enrich_self_healing(
        fault_type: str, recovery_level: str,
        target: str = "") -> dict:
    """故障检测输入富化(检测入参
    确定性组装)"""
    return {
        "faultType": fault_type,
        "recoveryLevel": recovery_level,
        "recentFailures": 0,
        "diagnoseConfidence": 60.0,
        "target": target,
        "downtimeMinutes": 0.0,
        "taskFailureRate": 0.0,
        "multiTarget": False,
    }


async def enforce_self_healing(
        recovery_id: int, ctx: dict) -> dict:
    return await _gate(
        "self_healing",
        f"recovery:{recovery_id}", ctx,
        "自愈决策被风控拦截(高风险故障"
        "需人工介入), 请运维专员处理")
