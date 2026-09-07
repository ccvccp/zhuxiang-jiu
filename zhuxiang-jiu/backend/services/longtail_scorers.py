"""批次六·长尾七模块 AI 评分器集合
(longtail_scorers, 全站批次六)

《全站AI智能混合架构升级总计划》批次六:
    7 个零 AI 长尾模块新档案(batch33-39,
    计数 48→55)——七评分器同文件
    (八因子确定性加权, 对齐批次一~五范式):

    | 档案 | batch | 模块 | 挂门 | 回流终态 |
    |------|-------|------|------|---------|
    | ticket_quality    | 33 | 07客服工单 | create_ticket | confirm(满意度) |
    | partner_review    | 34 | 15合作接口 | review_application | sign(签约) |
    | agent_risk        | 35 | 16代理商 | audit | rebate_withdraw |
    | delivery_zone     | 36 | 20位置地图 | check_delivery_point | add_evidence |
    | venue_partner     | 37 | 21酒店合作商 | audit_partner | settle_commission |
    | ops_alert         | 38 | 26智能监控 | raise_alert | resolve_alert |
    | self_healing      | 39 | 27智能维护 | detect_fault | attempt_recovery |

铁律(对齐批次一~五): 确定性加权/
LLM 不进判定链/observe 默认只评分
快照/各模块硬规则保留为合规底线。
"""

import logging
from typing import ClassVar

from core.helpers import ts

logger = logging.getLogger("longtail_scorers")

MODEL_VERSION = "v1-longtail"


def _clamp(v, low=0.0, high=100.0):
    return max(low, min(high, float(v)))


def _factor(name, label, score, weight, detail):
    return {"name": name, "label": label,
            "score": round(_clamp(score), 1),
            "weight": round(float(weight), 4),
            "contribution": round(
                _clamp(score) * float(weight), 2),
            "detail": detail}


def _three_level(risk: float) -> str:
    return ("high" if risk >= 60
            else "medium" if risk >= 30
            else "low")


def _result(scorer_id, module, risk, level,
            factors, confidence, names,
            actions):
    return {
        "success": True, "scorer": scorer_id,
        "module": module, "score": risk,
        "level": level, "levelName": names[level],
        "action": actions[level],
        "factors": factors, "confidence": confidence,
        "modelVersion": MODEL_VERSION,
        "scoredAt": ts(),
    }


# ============================================================
# 33·07客服工单质量评分(ticket_quality)
# ============================================================

class TicketQualityScorer:
    """07号客服工单 AI 评分器(第49档案)"""

    WEIGHTS: ClassVar[dict] = {
        "sla_breach": 0.20, "priority_urgency": 0.15,
        "compensation_level": 0.15,
        "satisfaction_deficit": 0.15,
        "escalation_risk": 0.10, "user_level_vip": 0.10,
        "type_risk": 0.10, "reopen_risk": 0.05,
    }
    REQUIRED: ClassVar[list] = ["ticketType"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")
        prio = str(ctx.get("priority") or "low")
        ttype = str(ctx.get("ticketType") or "")
        f1 = _clamp(ctx.get("slaOverdueHours") or 0) \
            * 2
        f2 = {"urgent": 100.0, "high": 60.0,
              "medium": 30.0}.get(prio, 10.0)
        f3 = {"severe": 100.0, "general": 50.0,
              "minor": 10.0}.get(
            str(ctx.get("compensationLevel")
                or "none"), 0.0)
        sat = ctx.get("satisfaction")
        f4 = 40.0 if sat is None else _clamp(
            (5 - float(sat)) * 25)
        f5 = 100.0 if ctx.get("escalated") else 0.0
        f6 = 80.0 if int(
            ctx.get("userLevel") or 1) >= 4 else 0.0
        f7 = 70.0 if "complaint" in ttype else 20.0
        f8 = 100.0 if ctx.get("reopened") else 0.0
        fs = [("sla_breach", "SLA超时", f1,
               "工单创建以来超时"),
              ("priority_urgency", "优先级", f2,
               f"优先级 {prio}"),
              ("compensation_level", "补偿等级", f3,
               "补偿档位"),
              ("satisfaction_deficit", "满意度缺口", f4,
               f"满意度 {sat}"),
              ("escalation_risk", "已升级", f5, "升级标记"),
              ("user_level_vip", "VIP用户", f6, "用户等级"),
              ("type_risk", "类型敏感", f7, f"类型 {ttype}"),
              ("reopen_risk", "重开风险", f8, "重开标记")]
        factors = [_factor(n, l, v,
                           self.WEIGHTS[n], d)
                   for n, l, v, d in fs]
        risk = round(sum(f["contribution"]
                         for f in factors), 1)
        return _result(
            "ticket_quality", "07客服工单", risk,
            _three_level(risk), factors,
            1.0, {"low": "低风险(常规处理)",
                  "medium": "中风险(优先跟进)",
                  "high": "高风险(升级专处理)"},
            {"low": "工单常规处理",
             "medium": "工单优先跟进",
             "high": "工单升级专处理"})


# ============================================================
# 34·15合作方审核评分(partner_review)
# ============================================================

class PartnerReviewScorer:
    """15号合作接口 AI 评分器(第50档案)"""

    WEIGHTS: ClassVar[dict] = {
        "qualification_gap": 0.20,
        "score_deficit": 0.15,
        "amount_scale": 0.15,
        "partner_history": 0.15,
        "level_risk": 0.10, "type_risk": 0.10,
        "contract_risk": 0.10, "scope_breadth": 0.05,
    }
    REQUIRED: ClassVar[list] = ["partnerType"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")
        missing = max(0, int(
            ctx.get("qualificationGap") or 0))
        f1 = _clamp(missing * 40)
        f2 = _clamp(100 - float(
            ctx.get("reviewScore") or 0))
        amt = float(ctx.get("estimatedAmount") or 0)
        f3 = _clamp(amt / 500000 * 100)
        f4 = _clamp(int(
            ctx.get("partnerViolations") or 0) * 40)
        f5 = {"bronze": 80.0, "silver": 50.0,
              "gold": 20.0, "platinum": 0.0}.get(
            str(ctx.get("partnerLevel") or ""), 60.0)
        f6 = 60.0 if str(
            ctx.get("partnerType") or "") \
            in ("supply", "channel") else 20.0
        f7 = 50.0 if ctx.get("contractTerminated") \
            else 0.0
        f8 = 100.0 if ctx.get("multiRegion") else 30.0
        fs = [("qualification_gap", "资质缺口", f1,
               f"缺 {missing} 项"),
              ("score_deficit", "审核分缺口", f2, "规则评分"),
              ("amount_scale", "金额规模", f3,
               f"预估 ¥{amt:.0f}"),
              ("partner_history", "合作方历史", f4, "历史违约"),
              ("level_risk", "等级风险", f5, "合作方等级"),
              ("type_risk", "类型敏感", f6, "合作类型"),
              ("contract_risk", "解约风险", f7, "历史解约"),
              ("scope_breadth", "范围广度", f8, "多地域")]
        factors = [_factor(n, l, v,
                           self.WEIGHTS[n], d)
                   for n, l, v, d in fs]
        risk = round(sum(f["contribution"]
                         for f in factors), 1)
        return _result(
            "partner_review", "15合作接口", risk,
            _three_level(risk), factors, 1.0,
            {"low": "低风险(标准签约)",
             "medium": "中风险(法务复核)",
             "high": "高风险(终止合作)"},
            {"low": "合作正常签约",
             "medium": "合作法务复核",
             "high": "合作终止处理"})


# ============================================================
# 35·16代理商风险评分(agent_risk)
# ============================================================

class AgentRiskScorer:
    """16号代理商管理 AI 评分器(第51档案)"""

    WEIGHTS: ClassVar[dict] = {
        "credit_deficit": 0.20,
        "return_rate": 0.15, "payment_delay": 0.15,
        "level_risk": 0.10, "purchase_scale": 0.10,
        "region_risk": 0.10, "wallet_drain": 0.10,
        "stability": 0.10,
    }
    REQUIRED: ClassVar[list] = ["creditScore"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")
        f1 = _clamp(100 - float(
            ctx.get("creditScore") or 0))
        f2 = _clamp(float(
            ctx.get("returnRate") or 0) * 500)
        f3 = _clamp(float(
            ctx.get("paymentDelayRate") or 0) * 300)
        f4 = {"D": 100.0, "C": 70.0, "B": 40.0,
              "A": 15.0, "S": 0.0}.get(
            str(ctx.get("level") or "C"), 60.0)
        f5 = _clamp(float(
            ctx.get("totalPurchases") or 0)
            / 1000000 * 100)
        f6 = 60.0 if ctx.get("crossRegion") else 0.0
        f7 = _clamp(float(
            ctx.get("walletRatio") or 0) * 100)
        f8 = _clamp(50 - int(
            ctx.get("activeMonths") or 0) * 5)
        fs = [("credit_deficit", "信用缺口", f1, "信用分"),
              ("return_rate", "退货率", f2, "退货率"),
              ("payment_delay", "回款延迟", f3, "延迟率"),
              ("level_risk", "等级风险", f4, "代理等级"),
              ("purchase_scale", "采购规模", f5, "累计采购"),
              ("region_risk", "窜货风险", f6, "跨区标记"),
              ("wallet_drain", "钱包水位", f7, "钱包占比"),
              ("stability", "稳定期缺口", f8, "活跃月数")]
        factors = [_factor(n, l, v,
                           self.WEIGHTS[n], d)
                   for n, l, v, d in fs]
        risk = round(sum(f["contribution"]
                         for f in factors), 1)
        return _result(
            "agent_risk", "16代理商管理", risk,
            _three_level(risk), factors, 1.0,
            {"low": "低风险(正常代理)",
             "medium": "中风险(限额观察)",
             "high": "高风险(资质复核)"},
            {"low": "代理正常放行",
             "medium": "代理限额观察",
             "high": "代理资质复核"})


# ============================================================
# 36·20配送范围评分(delivery_zone)
# ============================================================

class DeliveryZoneScorer:
    """20号位置地图 AI 评分器(第52档案)"""

    WEIGHTS: ClassVar[dict] = {
        "distance_ratio": 0.25,
        "evidence_missing": 0.15,
        "radius_small": 0.15,
        "zone_status": 0.10, "fee_anomaly": 0.10,
        "threshold_gap": 0.10,
        "address_density": 0.10,
        "polygon_complex": 0.05,
    }
    REQUIRED: ClassVar[list] = ["distanceKm"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")
        dist = float(ctx.get("distanceKm") or 0)
        radius = float(ctx.get("radiusKm") or 0)
        f1 = _clamp(dist / radius * 100) \
            if radius > 0 else 100.0
        f2 = 60.0 if not ctx.get("hasEvidence") \
            else 0.0
        f3 = _clamp((10 - radius) * 12) \
            if 0 < radius < 10 else 0.0
        f4 = 100.0 if str(
            ctx.get("zoneStatus") or "active") \
            != "active" else 0.0
        fee = float(ctx.get("shippingFee") or 0)
        f5 = _clamp(fee * 10) if fee > 8 else 0.0
        f6 = _clamp(float(
            ctx.get("thresholdGap") or 0) / 100 * 100)
        f7 = _clamp((3 - int(
            ctx.get("addressCount") or 1)) * 30)
        f8 = 50.0 if int(
            ctx.get("polygonPoints") or 0) > 30 \
            else 0.0
        fs = [("distance_ratio", "距离占比", f1,
               f"距中心 {dist:.1f}km/半径{radius:.0f}"),
              ("evidence_missing", "存证缺失", f2, "存证状态"),
              ("radius_small", "半径过小", f3,
               f"半径 {radius:.0f}km"),
              ("zone_status", "区域状态", f4, "区域状态"),
              ("fee_anomaly", "运费异常", f5,
               f"运费 ¥{fee:.0f}"),
              ("threshold_gap", "免运门槛", f6, "门槛缺口"),
              ("address_density", "地址稀疏", f7, "地址数"),
              ("polygon_complex", "多边形复杂", f8, "顶点数")]
        factors = [_factor(n, l, v,
                           self.WEIGHTS[n], d)
                   for n, l, v, d in fs]
        risk = round(sum(f["contribution"]
                         for f in factors), 1)
        return _result(
            "delivery_zone", "20位置地图", risk,
            _three_level(risk), factors, 1.0,
            {"low": "低风险(正常配送)",
             "medium": "中风险(人工确认)",
             "high": "高风险(范围外拒配)"},
            {"low": "正常配送",
             "medium": "人工确认配送",
             "high": "范围外拒配"})


# ============================================================
# 37·21酒店合作商评分(venue_partner)
# ============================================================

class VenuePartnerScorer:
    """21号酒店合作商 AI 评分器(第53档案)"""

    WEIGHTS: ClassVar[dict] = {
        "level_risk": 0.15, "settle_debt": 0.15,
        "paylater_exposure": 0.15, "star_deficit": 0.10,
        "supply_mode_risk": 0.10, "agent_missing": 0.10,
        "type_risk": 0.10, "history_risk": 0.15,
    }
    REQUIRED: ClassVar[list] = ["partnerType"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")
        f1 = {"D": 100.0, "C": 70.0, "B": 40.0,
              "A": 15.0, "S": 0.0}.get(
            str(ctx.get("partnerLevel") or "C"), 60.0)
        f2 = _clamp(float(
            ctx.get("unsettledAmount") or 0)
            / 50000 * 100)
        quota = float(
            ctx.get("paylaterQuota") or 0)
        used = float(ctx.get("paylaterUsed") or 0)
        f3 = _clamp(used / quota * 100) \
            if quota > 0 else 0.0
        star = int(ctx.get("starLevel") or 0)
        f4 = _clamp((4 - star) * 30) if star else 60.0
        f5 = 60.0 if str(
            ctx.get("supplyMode") or "") == "consignment" \
            else 20.0
        f6 = 50.0 if not ctx.get("agentId") else 0.0
        ptype = str(ctx.get("partnerType") or "")
        f7 = {"club": 70.0, "bar": 50.0,
              "hotel": 20.0}.get(ptype, 40.0)
        f8 = _clamp(int(
            ctx.get("suspendedCount") or 0) * 50)
        fs = [("level_risk", "等级风险", f1, "合作商等级"),
              ("settle_debt", "未结算欠款", f2, "欠款金额"),
              ("paylater_exposure", "铺货敞口", f3, "额度占用"),
              ("star_deficit", "星级缺口", f4,
               f"星级 {star}"),
              ("supply_mode_risk", "供货模式", f5, "供货模式"),
              ("agent_missing", "无代理", f6, "代理关联"),
              ("type_risk", "类型敏感", f7, f"类型 {ptype}"),
              ("history_risk", "历史风险", f8, "暂停次数")]
        factors = [_factor(n, l, v,
                           self.WEIGHTS[n], d)
                   for n, l, v, d in fs]
        risk = round(sum(f["contribution"]
                         for f in factors), 1)
        return _result(
            "venue_partner", "21酒店合作商", risk,
            _three_level(risk), factors, 1.0,
            {"low": "低风险(正常合作)",
             "medium": "中风险(限额合作)",
             "high": "高风险(暂停合作)"},
            {"low": "合作正常推进",
             "medium": "合作限额观察",
             "high": "合作暂停复核"})


# ============================================================
# 38·26监控告警评分(ops_alert)
# ============================================================

class OpsAlertScorer:
    """26号智能监控 AI 评分器(第54档案)"""

    WEIGHTS: ClassVar[dict] = {
        "severity_level": 0.25,
        "value_deviation": 0.20,
        "unresolved_burst": 0.15,
        "ack_delay": 0.10, "source_risk": 0.10,
        "incident_linked": 0.10, "night_alert": 0.05,
        "suppressed": 0.05,
    }
    REQUIRED: ClassVar[list] = ["alertLevel"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")
        f1 = {"fatal": 100.0, "critical": 80.0,
              "warning": 40.0,
              "info": 10.0}.get(
            str(ctx.get("alertLevel") or "info"), 30.0)
        cur = float(ctx.get("currentValue") or 0)
        thr = float(ctx.get("threshold") or 0)
        f2 = _clamp((cur - thr) / thr * 100) \
            if thr > 0 and cur > thr else 0.0
        f3 = _clamp(int(
            ctx.get("unresolvedCount") or 0) * 20)
        f4 = _clamp(float(
            ctx.get("ackDelayMinutes") or 0) / 2)
        f5 = 70.0 if str(
            ctx.get("source") or "") in (
            "database", "payment", "auth") else 30.0
        f6 = 100.0 if ctx.get("incidentLinked") \
            else 0.0
        f7 = 60.0 if ctx.get("nightWindow") else 0.0
        f8 = 100.0 if ctx.get("suppressed") else 0.0
        fs = [("severity_level", "严重级别", f1, "告警级别"),
              ("value_deviation", "阈值偏离", f2,
               f"值 {cur:.0f}/阈值 {thr:.0f}"),
              ("unresolved_burst", "未解积压", f3, "积压数"),
              ("ack_delay", "响应延迟", f4, "确认延迟"),
              ("source_risk", "来源敏感", f5, "告警来源"),
              ("incident_linked", "事件关联", f6, "事件关联"),
              ("night_alert", "夜间告警", f7, "时段"),
              ("suppressed", "已被抑制", f8, "抑制标记")]
        factors = [_factor(n, l, v,
                           self.WEIGHTS[n], d)
                   for n, l, v, d in fs]
        risk = round(sum(f["contribution"]
                         for f in factors), 1)
        return _result(
            "ops_alert", "26智能监控", risk,
            _three_level(risk), factors, 1.0,
            {"low": "低风险(常规告警)",
             "medium": "中风险(升级观察)",
             "high": "高风险(立即响应)"},
            {"low": "告警常规通知",
             "medium": "告警升级观察",
             "high": "告警立即响应"})


# ============================================================
# 39·27维护自愈评分(self_healing)
# ============================================================

class SelfHealingScorer:
    """27号智能维护 AI 评分器(第55档案)"""

    WEIGHTS: ClassVar[dict] = {
        "fault_severity": 0.20,
        "recovery_level": 0.20,
        "failure_count": 0.15,
        "diagnose_confidence": 0.10,
        "target_risk": 0.10, "duration_risk": 0.10,
        "task_failure_rate": 0.10,
        "scope_breadth": 0.05,
    }
    REQUIRED: ClassVar[list] = ["faultType"]

    async def score(self, ctx: dict) -> dict:
        if not ctx:
            raise ValueError("评分上下文不能为空")
        ftype = str(ctx.get("faultType") or "")
        f1 = {"data_loss": 100.0, "service_down": 80.0,
              "performance": 50.0,
              "disk_full": 40.0}.get(ftype, 40.0)
        f2 = {"manual": 100.0, "semi": 60.0,
              "auto": 10.0}.get(
            str(ctx.get("recoveryLevel") or "auto"),
            50.0)
        f3 = _clamp(int(
            ctx.get("recentFailures") or 0) * 30)
        f4 = _clamp(100 - float(
            ctx.get("diagnoseConfidence") or 50) )
        f5 = 70.0 if str(
            ctx.get("target") or "") in (
            "database", "redis", "payment") else 30.0
        f6 = _clamp(float(
            ctx.get("downtimeMinutes") or 0) / 3)
        f7 = _clamp(float(
            ctx.get("taskFailureRate") or 0) * 200)
        f8 = 60.0 if ctx.get("multiTarget") else 0.0
        fs = [("fault_severity", "故障严重度", f1,
               f"类型 {ftype}"),
              ("recovery_level", "自愈级别", f2, "恢复级别"),
              ("failure_count", "近期失败", f3, "失败次数"),
              ("diagnose_confidence", "诊断置信", f4,
               "置信度"),
              ("target_risk", "目标敏感", f5, "影响目标"),
              ("duration_risk", "停机时长", f6, "时长"),
              ("task_failure_rate", "任务失败率", f7, "比率"),
              ("scope_breadth", "影响面", f8, "多目标")]
        factors = [_factor(n, l, v,
                           self.WEIGHTS[n], d)
                   for n, l, v, d in fs]
        risk = round(sum(f["contribution"]
                         for f in factors), 1)
        return _result(
            "self_healing", "27智能维护", risk,
            _three_level(risk), factors, 1.0,
            {"low": "低风险(自动自愈)",
             "medium": "中风险(半自动)",
             "high": "高风险(人工介入)"},
            {"low": "故障自动自愈",
             "medium": "故障半自动恢复",
             "high": "故障人工介入"})
