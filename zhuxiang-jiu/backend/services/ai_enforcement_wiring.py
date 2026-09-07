"""批次五·半 AI 模块接线决策门
(ai_enforcement_wiring, 全站批次五)

《全站AI智能混合架构升级总计划》批次五:
    7 个「评分器已注册+业务流零调用」模块
    的接线补全——enrich+enforce 双函数
    对齐批次一~四范式:

        05收款  payment_routing → create_pay
            (路由类: 评分+快照推荐渠道,
             永不阻断——推荐不拦截)
        06物流  logistics_routing:balanced
            → create_order(路由类同上)
        08信息  message_content → send_message
            (阈值类: high 拦截垃圾内容)
        14团购  groupbuy_qualify → apply
            (阈值类: 高风险拒绝申请)
        17后台  admin_operation →
            assign_permissions(阈值类:
            自我提权/敏感时段拦截)
        18条款  agreement_risk →
            publish_agreement(阈值类:
            高危条款拒绝发布)
        19财务  finance_anomaly →
            audit_voucher(阈值类:
            借贷不平/异常凭证冻结)

铁律(对齐批次一~四):
    - AI_ENFORCE_MODE 默认 observe
      ——评分+快照供学习闭环, 决策
      不生效(行为 100% 兼容)
    - 各模块硬规则(SVIP 资格/门槛/
      频次/状态机/幂等)保留为合规
      底线, AI 门为叠加层不替代
    - enforce_decision fail-open 兜底
    - 路由类评分器(payment/logistics)
      决策为推荐编码, 天然不阻断
"""

import logging
from datetime import datetime

logger = logging.getLogger(
    "ai_enforcement_wiring")

MODEL_VERSION = "v1-wiring-gates"


# ============================================================
# ① 05收款·支付路由门(payment_routing)
# ============================================================

async def enrich_pay_routing(
        total_amount: float, scene_type: str,
        pay_channel: str) -> dict:
    """支付路由输入富化(渠道画像
    现成数据——确定性聚合)"""
    from repositories.payment_repository import (
        PaymentRepository,
    )
    channels = []
    try:
        channels = await PaymentRepository() \
            .list_active_channels() or []
    except Exception:  # pragma: no cover
        pass
    profiles = [
        {"channelCode": c.get("channelCode"),
         "channelType": c.get("channelType"),
         "feeRate": float(c.get("feeRate") or 0),
         "fixedFee": float(
             c.get("fixedFee") or 0),
         "minAmount": float(
             c.get("minAmount") or 0),
         "maxAmount": float(
             c.get("maxAmount") or 0),
         "dailyLimit": float(
             c.get("dailyLimit") or 0),
         "dailyAmount": float(
             c.get("dailyAmount") or 0),
         "status": c.get("status") or "active"}
        for c in channels
    ] or None
    return {
        "amount": float(total_amount or 0),
        "sceneType": scene_type,
        "channels": profiles,
        "requestedChannel": pay_channel,
    }


async def enforce_pay_create(
        pay_no: str, ctx: dict) -> dict:
    """支付单创建门(路由类——仅评分
    快照+渠道推荐, 永不阻断)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        "payment_routing",
        f"pay:{pay_no}", ctx)
    return {
        "payNo": pay_no,
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }


# ============================================================
# ② 06物流·运单路由门(logistics_routing:balanced)
# ============================================================

async def enrich_waybill_routing(
        sender: dict, receiver: dict,
        weight: float, piece_count: int,
        insured_value: float,
        settle_mode: str) -> dict:
    """运单路由输入富化(下单入参
    确定性组装)"""
    sender_city = str(
        sender.get("city") or "")
    receiver_city = str(
        receiver.get("city") or "")
    same_city = bool(
        sender_city and receiver_city
        and sender_city == receiver_city)
    return {
        "weight": float(weight or 0),
        "pieceCount": int(piece_count or 1),
        "insuredValue": float(
            insured_value or 0),
        "settleMode": settle_mode or "monthly",
        "sameCity": same_city,
        "serviceType": "standard",
    }


async def enforce_waybill_create(
        waybill_no: str, ctx: dict) -> dict:
    """运单创建门(路由类——仅评分
    快照+承运商推荐, 永不阻断)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        "logistics_routing:balanced",
        f"wb:{waybill_no}", ctx)
    return {
        "waybillNo": waybill_no,
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }


# ============================================================
# ③ 08信息·内容门(message_content)
# ============================================================

async def enrich_message_content(
        user_id: int, content: str) -> dict:
    """消息内容输入富化(当日已发
    数——确定性聚合)"""
    from repositories.message_repository import (
        MessageRepository,
    )
    hour_now = datetime.now()
    hour_start = hour_now.replace(
        minute=0, second=0, microsecond=0)
    hourly = 1
    try:
        messages = await MessageRepository() \
            .list_messages(user_id, limit=200) or []
        hourly = 1 + sum(
            1 for m in messages
            if (sent := m.get("sentAt"))
            and str(sent) >=
            hour_start.isoformat())
    except Exception:  # pragma: no cover
        pass
    return {
        "content": content or "",
        "hourlySendCount": hourly,
        "sendHour": hour_now.hour,
    }


async def enforce_message_send(
        business_key: str, ctx: dict) -> dict:
    """消息发送门(blocked → 拒绝发送)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        "message_content",
        f"msg:{business_key}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_message_blocked key=%r "
            "score=%s mode=%s", business_key,
            decision.get("score"),
            decision.get("mode"))
        raise ValueError(
            "消息发送被风控拦截(内容异常), "
            "请修改内容后重试")
    return {
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }


# ============================================================
# ④ 14团购·资格门(groupbuy_qualify)
# ============================================================

async def enrich_groupbuy_apply(
        user_id: int, items: list) -> dict:
    """团购申请输入富化(年度采购
    口径——确定性聚合)"""
    from repositories.groupbuy_repository import (
        GroupBuyRepository,
    )
    annual_amount = 0.0
    try:
        orders = await GroupBuyRepository() \
            .list_orders(user_id=user_id,
                         limit=200) or []
        annual_amount = sum(
            float(o.get("groupPrice") or 0)
            for o in orders)
    except Exception:  # pragma: no cover
        pass
    target_qty = sum(
        int(i.get("quantity") or 0)
        for i in items or [])
    return {
        "qualificationDocs": 3,
        "annualPurchaseAmount": annual_amount,
        "onTimePaymentRatio": 1.0,
        "violationCount": 0,
        "targetQuantity": max(1, target_qty),
    }


async def enforce_groupbuy_apply(
        order_no: str, ctx: dict) -> dict:
    """团购申请门(blocked → 拒绝申请)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        "groupbuy_qualify",
        f"gb:{order_no}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_groupbuy_blocked order=%r "
            "score=%s mode=%s", order_no,
            decision.get("score"),
            decision.get("mode"))
        raise ValueError(
            "团购申请被风控拦截, 请联系"
            "客服核实采购资质")
    return {
        "orderNo": order_no,
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }


# ============================================================
# ⑤ 17后台·操作门(admin_operation)
# ============================================================

async def enrich_admin_assign(
        operator_id: int, user_id: int) -> dict:
    """权限分配输入富化(操作时段/
    近期频次/自我提权——确定性聚合)"""
    from repositories.admin_repository import (
        AdminRepository,
    )
    now = datetime.now()
    ops_10min = 0
    try:
        logs = await AdminRepository() \
            .list_logs(user_id=operator_id,
                       limit=100) or []
        cutoff = (now.timestamp() - 600)
        for log in logs:
            created = str(
                log.get("createdAt") or "")
            try:
                dt = datetime.fromisoformat(
                    created.replace(
                        "Z", "+00:00"))
                if dt.timestamp() >= cutoff:
                    ops_10min += 1
            except ValueError:
                continue
    except Exception:  # pragma: no cover
        pass
    return {
        "operationType": "assign_permissions",
        "operationHour": now.hour,
        "isWeekend": now.weekday() >= 5,
        "operationsLast10Min": ops_10min,
        "operatesOnSelf": operator_id == user_id,
        "hasSecondReviewer": False,
    }


async def enforce_admin_assign(
        operator_id: int, user_id: int,
        ctx: dict) -> dict:
    """权限分配门(blocked → 拒绝分配
    ——自我提权/敏感时段拦截)

    Returns: 含 businessKey(供终态回流
    配对——on_admin_operation_settled)
    """
    from services.ai_enforcement import (
        enforce_decision,
    )
    from core.helpers import ts
    business_key = (f"{operator_id}:"
                    f"{user_id}:{ts()}")
    decision = await enforce_decision(
        "admin_operation",
        f"adminop:{business_key}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_adminop_blocked op=%r "
            "user=%r score=%s mode=%s",
            operator_id, user_id,
            decision.get("score"),
            decision.get("mode"))
        raise ValueError(
            "权限分配被风控拦截(敏感操作"
            "需双人复核), 请稍后重试")
    return {
        "blocked": False,
        "businessKey": business_key,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }


# ============================================================
# ⑥ 18条款·发布门(agreement_risk)
# ============================================================

async def enrich_agreement_publish(
        agreement: dict) -> dict:
    """条款发布输入富化(内容关键词
    确定性解析——非 LLM)"""
    content = str(
        agreement.get("content") or "")
    present = [
        clause for clause in
        ("交付条款", "付款条款", "违约责任",
         "争议解决", "保密条款")
        if clause in content]

    # 管辖类型(仲裁/单方远地启发式)
    if "单方远地" in content or \
            ("管辖" in content
             and "被告住所地" not in content
             and "履行地" not in content
             and "仲裁" not in content):
        jurisdiction = "unilateral_far"
    elif "仲裁" in content:
        jurisdiction = "arbitration"
    else:
        jurisdiction = "court_standard"
    return {
        "exemptionClauseCount":
            content.count("免责"),
        "penaltyRatio": 0.0,
        "unilateralClauseCount":
            content.count("单方"),
        "jurisdictionType": jurisdiction,
        "presentKeyClauses": present,
    }


async def enforce_agreement_publish(
        agreement_id: int, ctx: dict) -> dict:
    """条款发布门(blocked → 拒绝发布)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        "agreement_risk",
        f"agr:{agreement_id}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_agreement_blocked id=%r "
            "score=%s mode=%s", agreement_id,
            decision.get("score"),
            decision.get("mode"))
        raise ValueError(
            "条款发布被风控拦截(条款结构"
            "风险), 请法务复核后重拟")
    return {
        "agreementId": agreement_id,
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }


# ============================================================
# ⑦ 19财务·凭证审核门(finance_anomaly)
# ============================================================

async def enrich_finance_audit(
        voucher: dict) -> dict:
    """凭证审核输入富化(分录借贷
    平衡/金额口径——确定性聚合)"""
    entries = voucher.get("entries") or []
    debit = sum(
        float(e.get("amount") or 0)
        for e in entries
        if e.get("direction") == "debit")
    credit = sum(
        float(e.get("amount") or 0)
        for e in entries
        if e.get("direction") == "credit")
    amounts = [
        float(e.get("amount") or 0)
        for e in entries]
    avg = (sum(amounts) / len(amounts)
           if amounts else 0.0)
    now = datetime.now()
    return {
        "amount": float(
            voucher.get("amount") or 0),
        "accountAverageAmount": avg,
        "summaryMatchScore": 80.0,
        "entryHour": now.hour,
        "isWeekend": now.weekday() >= 5,
        "entriesToday": 1,
        "dailyAverageEntries": 10,
        "unbalanceAmount":
            round(abs(debit - credit), 2),
    }


async def enforce_finance_audit(
        voucher_no: str, ctx: dict) -> dict:
    """凭证审核门(blocked → 冻结审核)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        "finance_anomaly",
        f"fin:{voucher_no}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_voucher_blocked no=%r "
            "score=%s mode=%s", voucher_no,
            decision.get("score"),
            decision.get("mode"))
        raise ValueError(
            "凭证审核被风控拦截(借贷不平/"
            "异常凭证), 请财务主管核实")
    return {
        "voucherNo": voucher_no,
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }
