"""AI 语义评分层(v7.1 → v7.2 升级: B级高落差模块补齐 AI 语义)

为 5 个高落差模块(04订单/05收款/06物流/11流量/29推广码)补齐 AI 评分语义,
沿用 agent_service._calc_credit_score + risk_assess 的多因子加权评分模式:

    输入上下文 → 多因子评分(0-100) → 等级映射(3级) → 决策动作 → 因子明细+置信度

评分器清单:
    OrderRiskScorer          订单风控评分(04): 8因子 → 风险分 → pass/review/block
    PaymentRoutingScorer     支付路由评分(05): 5因子/渠道 → 适配分 → 推荐渠道
    LogisticsRoutingScorer   物流路由评分(06): 6因子/承运商 → 适配分 → 推荐承运商
    TrafficAntiFraudScorer   流量防作弊评分(11): 7因子 → 作弊分 → pass/review/block
    PromotionAntiFraudScorer 推广码防作弊评分(29): 6因子 → 作弊分 → pay/hold/review

设计约定:
    - 纯函数式评分(输入 dict → 输出 dict), 不落库, 不改现有模块(零侵入)
    - 全 async(项目约定); 异常约定: ValueError → 409(输入非法)
    - 置信度 = 输入字段完整度(缺失字段越多置信度越低, 对齐 AI 客服语义)
"""

import logging
from typing import ClassVar
from datetime import datetime, UTC

from core.helpers import ts
from services.ai_learning_service import (
    get_active_weight_version, load_effective_weights,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1"

# 3 级风险等级与决策动作
LEVEL_LOW, LEVEL_MEDIUM, LEVEL_HIGH = "low", "medium", "high"
LEVEL_NAMES = {LEVEL_LOW: "低风险", LEVEL_MEDIUM: "中风险", LEVEL_HIGH: "高风险"}


def _now_hour() -> int:
    return datetime.now(UTC).hour


def _clamp(value, lo=0.0, hi=100.0) -> float:
    return max(lo, min(hi, value))


def _risk_level(score: float, medium_at=35.0, high_at=65.0) -> str:
    if score >= high_at:
        return LEVEL_HIGH
    if score >= medium_at:
        return LEVEL_MEDIUM
    return LEVEL_LOW


def _confidence(ctx: dict, required: list) -> float:
    """置信度 = 必需字段完整度(缺失越多越低, 下限 0.3)"""
    present = sum(1 for k in required if ctx.get(k) is not None)
    return round(max(0.3, present / len(required)), 2) if required else 0.5


def _factor(name: str, label: str, score: float, weight: float, detail: str) -> dict:
    """构造因子明细(风险贡献 = score × weight)"""
    score = round(_clamp(score), 1)
    return {"name": name, "label": label, "score": score, "weight": weight,
            "contribution": round(score * weight, 1), "detail": detail}


# ============================================================
# 1. 订单风控评分(模块 04 订单管理)
# ============================================================

class OrderRiskScorer:
    """订单风控评分: 下单时点风险画像(刷单/薅羊毛/恶意订单识别)

    8 因子加权 → 风险分(0-100, 越高越危险) → 3级风险 + 处置动作
    """

    WEIGHTS: ClassVar[dict] = {
        "credit": 0.20,        # 会员竹信分
        "register_age": 0.15,  # 注册时长
        "amount": 0.15,        # 大额异常
        "quantity": 0.10,      # 数量异常
        "cancel_rate": 0.15,   # 历史取消率
        "address": 0.10,       # 地址完整性
        "remark": 0.10,        # 备注风险词
        "time_pattern": 0.05,  # 下单时段
    }
    RISK_WORDS = ("刷单", "代刷", "返现", "刷单返", "薅羊毛", "黄牛")
    REQUIRED: ClassVar[list] = ["bambooScore", "registerHours", "orderAmount", "historyOrders"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                bambooScore: int 竹信分(0-1000, 缺省取 600 中性),
                registerHours: float 注册至今年时数,
                orderAmount: float 订单金额,
                totalQuantity: int 总件数,
                historyOrders: int 历史订单数, historyCancels: int 历史取消数,
                addressComplete: bool, remark: str, orderHour: int(0-23)
            }

        Raises:
            ValueError: 订单金额非法
        """
        amount = float(ctx.get("orderAmount") or 0)
        if amount < 0:
            raise ValueError("订单金额不能为负")

        # 自学习层生效权重(champion), 无档案/异常时回退类默认值
        weights = await load_effective_weights("order_risk", self.WEIGHTS)

        f = {}
        # 竹信分: <300 高风险, ≥700 零风险
        credit = float(ctx.get("bambooScore") if ctx.get("bambooScore") is not None else 600)
        f["credit"] = _factor("credit", "会员信用", _clamp((700 - credit) / 4),
                              weights["credit"], f"竹信分 {credit:.0f}")
        # 注册时长: <24h 满风险, ≥720h(30天) 零风险
        hours = float(ctx.get("registerHours") or 0)
        f["register_age"] = _factor(
            "register_age", "注册时长", _clamp((720 - hours) / 7.2),
            weights["register_age"],
            f"注册 {hours:.0f} 小时" + ("(新号)" if hours < 24 else ""))
        # 金额: ≥10000 满风险, 线性
        f["amount"] = _factor("amount", "订单金额", _clamp(amount / 100),
                              weights["amount"], f"金额 ¥{amount:,.2f}")
        # 数量: ≥20 件满风险
        qty = int(ctx.get("totalQuantity") or 0)
        f["quantity"] = _factor("quantity", "购买数量", _clamp(qty * 5),
                                weights["quantity"], f"共 {qty} 件")
        # 历史取消率
        hist = int(ctx.get("historyOrders") or 0)
        cancels = int(ctx.get("historyCancels") or 0)
        rate = (cancels / hist) if hist > 0 else 0.0
        f["cancel_rate"] = _factor("cancel_rate", "历史取消率", _clamp(rate * 150),
                                   weights["cancel_rate"],
                                   f"取消率 {rate:.0%}({cancels}/{hist})")
        # 地址完整性
        addr_ok = bool(ctx.get("addressComplete", True))
        f["address"] = _factor("address", "收货地址", 0 if addr_ok else 85,
                               weights["address"],
                               "完整" if addr_ok else "地址缺失关键字段")
        # 备注风险词
        remark = str(ctx.get("remark") or "")
        hit = [w for w in self.RISK_WORDS if w in remark]
        f["remark"] = _factor("remark", "备注风险词", 100 if hit else 0,
                              weights["remark"],
                              f"命中 {hit}" if hit else "无风险词")
        # 下单时段(0-5 点凌晨)
        hour = int(ctx.get("orderHour", _now_hour()))
        night = hour < 6
        f["time_pattern"] = _factor("time_pattern", "下单时段",
                                    70 if night else 0,
                                    weights["time_pattern"],
                                    f"{hour} 点" + ("(凌晨)" if night else ""))

        risk = sum(x["contribution"] for x in f.values())
        level = _risk_level(risk)
        action = {"low": "pass", "medium": "review", "high": "block"}[level]
        result = {
            "success": True, "scorer": "order_risk", "modelVersion": MODEL_VERSION,
            "weightVersion": get_active_weight_version("order_risk"),
            "score": round(risk, 1), "level": level, "levelName": LEVEL_NAMES[level],
            "action": action, "actionName": {"pass": "直接放行",
                                             "review": "人工复核",
                                             "block": "拦截"}[action],
            "confidence": _confidence(ctx, self.REQUIRED),
            "factors": list(f.values()), "scoredAt": ts(),
        }
        logger.info("ai_order_risk_scored score=%s level=%s action=%s",
                    result["score"], level, action)
        return result


# ============================================================
# 2. 支付路由评分(模块 05 收款管理)
# ============================================================

# 内置渠道画像(渠道配置表为空时兜底; 字段对齐 payment_channels 表)
BUILTIN_CHANNEL_PROFILES = {
    "wechat":     {"channelType": "third_party", "feeRate": 0.006, "fixedFee": 0.0,
                   "minAmount": 0.01, "maxAmount": 50000, "dailyLimit": 200000},
    "alipay":     {"channelType": "third_party", "feeRate": 0.0055, "fixedFee": 0.0,
                   "minAmount": 0.01, "maxAmount": 50000, "dailyLimit": 200000},
    "unionpay":   {"channelType": "bank", "feeRate": 0.004, "fixedFee": 1.0,
                   "minAmount": 1.0, "maxAmount": 100000, "dailyLimit": 500000},
    "bank":       {"channelType": "bank", "feeRate": 0.001, "fixedFee": 2.0,
                   "minAmount": 100.0, "maxAmount": 500000, "dailyLimit": 2000000},
    "aggregate":  {"channelType": "aggregate", "feeRate": 0.008, "fixedFee": 0.0,
                   "minAmount": 0.01, "maxAmount": 20000, "dailyLimit": 100000},
}

# 场景-渠道类型适配矩阵(1.0 最优)
_SCENE_TYPE_FIT = {
    "order_pay":      {"third_party": 1.0, "aggregate": 0.8, "bank": 0.5},
    "wallet_deposit": {"bank": 1.0, "third_party": 0.7, "aggregate": 0.6},
    "agent_purchase": {"bank": 1.0, "aggregate": 0.8, "third_party": 0.6},
}


class PaymentRoutingScorer:
    """支付路由评分: 为支付请求在各候选渠道中计算适配分, 输出推荐渠道

    5 因子/渠道 → 适配分(0-100, 越高越优) → 排序推荐
    """

    WEIGHTS: ClassVar[dict] = {"availability": 0.30, "limit_fit": 0.25, "cost": 0.20,
               "scene_fit": 0.15, "capacity": 0.10}

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                amount: float 实付金额,
                sceneType: order_pay/wallet_deposit/agent_purchase,
                channels: 渠道配置列表(缺省用内置画像),
                    [{channelCode, channelType, feeRate, fixedFee, minAmount,
                      maxAmount, dailyLimit, dailyAmount, status}]
            }

        Raises:
            ValueError: 金额非法 / 场景非法
        """
        amount = float(ctx.get("amount") or 0)
        if amount <= 0:
            raise ValueError("路由评分要求金额 > 0")
        scene = str(ctx.get("sceneType") or "order_pay")
        if scene not in _SCENE_TYPE_FIT:
            raise ValueError(f"场景类型非法: {scene}")

        channels = ctx.get("channels") or [
            {"channelCode": code, **prof, "dailyAmount": 0.0, "status": "active"}
            for code, prof in BUILTIN_CHANNEL_PROFILES.items()
        ]

        candidates = []
        for ch in channels:
            code = ch.get("channelCode", "")
            reasons = []
            # ① 可用性: 非 active 直接不合格
            status = ch.get("status", "active")
            avail = 100 if status == "active" else 0
            if status != "active":
                reasons.append(f"渠道状态 {status}, 不可用")
            # ② 限额适配: 单笔/日/月
            lo, hi = float(ch.get("minAmount") or 0), float(ch.get("maxAmount") or 0)
            daily_left = float(ch.get("dailyLimit") or 0) - float(ch.get("dailyAmount") or 0)
            limit_ok = lo <= amount <= hi and daily_left >= amount
            limit_score = 100 if limit_ok else (50 if lo <= amount <= hi else 0)
            if not limit_ok:
                reasons.append("超出单笔或日累计限额")
            # ③ 成本: 综合费率越低越好(0 → 100 分; ≥1% → 0 分)
            fee = float(ch.get("feeRate") or 0) + float(ch.get("fixedFee") or 0) / max(amount, 1)
            cost_score = _clamp((0.01 - fee) / 0.01 * 100) if fee < 0.01 else 0
            # ④ 场景适配
            fit = _SCENE_TYPE_FIT[scene].get(ch.get("channelType", ""), 0.4)
            scene_score = fit * 100
            # ⑤ 日限额余量(容量)
            capacity = _clamp(daily_left / float(ch.get("dailyLimit") or 1) * 100)

            total = (avail * self.WEIGHTS["availability"]
                     + limit_score * self.WEIGHTS["limit_fit"]
                     + cost_score * self.WEIGHTS["cost"]
                     + scene_score * self.WEIGHTS["scene_fit"]
                     + capacity * self.WEIGHTS["capacity"])
            eligible = status == "active" and limit_ok
            if eligible:
                reasons.append(f"综合费率 {fee:.3%}, 场景适配 {fit:.0%}")
            candidates.append({
                "channelCode": code, "channelType": ch.get("channelType", ""),
                "score": round(total, 1), "eligible": eligible,
                "factors": {"availability": avail, "limitFit": limit_score,
                            "cost": round(cost_score, 1), "sceneFit": scene_score,
                            "capacity": round(capacity, 1)},
                "reasons": reasons,
            })

        candidates.sort(key=lambda c: (-c["score"], c["channelCode"]))
        for i, c in enumerate(candidates, 1):
            c["rank"] = i
        best = next((c for c in candidates if c["eligible"]), None)

        result = {
            "success": True, "scorer": "payment_routing", "modelVersion": MODEL_VERSION,
            "amount": round(amount, 2), "sceneType": scene,
            "candidates": candidates,
            "recommendation": ({"channelCode": best["channelCode"],
                                "score": best["score"],
                                "reason": "; ".join(best["reasons"])}
                               if best else {"channelCode": "",
                                             "score": 0, "reason": "无可用渠道(均超限/停用)"}),
            "confidence": _confidence(ctx, ["amount", "sceneType", "channels"]),
            "scoredAt": ts(),
        }
        logger.info("ai_payment_routing_scored amount=%s scene=%s best=%s",
                    amount, scene, result["recommendation"]["channelCode"])
        return result


# ============================================================
# 3. 物流路由评分(模块 06 物流接口)
# ============================================================

# 承运商画像(对齐 logistics_repository 承运商编码)
CARRIER_PROFILES = {
    "SF":  {"name": "顺丰速运", "speed": 95, "cost": 45, "maxWeight": 100,
            "insured": True, "monthly": True, "sameCity": True, "heavy": False},
    "JD":  {"name": "京东物流", "speed": 85, "cost": 60, "maxWeight": 80,
            "insured": True, "monthly": True, "sameCity": True, "heavy": False},
    "LLL": {"name": "货拉拉", "speed": 75, "cost": 85, "maxWeight": 2000,
            "insured": False, "monthly": False, "sameCity": True, "heavy": True},
    "DB":  {"name": "德邦快递", "speed": 70, "cost": 70, "maxWeight": 500,
            "insured": True, "monthly": True, "sameCity": False, "heavy": True},
    "YT":  {"name": "圆通速递", "speed": 55, "cost": 95, "maxWeight": 50,
            "insured": False, "monthly": True, "sameCity": False, "heavy": False},
}

# 策略偏好 → 时效/成本权重
_BUDGET_WEIGHTS = {
    "speed":    {"speed": 0.40, "cost": 0.10},
    "cost":     {"speed": 0.10, "cost": 0.40},
    "balanced": {"speed": 0.25, "cost": 0.25},
}


class LogisticsRoutingScorer:
    """物流路由评分: 为运单在各承运商中计算适配分, 输出推荐承运商

    6 因子/承运商(时效/成本权重按策略偏好调整) → 适配分 → 排序推荐
    """

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                weight: float kg, pieceCount: int,
                insuredValue: float 保价金额, settleMode: monthly/cash/prepaid,
                sameCity: bool 是否同城, budget: speed/cost/balanced(缺省 balanced),
                serviceType: standard/express/same_city(影响时效加分)
            }

        Raises:
            ValueError: 重量非法
        """
        weight = float(ctx.get("weight") or 0)
        if weight <= 0:
            raise ValueError("路由评分要求重量 > 0")
        insured = float(ctx.get("insuredValue") or 0)
        settle = str(ctx.get("settleMode") or "monthly")
        same_city = bool(ctx.get("sameCity", False))
        budget = str(ctx.get("budget") or "balanced")
        if budget not in _BUDGET_WEIGHTS:
            budget = "balanced"
        # 自学习层生效权重(按策略子键隔离: logistics_routing:speed/cost/balanced)
        scorer_key = f"logistics_routing:{budget}"
        weights = await load_effective_weights(scorer_key, _BUDGET_WEIGHTS[budget])
        w_speed = weights["speed"]
        w_cost = weights["cost"]
        w_rest = round(1.0 - w_speed - w_cost, 2)  # 其余 4 因子平分
        w_other = round(w_rest / 4, 3) if w_rest > 0 else 0.0
        express = str(ctx.get("serviceType") or "standard") == "express"

        candidates = []
        for code, prof in CARRIER_PROFILES.items():
            reasons, hard_fail = [], False
            # ① 重量上限(硬约束)
            if weight > prof["maxWeight"]:
                hard_fail = True
                reasons.append(f"超重(>{prof['maxWeight']}kg)")
            # ② 时效(express 服务对速度要求放大)
            speed = prof["speed"] * (1.1 if express else 1.0)
            # ③ 成本
            # ④ 保价能力: 高保价(≥¥2000)必须支持保价
            insured_ok = prof["insured"] or insured < 2000
            if not insured_ok:
                hard_fail = True
                reasons.append("高保价运单需支持保价")
            # ⑤ 月结支持
            settle_ok = settle != "monthly" or prof["monthly"]
            # ⑥ 区域适配(同城单: 同城承运商加分)
            region = 100 if (prof["sameCity"] or not same_city) else 40

            total = (speed * w_speed + prof["cost"] * w_cost
                     + (100 if insured_ok else 0) * w_other
                     + (100 if settle_ok else 30) * w_other
                     + region * w_other
                     + (100 if prof["heavy"] or weight <= 50 else 50) * w_other)
            eligible = not hard_fail
            if eligible:
                reasons.append(f"时效 {prof['speed']} 分/成本 {prof['cost']} 分"
                               f"(策略: {budget})")
            candidates.append({
                "carrier": code, "carrierName": prof["name"],
                "score": round(_clamp(total), 1), "eligible": eligible,
                "factors": {"speed": round(speed, 1), "cost": prof["cost"],
                            "insuredFit": 100 if insured_ok else 0,
                            "settleFit": 100 if settle_ok else 30,
                            "regionFit": region},
                "reasons": reasons,
            })

        candidates.sort(key=lambda c: (-c["score"], c["carrier"]))
        for i, c in enumerate(candidates, 1):
            c["rank"] = i
        best = next((c for c in candidates if c["eligible"]), None)

        result = {
            "success": True, "scorer": "logistics_routing", "modelVersion": MODEL_VERSION,
            "weightVersion": get_active_weight_version(scorer_key),
            "weight": weight, "budget": budget,
            "candidates": candidates,
            "recommendation": ({"carrier": best["carrier"],
                                "carrierName": best["carrierName"],
                                "score": best["score"],
                                "reason": "; ".join(best["reasons"])}
                               if best else {"carrier": "", "carrierName": "",
                                             "score": 0, "reason": "无满足硬约束的承运商"}),
            "confidence": _confidence(ctx, ["weight", "insuredValue", "settleMode"]),
            "scoredAt": ts(),
        }
        logger.info("ai_logistics_routing_scored weight=%s budget=%s best=%s",
                    weight, budget, result["recommendation"]["carrier"])
        return result


# ============================================================
# 4. 流量防作弊评分(模块 11 流量管理)
# ============================================================

class TrafficAntiFraudScorer:
    """流量防作弊评分: 识别推广员引流作弊(机器流量/羊毛党/刷量)

    7 因子加权 → 作弊分(0-100) → 3级风险 + 处置动作
    """

    WEIGHTS: ClassVar[dict] = {
        "burst": 0.20,            # 短时爆发
        "new_account": 0.20,      # 新账号占比
        "promoter_history": 0.15, # 历史作弊
        "conversion": 0.15,       # 转化率异常
        "source": 0.15,           # 来源集中度
        "night": 0.10,            # 凌晨占比
        "effective_rate": 0.05,   # 有效率过低
    }
    REQUIRED: ClassVar[list] = ["recentCount", "totalRecords", "newAccountRatio"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                promoterId: int,
                recentCount: int 近1小时引流数, avgIntervalSeconds: float 平均间隔,
                newAccountRatio: float 新账号占比(0-1),
                nightRatio: float 凌晨(0-5点)占比(0-1),
                conversionRate: float 转化率(0-1),
                uniqueSources: int 唯一来源数,
                totalRecords: int 总记录, effectiveRecords: int 有效记录,
                fraudCount: int 推广员历史作弊次数
            }
        """
        # 自学习层生效权重(champion), 无档案/异常时回退类默认值
        weights = await load_effective_weights("traffic_antifraud", self.WEIGHTS)

        f = {}
        # ① 爆发: 近1小时 >20 条满风险; 间隔 <10s 也视为脚本
        recent = int(ctx.get("recentCount") or 0)
        interval = float(ctx.get("avgIntervalSeconds") or 999)
        burst = max(recent * 5, 100 if interval < 10 else 0)
        f["burst"] = _factor("burst", "短时爆发", burst, weights["burst"],
                             f"近1小时 {recent} 条, 平均间隔 {interval:.0f}s")
        # ② 新账号占比
        new_ratio = float(ctx.get("newAccountRatio") or 0)
        f["new_account"] = _factor("new_account", "新账号占比",
                                   _clamp(new_ratio * 125), weights["new_account"],
                                   f"新账号占比 {new_ratio:.0%}")
        # ③ 历史作弊
        fraud_hist = int(ctx.get("fraudCount") or 0)
        f["promoter_history"] = _factor(
            "promoter_history", "历史作弊", _clamp(fraud_hist * 40),
            weights["promoter_history"], f"历史作弊 {fraud_hist} 次")
        # ④ 转化率异常(>0.9 视为刷量特征)
        conv = float(ctx.get("conversionRate") or 0)
        conv_score = _clamp((conv - 0.9) * 1000) if conv > 0.9 else 0
        f["conversion"] = _factor("conversion", "转化率异常", conv_score,
                                  weights["conversion"], f"转化率 {conv:.0%}")
        # ⑤ 来源集中度: 仅 1 个来源满风险, ≥5 个零风险
        sources = int(ctx.get("uniqueSources") or 0)
        src_score = _clamp((5 - sources) * 25) if sources > 0 else 0
        f["source"] = _factor("source", "来源集中度", src_score,
                              weights["source"], f"{sources} 个来源")
        # ⑥ 凌晨占比
        night = float(ctx.get("nightRatio") or 0)
        f["night"] = _factor("night", "凌晨占比", _clamp(night * 120),
                             weights["night"], f"凌晨占比 {night:.0%}")
        # ⑦ 有效率过低(<30% 视为垃圾流量)
        total = int(ctx.get("totalRecords") or 0)
        effective = int(ctx.get("effectiveRecords") or 0)
        eff_rate = (effective / total) if total > 0 else 1.0
        eff_score = _clamp((0.3 - eff_rate) * 200) if eff_rate < 0.3 else 0
        f["effective_rate"] = _factor("effective_rate", "有效率",
                                      eff_score, weights["effective_rate"],
                                      f"有效率 {eff_rate:.0%}")

        fraud = sum(x["contribution"] for x in f.values())
        level = _risk_level(fraud, medium_at=30.0, high_at=60.0)
        action = {"low": "pass", "medium": "review", "high": "block"}[level]
        result = {
            "success": True, "scorer": "traffic_antifraud", "modelVersion": MODEL_VERSION,
            "weightVersion": get_active_weight_version("traffic_antifraud"),
            "promoterId": ctx.get("promoterId"),
            "score": round(fraud, 1), "level": level, "levelName": LEVEL_NAMES[level],
            "action": action, "actionName": {"pass": "正常计入",
                                             "review": "冻结待审",
                                             "block": "拒绝并标记"}[action],
            "confidence": _confidence(ctx, self.REQUIRED),
            "factors": list(f.values()), "scoredAt": ts(),
        }
        logger.info("ai_traffic_antifraud_scored promoter=%s score=%s action=%s",
                    ctx.get("promoterId"), result["score"], action)
        return result


# ============================================================
# 5. 推广码防作弊评分(模块 29 推广码矩阵获利)
# ============================================================

class PromotionAntiFraudScorer:
    """推广码防作弊评分: 识别矩阵奖励套利(秒绑秒领/僵尸下级/脚本裂变)

    6 因子加权 → 作弊分(0-100) → 3级风险 + 奖励处置动作
    """

    WEIGHTS: ClassVar[dict] = {
        "loop_suspect": 0.20,   # 疑似环/自绑
        "bind_speed": 0.20,     # 绑定到领奖速度
        "zombie": 0.20,         # 僵尸下级占比
        "growth_burst": 0.15,   # 裂变速度异常
        "history": 0.15,        # 历史撤销/申诉
        "night": 0.10,          # 凌晨绑定占比
    }
    REQUIRED: ClassVar[list] = ["relationCount", "avgBindToRewardHours", "inactiveInviteeRatio"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                promoterId: int,
                relationCount: int 下级绑定数,
                avgBindToRewardHours: float 绑定到领奖平均时长(小时),
                inactiveInviteeRatio: float 僵尸下级占比(0-1, 无订单/无活跃),
                nightBindRatio: float 凌晨绑定占比(0-1),
                fastestHundredDays: int 最快发展百人所用天数,
                selfLoopSuspect: bool 疑似环/自绑(防环检查命中),
                revokedCount: int 历史撤销次数, appealCount: int 申诉次数
            }
        """
        f = {}
        # ① 环/自绑(硬特征)
        loop = bool(ctx.get("selfLoopSuspect", False))
        f["loop_suspect"] = _factor("loop_suspect", "环/自绑嫌疑",
                                    100 if loop else 0,
                                    self.WEIGHTS["loop_suspect"],
                                    "防环检查命中" if loop else "未命中")
        # ② 秒绑秒领: <1h 满风险, ≥72h 零风险
        speed = float(ctx.get("avgBindToRewardHours") if
                      ctx.get("avgBindToRewardHours") is not None else 72)
        f["bind_speed"] = _factor("bind_speed", "领奖速度",
                                  _clamp((72 - speed) / 71 * 100),
                                  self.WEIGHTS["bind_speed"],
                                  f"平均 {speed:.1f} 小时" + ("(秒绑秒领)" if speed < 1 else ""))
        # ③ 僵尸下级占比: ≥80% 满风险
        zombie = float(ctx.get("inactiveInviteeRatio") or 0)
        f["zombie"] = _factor("zombie", "僵尸下级", _clamp(zombie * 125),
                              self.WEIGHTS["zombie"], f"占比 {zombie:.0%}")
        # ④ 裂变爆发: <3 天百人满风险, ≥14 天零风险
        days = int(ctx.get("fastestHundredDays") or 14)
        f["growth_burst"] = _factor("growth_burst", "裂变速度",
                                    _clamp((14 - days) / 11 * 100),
                                    self.WEIGHTS["growth_burst"],
                                    f"最快百人 {days} 天" + ("(异常)" if days < 3 else ""))
        # ⑤ 历史撤销/申诉
        revoked = int(ctx.get("revokedCount") or 0)
        appeal = int(ctx.get("appealCount") or 0)
        f["history"] = _factor("history", "历史记录",
                               _clamp((revoked + appeal) * 35),
                               self.WEIGHTS["history"],
                               f"撤销 {revoked} 次/申诉 {appeal} 次")
        # ⑥ 凌晨绑定占比
        night = float(ctx.get("nightBindRatio") or 0)
        f["night"] = _factor("night", "凌晨绑定", _clamp(night * 120),
                             self.WEIGHTS["night"], f"占比 {night:.0%}")

        fraud = sum(x["contribution"] for x in f.values())
        level = _risk_level(fraud, medium_at=30.0, high_at=60.0)
        action = {"low": "pay", "medium": "hold", "high": "review"}[level]
        result = {
            "success": True, "scorer": "promotion_antifraud", "modelVersion": MODEL_VERSION,
            "promoterId": ctx.get("promoterId"),
            "score": round(fraud, 1), "level": level, "levelName": LEVEL_NAMES[level],
            "action": action, "actionName": {"pay": "正常发放",
                                             "hold": "暂缓发放",
                                             "review": "转人工仲裁"}[action],
            "confidence": _confidence(ctx, self.REQUIRED),
            "factors": list(f.values()), "scoredAt": ts(),
        }
        logger.info("ai_promotion_antifraud_scored promoter=%s score=%s action=%s",
                    ctx.get("promoterId"), result["score"], action)
        return result


# 统一入口(供路由层按类型分发)
SCORERS = {
    "order_risk": OrderRiskScorer(),
    "payment_routing": PaymentRoutingScorer(),
    "logistics_routing": LogisticsRoutingScorer(),
    "traffic_antifraud": TrafficAntiFraudScorer(),
    "promotion_antifraud": PromotionAntiFraudScorer(),
    "alliance_onboarding": None,   # 37号: 下方定义后回填
}


# ============================================================
# 6. 同盟入驻预审评分(模块 37 AI智能网站同盟)
# ============================================================

class AllianceOnboardingScorer:
    """同盟入驻预审评分: 超级会员入盟申请资质画像

    5 因子加权 → 资质分(0-100, 越高越优) → 3级 → 入盟处置动作
    (37号设计文档 §2.1: ≥80 快车道/60-79 人工重点审/<60 拒)

    与风险类评分器方向相反: 高分=优质, action 语义为入盟通道。
    """

    WEIGHTS: ClassVar[dict] = {
        "member_level": 0.25,      # 会员等级(超级会员线)
        "credit": 0.20,            # 信用分
        "realname": 0.15,          # 实名完整性
        "credentials": 0.25,       # 类目资质齐备度
        "competition": 0.15,       # 区域竞争度(竞争小加分)
    }
    REQUIRED: ClassVar[list] = ["memberLevel", "creditScore", "realnameVerified"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                applicantId: int 申请人会员ID,
                memberLevel: int 会员等级,
                creditScore: int 信用分(0-100, 缺省 85 中性),
                realnameVerified: bool 已实名,
                credentialsTotal: int 类目要求资质数,
                credentialsProvided: int 已提交资质数,
                gridOccupancy: int 所在网格同业商户数,
                gridCap: int 网格同业密度上限
            }
        """
        weights = await load_effective_weights("alliance_onboarding",
                                               self.WEIGHTS)
        f = {}
        # ① 会员等级: ≥6 满分, 达超级会员线(4) 及格, <4 记差
        level = int(ctx.get("memberLevel") or 0)
        level_score = 100 if level >= 6 else (
            70 + (level - 4) * 15 if level >= 4 else max(0, level * 15))
        f["member_level"] = _factor("member_level", "会员等级",
                                    level_score, weights["member_level"],
                                    f"Lv{level}(" +
                                    ("超级会员" if level >= 4 else "未达超级会员线") + ")")
        # ② 信用分: ≥95 满分, 60 及格线
        credit = float(ctx.get("creditScore") if
                       ctx.get("creditScore") is not None else 85)
        f["credit"] = _factor("credit", "信用分",
                              _clamp((credit - 60) / 35 * 100),
                              weights["credit"], f"信用分 {credit:.0f}")
        # ③ 实名完整性
        verified = bool(ctx.get("realnameVerified", False))
        f["realname"] = _factor("realname", "实名认证",
                                100 if verified else 0,
                                weights["realname"],
                                "已实名" if verified else "未实名")
        # ④ 类目资质齐备度
        required = int(ctx.get("credentialsTotal") or 0)
        provided = int(ctx.get("credentialsProvided") or 0)
        completeness = (provided / required * 100) if required else 100
        f["credentials"] = _factor("credentials", "资质齐备",
                                   _clamp(completeness),
                                   weights["credentials"],
                                   f"资质 {provided}/{required}")
        # ⑤ 区域竞争度: 空白网格满分, 满员零分
        occupancy = int(ctx.get("gridOccupancy") or 0)
        cap = int(ctx.get("gridCap") or 3)
        spare = max(0, cap - occupancy)
        f["competition"] = _factor("competition", "区域竞争",
                                   _clamp(spare / cap * 100) if cap else 100,
                                   weights["competition"],
                                   f"同业 {occupancy}/{cap}")

        total = sum(x["contribution"] for x in f.values())
        # 高分=优质: ≥80 快车道, 60-79 重点审, <60 拒
        if total >= 80:
            level_key, action = LEVEL_HIGH, "fast_track"
        elif total >= 60:
            level_key, action = LEVEL_MEDIUM, "manual_review"
        else:
            level_key, action = LEVEL_LOW, "reject"
        result = {
            "success": True, "scorer": "alliance_onboarding",
            "modelVersion": MODEL_VERSION,
            "applicantId": ctx.get("applicantId"),
            "score": round(total, 1), "level": level_key,
            "levelName": {LEVEL_HIGH: "优质", LEVEL_MEDIUM: "待核",
                          LEVEL_LOW: "不足"}[level_key],
            "action": action,
            "actionName": {"fast_track": "人工终审快车道",
                           "manual_review": "人工重点审核",
                           "reject": "预审拒绝"}[action],
            "confidence": _confidence(ctx, self.REQUIRED),
            "factors": list(f.values()), "scoredAt": ts(),
        }
        logger.info("ai_alliance_onboarding_scored applicant=%s score=%s "
                    "action=%s", ctx.get("applicantId"), result["score"],
                    action)
        return result


SCORERS["alliance_onboarding"] = AllianceOnboardingScorer()


# ============================================================
# 7. 同盟评价语义审评(模块 37 P1: 恶意差评/刷好评识别)
# ============================================================

class AllianceReviewScorer:
    """同盟评价审评: 恶意差评/刷好评识别(设计文档 §2.6)

    5 因子加权 → 违规分(0-100, 越高越可疑) → 3级 → 处置动作
    (low=正常展示 / medium=观察 / high=建议折叠)
    """

    WEIGHTS: ClassVar[dict] = {
        "extreme_words": 0.25,    # 情绪极端词
        "attack": 0.25,           # 人身攻击/辱骂
        "ad_spam": 0.20,          # 广告刷评
        "frequency": 0.15,        # 短时高频评价
        "score_deviation": 0.15,  # 与商户均分严重偏离
    }
    EXTREME_WORDS = ("垃圾", "骗子", "黑店", "无语", "恶心", "再也不会来")
    ATTACK_WORDS = ("傻", "蠢", "滚", "废物", "玩意", "货色", "东西吧")
    AD_SPAM_WORDS = ("加微信", "加V", "低价出", "代购", "优惠券链接", "点击链接")
    REQUIRED: ClassVar[list] = ["score", "content"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                reviewId: int, merchantId: int,
                score: int 星级(1-5),
                content: str 评价文本,
                merchantRatingAvg: float 商户当前均分(缺省取评价分),
                reviewerReviewsToday: int 该用户当日评价数(缺省 0)
            }
        """
        weights = await load_effective_weights("alliance_review",
                                               self.WEIGHTS)
        content = str(ctx.get("content") or "")
        review_score = int(ctx.get("score") or 0)
        f = {}
        # ① 极端情绪词
        extreme = [w for w in self.EXTREME_WORDS if w in content]
        f["extreme_words"] = _factor(
            "extreme_words", "极端情绪", _clamp(len(extreme) * 60),
            weights["extreme_words"],
            f"命中{extreme}" if extreme else "无")
        # ② 人身攻击
        attack = [w for w in self.ATTACK_WORDS if w in content]
        f["attack"] = _factor(
            "attack", "人身攻击", _clamp(len(attack) * 80),
            weights["attack"], f"命中{attack}" if attack else "无")
        # ③ 广告刷评
        spam = [w for w in self.AD_SPAM_WORDS if w in content]
        f["ad_spam"] = _factor(
            "ad_spam", "广告刷评", _clamp(len(spam) * 90),
            weights["ad_spam"], f"命中{spam}" if spam else "无")
        # ④ 短时高频(≥5 条/日 满分)
        today = int(ctx.get("reviewerReviewsToday") or 0)
        f["frequency"] = _factor(
            "frequency", "评价频率", _clamp(today * 20),
            weights["frequency"], f"当日 {today} 条")
        # ⑤ 与商户均分偏离(≥2 星差 记差评嫌疑; 全 5 星新号刷好评
        # 由 frequency 因子承担, 此处只测负向偏离)
        avg = float(ctx.get("merchantRatingAvg")
                    if ctx.get("merchantRatingAvg") is not None
                    else review_score)
        deviation = max(0.0, avg - review_score)
        f["score_deviation"] = _factor(
            "score_deviation", "分值偏离", _clamp(deviation * 40),
            weights["score_deviation"],
            f"评分{review_score} vs 商户均分{avg:.1f}")

        total = sum(x["contribution"] for x in f.values())
        # 评价语义场景更敏感: 极端词命中即应折叠(单一极端词因子
        # clamp 100×0.25=25, 叠加偏离即可过 45 线)
        level = _risk_level(total, medium_at=30.0, high_at=45.0)
        action = {"low": "show", "medium": "watch",
                  "high": "fold"}[level]
        return {
            "success": True, "scorer": "alliance_review",
            "modelVersion": MODEL_VERSION,
            "reviewId": ctx.get("reviewId"),
            "merchantId": ctx.get("merchantId"),
            "score": round(total, 1), "level": level,
            "levelName": LEVEL_NAMES[level],
            "action": action,
            "actionName": {"show": "正常展示", "watch": "观察",
                           "fold": "建议折叠"}[action],
            "confidence": _confidence(ctx, self.REQUIRED),
            "factors": list(f.values()), "scoredAt": ts(),
        }


SCORERS["alliance_review"] = AllianceReviewScorer()


# ============================================================
# 8. 商品上架预审(模块 38 P0: AI智能产品管理)
# ============================================================

class ProductGateScorer:
    """商品上架预审评分: 上架闸门质量分(38号设计文档 §2.5)

    5 因子加权 → 质量分(0-100, 越高越优) → 3级 → 上架处置动作
    (≥80 快车道/60-79 人工重点审/<60 拒), 阈值对齐 37号入盟口径。

    与风险类评分器方向相反: 高分=优质, action 语义为上架通道。
    """

    WEIGHTS: ClassVar[dict] = {
        "compliance": 0.25,      # 合规词命中(酒类广告法§23 口径)
        "completeness": 0.20,    # 信息完备度(必填字段)
        "image_quality": 0.25,   # 图片质量(AI 审图报告分, P1 接 vision)
        "price_sanity": 0.15,    # 价格合理性(同类目离群度)
        "category_risk": 0.15,   # 品类风险(酒精度数加权)
    }
    # 一审硬规则词表(与 36号/37号共享口径, 本地副本保证确定性)
    DRINKING_ACTION_WORDS = ("干杯", "一饮而尽", "不醉不归", "开怀畅饮",
                             "贪杯", "拼酒", "灌酒", "喝到")
    ABSOLUTE_WORDS = ("最好", "最佳", "第一", "顶级", "极品", "国宴",
                      "专供", "特效", "保健", "养生")
    REQUIRED_FIELDS = ("name", "price", "stock", "mainImage", "category")
    REQUIRED: ClassVar[list] = ["name", "price"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                productId: str 商品ID,
                name: str 商品名, description: str 描述,
                price: float 售价,
                categoryMedian: float 同类目中位价(缺省取 price 中性),
                alcohol: int 酒精度数(缺省 42 中性),
                missingFields: int 必填缺项数(缺省按字段自动探测),
                imageQuality: int 图片质量分 0-100(P0 由调用方给,
                    主图缺省 0/有主图缺省 70 中性; P1 接 vision 报告),
                imageCount: int 图片数(缺省按 mainImage 探测)
            }
        """
        weights = await load_effective_weights("product_gate",
                                               self.WEIGHTS)
        f = {}
        text = " ".join([str(ctx.get("name") or ""),
                         str(ctx.get("description") or "")])
        # ① 合规词: 饮酒动作×100 + 极限词×50, 命中即重扣
        hits_drink = [w for w in self.DRINKING_ACTION_WORDS if w in text]
        hits_abs = [w for w in self.ABSOLUTE_WORDS if w in text]
        compliance = _clamp(100 - len(hits_drink) * 100
                            - len(hits_abs) * 50)
        f["compliance"] = _factor(
            "compliance", "合规词", compliance, weights["compliance"],
            f"饮酒动作{hits_drink} 极限词{hits_abs}" if
            (hits_drink or hits_abs) else "无命中")
        # ② 信息完备度: 必填字段缺项(显式 missingFields 优先)
        missing = ctx.get("missingFields")
        if missing is None:
            missing = sum(1 for k in self.REQUIRED_FIELDS
                          if not ctx.get(k))
        missing = int(missing or 0)
        f["completeness"] = _factor(
            "completeness", "信息完备",
            _clamp(100 - missing * 25), weights["completeness"],
            f"缺项 {missing}/{len(self.REQUIRED_FIELDS)}")
        # ③ 图片质量: P0 规则轨(imageQuality 缺省按主图探测)
        quality = ctx.get("imageQuality")
        if quality is None:
            quality = 70 if ctx.get("mainImage") else 0
        f["image_quality"] = _factor(
            "image_quality", "图片质量", _clamp(float(quality)),
            weights["image_quality"],
            f"图片质量分 {float(quality):.0f}"
            f"(P1 接 vision 审图报告)")
        # ④ 价格合理性: 偏离同类目中位价 [0.5, 2] 区间外按倍数扣
        price = float(ctx.get("price") or 0)
        median = float(ctx.get("categoryMedian") or price or 1)
        if price > 0 and median > 0:
            ratio = price / median
            if 0.5 <= ratio <= 2.0:
                sanity = 100.0
            else:
                dev = (ratio / 2.0 if ratio > 2.0
                       else 0.5 / max(ratio, 1e-6))
                sanity = _clamp(100 - (dev - 1) * 100)
        else:
            sanity = 0.0
        f["price_sanity"] = _factor(
            "price_sanity", "价格合理", sanity, weights["price_sanity"],
            f"售价 {price:.0f} vs 中位 {median:.0f}"
            f"(比值 {price / median if median else 0:.2f})")
        # ⑤ 品类风险: 酒精度 >40° 线性加权(53° → 70 分)
        alcohol = int(ctx.get("alcohol") or 42)
        risk = _clamp(100 - max(0, alcohol - 40) * 10) \
            if ctx.get("alcohol") is not None else 100.0
        f["category_risk"] = _factor(
            "category_risk", "品类风险", risk, weights["category_risk"],
            f"{alcohol}°" if ctx.get("alcohol") is not None else "未标注度数")

        total = sum(x["contribution"] for x in f.values())
        # 高分=优质: ≥80 快车道, 60-79 重点审, <60 拒(37号入盟口径)
        if total >= 80:
            level_key, action = LEVEL_HIGH, "fast_track"
        elif total >= 60:
            level_key, action = LEVEL_MEDIUM, "manual_review"
        else:
            level_key, action = LEVEL_LOW, "reject"
        result = {
            "success": True, "scorer": "product_gate",
            "modelVersion": MODEL_VERSION,
            "productId": ctx.get("productId"),
            "score": round(total, 1), "level": level_key,
            "levelName": {LEVEL_HIGH: "优质", LEVEL_MEDIUM: "待核",
                          LEVEL_LOW: "不足"}[level_key],
            "action": action,
            "actionName": {"fast_track": "人工终审快车道",
                           "manual_review": "人工重点审核",
                           "reject": "预审拒绝"}[action],
            "confidence": _confidence(ctx, self.REQUIRED),
            "factors": list(f.values()), "scoredAt": ts(),
        }
        logger.info("ai_product_gate_scored product=%s score=%s action=%s",
                    ctx.get("productId"), result["score"], action)
        return result


SCORERS["product_gate"] = ProductGateScorer()


# ============================================================
# 9. 博主作品引流价值评分(模块 40 P0: DV博主跟随决策)
# ============================================================

class BloggerWorkScorer:
    """博主作品引流价值评分: 跟随决策质量分(40号设计文档 §2.4)

    5 因子加权 → 价值分(0-100, 越高越值得跟随) → 3级 → 决策动作
    (≥70 auto_follow / 50-70 manual_queue / <50 pass),
    阈值沿用 36号蹭点决策范式。

    与风险类评分器方向相反: 高分=优质, action 语义为跟随通道。
    """

    WEIGHTS: ClassVar[dict] = {
        "blogger_weight": 0.25,   # 博主权重(粉丝量级+互动率)
        "brand_fit": 0.25,        # 品牌契合(作品主题 vs 酒/礼场景)
        "work_heat": 0.20,        # 作品热度(互动数相对博主基线)
        "traffic_potential": 0.15,  # 引流潜力(推荐/测评/开箱语义)
        "competition": 0.15,      # 竞争密度(已跟随账号少 → 蓝海加分)
    }
    # 品牌契合词表(作品主题命中 → 契合度, 36号 _RELEVANCE_MAP 范式)
    BRAND_FIT_WORDS = ("酒", "白酒", "竹香", "品鉴", "微醺", "宴",
                       "礼", "送礼", "年货", "美食", "下酒菜")
    # 高转化语义(引流潜力因子)
    TRAFFIC_WORDS = ("推荐", "测评", "开箱", "排名", "清单", "合集",
                     "种草", "避坑", "指南")
    REQUIRED: ClassVar[list] = ["bloggerWeight"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                workId: int, bloggerId: int,
                bloggerWeight: float 博主权重(0-1, 池内 weight),
                engagementRate: float 互动率(0-1),
                brandHitCount: int 主题品牌词命中数(缺省按标题探测),
                title: str 作品标题, summary: str 作品文案,
                likes/comments/shares: int 互动数,
                bloggerBaselineLikes: int 该博主历史基线赞(缺省=likes),
                competitorCount: int 该作品下已跟随账号数(缺省 0)
            }
        """
        weights = await load_effective_weights("blogger_work_gate",
                                               self.WEIGHTS)
        f = {}
        # ① 博主权重: 权重(0-1)×70 + 互动率加分(≥5% 满分段)
        bw = float(ctx.get("bloggerWeight") or 0)
        engage = float(ctx.get("engagementRate") or 0)
        f["blogger_weight"] = _factor(
            "blogger_weight", "博主权重",
            _clamp(bw * 70 + engage * 600),
            weights["blogger_weight"],
            f"权重{bw:.1f} 互动率{engage:.1%}")
        # ② 品牌契合: 命中词数档位(0→5/1→55/2→75/3+→90)
        hits = ctx.get("brandHitCount")
        if hits is None:
            text = " ".join([str(ctx.get("title") or ""),
                             str(ctx.get("summary") or "")])
            hits = sum(1 for w in self.BRAND_FIT_WORDS if w in text)
        hits = int(hits or 0)
        fit = 5.0 if hits == 0 else (55.0 if hits == 1
                                     else (75.0 if hits == 2 else 90.0))
        f["brand_fit"] = _factor(
            "brand_fit", "品牌契合", fit, weights["brand_fit"],
            f"命中{hits}词")
        # ③ 作品热度: 互动总量相对博主基线放大倍数(≥3倍满分)
        likes = int(ctx.get("likes") or 0)
        comments = int(ctx.get("comments") or 0)
        shares = int(ctx.get("shares") or 0)
        baseline = int(ctx.get("bloggerBaselineLikes") or likes or 1)
        total = likes + comments * 3 + shares * 5
        base_total = max(1, baseline)
        ratio = total / base_total
        f["work_heat"] = _factor(
            "work_heat", "作品热度", _clamp(ratio / 3 * 100),
            weights["work_heat"],
            f"互动{total} vs 基线{base_total}(×{ratio:.1f})")
        # ④ 引流潜力: 高转化语义命中(每词 40 分, 封顶)
        text2 = " ".join([str(ctx.get("title") or ""),
                          str(ctx.get("summary") or "")])
        t_hits = [w for w in self.TRAFFIC_WORDS if w in text2]
        f["traffic_potential"] = _factor(
            "traffic_potential", "引流潜力",
            _clamp(len(t_hits) * 40), weights["traffic_potential"],
            f"命中{t_hits}" if t_hits else "无高转化语义")
        # ⑤ 竞争密度: 已跟随账号 0 → 蓝海 100, ≥5 → 0
        competitors = int(ctx.get("competitorCount") or 0)
        comp = _clamp(100 - competitors * 20)
        f["competition"] = _factor(
            "competition", "竞争密度", comp, weights["competition"],
            f"已跟随{competitors}号" + ("(蓝海)" if competitors == 0
                                       else ""))

        total_score = sum(x["contribution"] for x in f.values())
        if total_score >= 70:
            level_key, action = LEVEL_HIGH, "auto_follow"
        elif total_score >= 50:
            level_key, action = LEVEL_MEDIUM, "manual_queue"
        else:
            level_key, action = LEVEL_LOW, "pass"
        result = {
            "success": True, "scorer": "blogger_work_gate",
            "modelVersion": MODEL_VERSION,
            "workId": ctx.get("workId"),
            "bloggerId": ctx.get("bloggerId"),
            "score": round(total_score, 1), "level": level_key,
            "levelName": {LEVEL_HIGH: "优质", LEVEL_MEDIUM: "待核",
                          LEVEL_LOW: "不足"}[level_key],
            "action": action,
            "actionName": {"auto_follow": "全自动跟随",
                           "manual_queue": "人工确认队列",
                           "pass": "跳过留痕"}[action],
            "confidence": _confidence(ctx, self.REQUIRED),
            "factors": list(f.values()), "scoredAt": ts(),
        }
        logger.info("ai_blogger_work_scored work=%s blogger=%s "
                    "score=%s action=%s", ctx.get("workId"),
                    ctx.get("bloggerId"), result["score"], action)
        return result


SCORERS["blogger_work_gate"] = BloggerWorkScorer()


# ============================================================
# 10. 代驾司机资格审查评分(模块 41 P0: 超级会员注册代驾员)
# ============================================================

class DriverApplicationScorer:
    """代驾司机资格审查评分: 申请质量分(41号设计文档 §2.2)

    5 因子加权 → 资格分(0-100, 越高越可靠) → 3级 → 决策动作
    (≥70 approved 自动通过 / 50-70 manual_review 人工复核 /
     <50 rejected 拒绝), 阈值沿用 36/40号范式。

    高分=优质, action 语义为资格审查通道; 驾龄<3年等硬门槛
    在服务层前置拒绝, 评分器仅输出质量画像。
    """

    WEIGHTS: ClassVar[dict] = {
        "completeness": 0.30,    # 材料齐全性(必填字段/格式校验)
        "driving_years": 0.25,   # 驾龄与准驾车型
        "member_credit": 0.20,   # 会员信用(竹信分+投诉率)
        "account_health": 0.15,  # 账户健康(注册时长/实名等级)
        "consistency": 0.10,     # 声明一致性(年龄vs驾龄交叉校验)
    }
    # 准驾车型档位(C1/C2 常规, B/A 系满分——代驾场景均覆盖小客车)
    LICENSE_CLASS_SCORE = {"C1": 85.0, "C2": 80.0,
                           "B1": 95.0, "B2": 95.0, "A1": 100.0, "A2": 100.0}
    REQUIRED: ClassVar[list] = ["idNumber", "licenseNumber", "drivingYears",
                                "age"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                applicationId: int, memberId: int,
                idNumber: str 身份证号, licenseNumber: str 驾照号,
                licenseClass: str 准驾车型(缺省 C1),
                drivingYears: int 驾龄(年),
                age: int 年龄, ageVerified: bool 是否实名认证,
                registerHours: float 注册至今年时数(缺省 720 中性),
                bambooScore: int 竹信分(0-1000, 缺省 600 中性),
                complaintRate: float 历史投诉率(0-1, 缺省 0),
                accidentFreeDecl: bool 无重大事故声明,
                drunkFreeDecl: bool 无酒驾记录声明,
                emergencyContact: str 紧急联系人
            }
        """
        weights = await load_effective_weights("driver_application_gate",
                                               self.WEIGHTS)
        f = {}
        # ① 材料齐全性: 必填材料逐项计分 + 双声明加分
        materials = [
            ctx.get("idNumber"), ctx.get("licenseNumber"),
            ctx.get("drivingYears"), ctx.get("emergencyContact"),
            ctx.get("accidentFreeDecl"), ctx.get("drunkFreeDecl"),
        ]
        present = sum(1 for m in materials if m not in (None, "", 0))
        # 身份证/驾照格式长度校验(18 位身份证 / 12 位驾照口径)
        fmt_ok = (
            len(str(ctx.get("idNumber") or "")) == 18
            and len(str(ctx.get("licenseNumber") or "")) >= 10
        )
        f["completeness"] = _factor(
            "completeness", "材料齐全性",
            _clamp(present / len(materials) * (100 if fmt_ok else 60)),
            weights["completeness"],
            f"材料{present}/{len(materials)}" + ("" if fmt_ok else " 证件格式存疑"))

        # ② 驾龄与准驾车型: ≥5 年满分, 3-4 年线性; 车型档位折算
        years = float(ctx.get("drivingYears") or 0)
        years_score = _clamp((years - 3) / 2 * 100) if years >= 3 else 0.0
        license_class = str(ctx.get("licenseClass") or "C1").upper()
        class_score = self.LICENSE_CLASS_SCORE.get(license_class, 70.0)
        f["driving_years"] = _factor(
            "driving_years", "驾龄与准驾车型",
            _clamp(years_score * 0.7 + class_score * 0.3),
            weights["driving_years"],
            f"驾龄{years:.0f}年 {license_class}")

        # ③ 会员信用: 竹信分归一(0-1000 → 0-100) - 投诉率惩罚
        bamboo = float(ctx.get("bambooScore") or 600)
        complaint_rate = float(ctx.get("complaintRate") or 0)
        f["member_credit"] = _factor(
            "member_credit", "会员信用",
            _clamp(bamboo / 10 - complaint_rate * 200),
            weights["member_credit"],
            f"竹信分{bamboo:.0f} 投诉率{complaint_rate:.1%}")

        # ④ 账户健康: 注册时长(≥1 年满分) + 实名认证
        register_hours = float(ctx.get("registerHours") or 720)
        age_verified = bool(ctx.get("ageVerified"))
        f["account_health"] = _factor(
            "account_health", "账户健康",
            _clamp(min(register_hours / 8760, 1) * (100 if age_verified else 70)),
            weights["account_health"],
            f"注册{register_hours / 720:.0f}月" + (" 已实名" if age_verified else " 未实名"))

        # ⑤ 声明一致性: 年龄 ≥ 驾龄 + 18 为合理, 差值越大越扣分
        age = float(ctx.get("age") or 0)
        if age > 0 and years > 0:
            gap = age - years - 18
            consist = _clamp(100 - max(0, -gap) * 50 - max(0, gap - 20) * 2)
        else:
            consist = 50.0
        f["consistency"] = _factor(
            "consistency", "声明一致性", consist, weights["consistency"],
            f"年龄{age:.0f} 驾龄{years:.0f}"
            + ("(合理)" if consist >= 60 else "(存疑)"))

        total_score = sum(x["contribution"] for x in f.values())
        if total_score >= 70:
            level_key, action = LEVEL_HIGH, "approved"
        elif total_score >= 50:
            level_key, action = LEVEL_MEDIUM, "manual_review"
        else:
            level_key, action = LEVEL_LOW, "rejected"
        result = {
            "success": True, "scorer": "driver_application_gate",
            "modelVersion": MODEL_VERSION,
            "applicationId": ctx.get("applicationId"),
            "memberId": ctx.get("memberId"),
            "score": round(total_score, 1), "level": level_key,
            "levelName": {LEVEL_HIGH: "优质", LEVEL_MEDIUM: "待核",
                          LEVEL_LOW: "不足"}[level_key],
            "action": action,
            "actionName": {"approved": "自动通过",
                           "manual_review": "人工复核队列",
                           "rejected": "拒绝"}[action],
            "confidence": _confidence(ctx, self.REQUIRED),
            "factors": list(f.values()), "scoredAt": ts(),
        }
        logger.info("ai_driver_application_scored application=%s member=%s "
                    "score=%s action=%s", ctx.get("applicationId"),
                    ctx.get("memberId"), result["score"], action)
        return result


SCORERS["driver_application_gate"] = DriverApplicationScorer()


# ============================================================
# 11. 代驾派单评分(模块 41 P1: 智能派单引擎 AI 层)
# ============================================================

class RideDispatchScorer:
    """代驾派单适配评分: 司机-乘客匹配质量分(41号设计文档 §2.3)

    5 因子加权 → 适配分(0-100, 越高越优先派) → 3级 → 决策动作
    (≥70 dispatch 直接派单 / 50-70 dispatch_backup 派次优+备选通知 /
     <50 escalate 平台直发), 阈值沿用 36/40/41号范式。

    高分=优质, action 语义为派单通道; 规则层硬过滤(半径/评分/在忙)
    在服务层前置完成, 评分器仅对入围司机输出适配画像。
    """

    WEIGHTS: ClassVar[dict] = {
        "distance": 0.30,       # 接驾距离(≤1km 满分, 线性衰减至半径边界)
        "rating": 0.25,         # 服务评分(五星归一)
        "reliability": 0.20,    # 接单可靠度(接单率/取消率)
        "track_cost": 0.15,     # 轨道成本(自营满分/加盟居中/直发兜底)
        "load_balance": 0.10,   # 负载均衡(当日已接单数, 防过载)
    }
    # 轨道成本档位(自营低本站直付 / 加盟平台抽佣 / 直发平台定价最高)
    TRACK_COST_SCORE = {"self": 100.0, "partner": 60.0, "platform": 30.0}
    REQUIRED: ClassVar[list] = ["driverId", "distanceKm", "rating"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                driverId: int, track: str,
                distanceKm: float 接驾直线距离(规则层已过滤超半径),
                rating: float 服务评分(0-5),
                acceptRate: float 接单率(0-1, 缺省 0.9),
                cancelRate: float 取消率(0-1, 缺省 0.05),
                todayOrders: int 当日已接单数(缺省 0),
                dispatchRadiusKm: float 派单半径(缺省 5, 距离衰减标尺)
            }
        """
        weights = await load_effective_weights("ride_dispatch", self.WEIGHTS)
        f = {}
        # ① 接驾距离: ≤1km 满分, 1-半径线性衰减(超半径已被规则层过滤)
        dist = float(ctx.get("distanceKm") or 0)
        radius = float(ctx.get("dispatchRadiusKm") or 5)
        if dist <= 1.0:
            dist_score = 100.0
        else:
            dist_score = _clamp(100 - (dist - 1) / max(0.1, radius - 1) * 100)
        f["distance"] = _factor(
            "distance", "接驾距离", dist_score, weights["distance"],
            f"{dist:.2f}km" + ("(满分档)" if dist <= 1 else ""))

        # ② 服务评分: 五星归一
        rating = float(ctx.get("rating") or 0)
        f["rating"] = _factor(
            "rating", "服务评分", _clamp(rating / 5 * 100),
            weights["rating"], f"{rating:.1f}星")

        # ③ 接单可靠度: 接单率 70% + 完成率(1-取消率) 30%
        accept_rate = float(ctx.get("acceptRate") if
                            ctx.get("acceptRate") is not None else 0.9)
        cancel_rate = float(ctx.get("cancelRate") if
                            ctx.get("cancelRate") is not None else 0.05)
        f["reliability"] = _factor(
            "reliability", "接单可靠度",
            _clamp(accept_rate * 70 + (1 - cancel_rate) * 30),
            weights["reliability"],
            f"接单率{accept_rate:.0%} 取消率{cancel_rate:.1%}")

        # ④ 轨道成本: 自营 100 / 加盟 60 / 直发 30
        track = str(ctx.get("track") or "platform")
        track_score = self.TRACK_COST_SCORE.get(track, 30.0)
        f["track_cost"] = _factor(
            "track_cost", "轨道成本", track_score, weights["track_cost"],
            f"{track}轨")

        # ⑤ 负载均衡: 当日 0 单满分, 每单 -15
        today = int(ctx.get("todayOrders") or 0)
        f["load_balance"] = _factor(
            "load_balance", "负载均衡", _clamp(100 - today * 15),
            weights["load_balance"], f"当日{today}单")

        total_score = sum(x["contribution"] for x in f.values())
        if total_score >= 70:
            level_key, action = LEVEL_HIGH, "dispatch"
        elif total_score >= 50:
            level_key, action = LEVEL_MEDIUM, "dispatch_backup"
        else:
            level_key, action = LEVEL_LOW, "escalate"
        result = {
            "success": True, "scorer": "ride_dispatch",
            "modelVersion": MODEL_VERSION,
            "driverId": ctx.get("driverId"),
            "track": track,
            "score": round(total_score, 1), "level": level_key,
            "levelName": {LEVEL_HIGH: "优质", LEVEL_MEDIUM: "待核",
                          LEVEL_LOW: "不足"}[level_key],
            "action": action,
            "actionName": {"dispatch": "直接派单",
                           "dispatch_backup": "次优选派+备选通知",
                           "escalate": "升级平台直发"}[action],
            "confidence": _confidence(ctx, self.REQUIRED),
            "factors": list(f.values()), "scoredAt": ts(),
        }
        logger.info("ai_ride_dispatch_scored driver=%s score=%s action=%s",
                    ctx.get("driverId"), result["score"], action)
        return result


SCORERS["ride_dispatch"] = RideDispatchScorer()


# ============================================================
# 12. 代驾评价审评评分(模块 41 P3: 双向评价垃圾过滤)
# ============================================================

class RideReviewScorer:
    """代驾评价审评: 恶意差评/刷好评识别(41号设计文档 §2.4 行后,
    37号 AllianceReviewScorer 范式平移)

    5 因子加权 → 违规分(0-100, 越高越可疑) → 3级 → 处置动作
    (low=show 正常展示 / medium=watch 观察 / high=fold 折叠)。

    折叠评价不回写司机评分; 双向通用(乘客评司机/司机评乘客)。
    """

    WEIGHTS: ClassVar[dict] = {
        "extreme_words": 0.25,    # 情绪极端词
        "attack": 0.25,           # 人身攻击/辱骂
        "ad_spam": 0.20,          # 广告刷评
        "frequency": 0.15,        # 短时高频评价
        "score_deviation": 0.15,   # 与司机当前评分严重偏离
    }
    EXTREME_WORDS = ("垃圾", "骗子", "黑店", "无语", "恶心",
                     "再也不会用", "投诉到底")
    ATTACK_WORDS = ("傻", "蠢", "滚", "废物", "玩意", "货色",
                    "酒鬼", "马路杀手")
    AD_SPAM_WORDS = ("加微信", "加V", "低价出", "代驾券收", "点击链接",
                     "优惠券链接", "接私单")
    REQUIRED: ClassVar[list] = ["score", "content"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                reviewId: int, rideId: str, direction: str,
                driverId: int, memberId: int,
                score: int 星级(1-5),
                content: str 评价文本,
                driverRating: float 司机当前评分(缺省取评价分),
                reviewerReviewsToday: int 该用户当日评价数(缺省 0)
            }
        """
        weights = await load_effective_weights("ride_review", self.WEIGHTS)
        content = str(ctx.get("content") or "")
        review_score = int(ctx.get("score") or 0)
        f = {}
        # ① 极端情绪词
        extreme = [w for w in self.EXTREME_WORDS if w in content]
        f["extreme_words"] = _factor(
            "extreme_words", "极端情绪", _clamp(len(extreme) * 60),
            weights["extreme_words"],
            f"命中{extreme}" if extreme else "无")
        # ② 人身攻击
        attack = [w for w in self.ATTACK_WORDS if w in content]
        f["attack"] = _factor(
            "attack", "人身攻击", _clamp(len(attack) * 80),
            weights["attack"], f"命中{attack}" if attack else "无")
        # ③ 广告刷评
        spam = [w for w in self.AD_SPAM_WORDS if w in content]
        f["ad_spam"] = _factor(
            "ad_spam", "广告刷评", _clamp(len(spam) * 90),
            weights["ad_spam"], f"命中{spam}" if spam else "无")
        # ④ 短时高频(≥5 条/日 满分)
        today = int(ctx.get("reviewerReviewsToday") or 0)
        f["frequency"] = _factor(
            "frequency", "评价频率", _clamp(today * 20),
            weights["frequency"], f"当日 {today} 条")
        # ⑤ 与司机当前评分偏离(负向偏离计差评嫌疑)
        driver_rating = float(ctx.get("driverRating")
                              if ctx.get("driverRating") is not None
                              else review_score)
        deviation = max(0.0, driver_rating - review_score)
        f["score_deviation"] = _factor(
            "score_deviation", "分值偏离", _clamp(deviation * 40),
            weights["score_deviation"],
            f"评分{review_score} vs 司机{driver_rating:.1f}")

        total = sum(x["contribution"] for x in f.values())
        # 评价语义场景更敏感(对齐 37号阈值): 30 观察 / 45 折叠
        level = _risk_level(total, medium_at=30.0, high_at=45.0)
        action = {"low": "show", "medium": "watch",
                  "high": "fold"}[level]
        return {
            "success": True, "scorer": "ride_review",
            "modelVersion": MODEL_VERSION,
            "reviewId": ctx.get("reviewId"),
            "rideId": ctx.get("rideId"),
            "direction": ctx.get("direction"),
            "driverId": ctx.get("driverId"),
            "memberId": ctx.get("memberId"),
            "score": round(total, 1), "level": level,
            "levelName": LEVEL_NAMES[level],
            "action": action,
            "actionName": {"show": "正常展示", "watch": "观察",
                           "fold": "折叠"}[action],
            "confidence": _confidence(ctx, self.REQUIRED),
            "factors": list(f.values()), "scoredAt": ts(),
        }


SCORERS["ride_review"] = RideReviewScorer()


# ============================================================
# 13. 无感开票决策评分(模块 42 P0: 订单完成自动开票)
# ============================================================

class InvoiceDecisionScorer:
    """无感开票决策评分: 自动开票可行性分(42号设计文档 §2.2)

    4 因子加权 → 可行分(0-100, 越高越适合自动开) → 3级 → 决策动作
    (≥70 auto_issue 自动开具 / 50-70 manual_queue 待确认 /
     <50 reject 拦截留痕)。

    高分=可自动, action 语义为开票通道; 抬头缺失(collect)与
    金额下限在服务层前置判定, 评分器仅输出可行性画像。
    """

    WEIGHTS: ClassVar[dict] = {
        "title_confidence": 0.30,   # 抬头置信度(默认抬头+使用次数)
        "amount_reasonable": 0.25,  # 金额合理性(相对会员历史区间)
        "frequency": 0.20,          # 开票频次(24h 窗口, 拆分嫌疑)
        "order_risk": 0.25,        # 订单风险(order_risk 决策信号)
    }
    REQUIRED: ClassVar[list] = ["orderId", "memberId", "titleConfidence",
                               "amount"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                orderId: str, memberId: int,
                titleConfidence: float 抬头置信度(0-1, 服务层由
                    默认抬头+使用次数折算),
                amount: float 订单实付,
                memberAvgAmount: float 会员历史均单(缺省=当次金额,
                    首单中性),
                invoices24h: int 该会员 24h 已开票数(缺省 0),
                freqThreshold: int 高频阈值(缺省 5),
                orderRiskAction: str order_risk 决策动作
                    (pass/review/block, 缺省 pass)
            }
        """
        weights = await load_effective_weights("invoice_decision_gate",
                                               self.WEIGHTS)
        f = {}
        # ① 抬头置信度: 0-1 线性归一
        confidence = float(ctx.get("titleConfidence") or 0)
        f["title_confidence"] = _factor(
            "title_confidence", "抬头置信度",
            _clamp(confidence * 100), weights["title_confidence"],
            f"置信度{confidence:.0%}")

        # ② 金额合理性: 相对会员均单 0.5-2 倍区间满分,
        #    超出线性衰减(异常大额/小额)
        amount = float(ctx.get("amount") or 0)
        avg = float(ctx.get("memberAvgAmount")
                    if ctx.get("memberAvgAmount") else amount)
        if avg > 0 and amount > 0:
            ratio = amount / avg
            if 0.5 <= ratio <= 2.0:
                amount_score = 100.0
            elif ratio < 0.5:
                amount_score = _clamp(ratio / 0.5 * 100)
            else:
                amount_score = _clamp(100 - (ratio - 2) * 25)
        else:
            amount_score = 50.0
        f["amount_reasonable"] = _factor(
            "amount_reasonable", "金额合理性", amount_score,
            weights["amount_reasonable"],
            f"¥{amount:.0f} vs 均单¥{avg:.0f}")

        # ③ 开票频次: 24h 窗口, 达阈值 0 分
        invoices_24h = int(ctx.get("invoices24h") or 0)
        threshold = int(ctx.get("freqThreshold") or 5)
        freq_score = _clamp(100 - invoices_24h / max(1, threshold)
                            * 100)
        f["frequency"] = _factor(
            "frequency", "开票频次", freq_score, weights["frequency"],
            f"24h内{invoices_24h}张(阈值{threshold})")

        # ④ 订单风险: order_risk 决策信号直通降档
        risk_action = str(ctx.get("orderRiskAction") or "pass")
        risk_score = {"pass": 100.0, "review": 50.0,
                      "block": 0.0}.get(risk_action, 50.0)
        f["order_risk"] = _factor(
            "order_risk", "订单风险", risk_score,
            weights["order_risk"], f"风控{risk_action}")

        total_score = sum(x["contribution"] for x in f.values())
        if total_score >= 70:
            level_key, action = LEVEL_HIGH, "auto_issue"
        elif total_score >= 50:
            level_key, action = LEVEL_MEDIUM, "manual_queue"
        else:
            level_key, action = LEVEL_LOW, "reject"
        result = {
            "success": True, "scorer": "invoice_decision_gate",
            "modelVersion": MODEL_VERSION,
            "orderId": ctx.get("orderId"),
            "memberId": ctx.get("memberId"),
            "score": round(total_score, 1), "level": level_key,
            "levelName": {LEVEL_HIGH: "可自动", LEVEL_MEDIUM: "待确认",
                          LEVEL_LOW: "拦截"}[level_key],
            "action": action,
            "actionName": {"auto_issue": "自动开具",
                           "manual_queue": "待确认队列",
                           "reject": "拦截留痕"}[action],
            "confidence": _confidence(ctx, self.REQUIRED),
            "factors": list(f.values()), "scoredAt": ts(),
        }
        logger.info("ai_invoice_decision_scored order=%s member=%s "
                    "score=%s action=%s", ctx.get("orderId"),
                    ctx.get("memberId"), result["score"], action)
        return result


SCORERS["invoice_decision_gate"] = InvoiceDecisionScorer()


# ============================================================
#  43号·AI智能安全管理 P0: 威胁网关评分(第 26 档案)
# ============================================================

class ThreatGateScorer:
    """安全威胁网关评分: 请求威胁分(43号设计文档 §2.2)

    6 因子加权 → 威胁分(0-100, 越高越安全) → 4级 → 处置动作
    (≥70 allow 放行 / 50-70 throttle 渐进延迟 /
     25-50 challenge 挑战验证 / <25 block 封禁)。

    高分=可信, action 语义为处置通道; IP 冷启动给中性信誉
    (ip_reputation 因子), 频次/注入特征由安全网关中间件预计算。
    """

    WEIGHTS: ClassVar[dict] = {
        "ip_reputation": 0.20,      # IP 信誉(历史攻击/封禁/冷却)
        "request_rate": 0.20,        # 频次(IP+会员双维度滑动窗口)
        "payload_signature": 0.25,   # 注入特征(SQLi/XSS/遍历/扫描器)
        "path_anomaly": 0.10,        # 路径异常(探针路径/深度遍历)
        "identity_risk": 0.15,       # 身份风险(未认证打敏感端点/越权)
        "geo_time": 0.10,            # 时空异常(凌晨高频/密度跳变)
    }
    REQUIRED: ClassVar[list] = ["ip", "reputation", "requestCount"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                ip: str 客户端 IP,
                memberId: int 会员ID(缺省 0=未认证),
                reputation: float IP 信誉分(0-100, 冷启动 80),
                requestCount: int 窗口内请求数,
                rateLimit: int 窗口上限(缺省 120),
                payloadSignature: float 注入特征分(0-100, 服务层
                    预计算, 缺省 100=干净),
                pathAnomaly: float 路径异常分(0-100, 缺省 100),
                identityRisk: float 身份风险分(0-100, 缺省 100),
                hour: int 请求时段(0-23, 缺省取当前)
            }
        """
        weights = await load_effective_weights("security_threat_gate",
                                               self.WEIGHTS)
        f = {}
        # ① IP 信誉: 0-100 线性直通(blacklisted 直通 0)
        reputation = float(ctx.get("reputation")
                           if ctx.get("reputation") is not None
                           else 80.0)
        f["ip_reputation"] = _factor(
            "ip_reputation", "IP信誉", _clamp(reputation),
            weights["ip_reputation"], f"信誉分{reputation:.0f}")

        # ② 频次: 窗口内达到上限 0 分, 80% 起线性衰减
        count = int(ctx.get("requestCount") or 0)
        limit = max(1, int(ctx.get("rateLimit") or 120))
        if count <= limit * 0.8:
            rate_score = 100.0
        else:
            rate_score = _clamp(
                (1 - (count - limit * 0.8) / (limit * 0.2)) * 100)
        f["request_rate"] = _factor(
            "request_rate", "请求频次", rate_score,
            weights["request_rate"],
            f"窗口{count}/{limit}")

        # ③ 注入特征: 服务层预计算直通(0-100, 低=有特征)
        payload = float(ctx.get("payloadSignature")
                        if ctx.get("payloadSignature") is not None
                        else 100.0)
        f["payload_signature"] = _factor(
            "payload_signature", "注入特征", _clamp(payload),
            weights["payload_signature"],
            "干净" if payload >= 100 else f"特征分{payload:.0f}")

        # ④ 路径异常: 服务层预计算直通
        path_anom = float(ctx.get("pathAnomaly")
                         if ctx.get("pathAnomaly") is not None
                         else 100.0)
        f["path_anomaly"] = _factor(
            "path_anomaly", "路径异常", _clamp(path_anom),
            weights["path_anomaly"],
            "正常" if path_anom >= 100 else f"异常分{path_anom:.0f}")

        # ⑤ 身份风险: 未认证/越权信号直通
        identity = float(ctx.get("identityRisk")
                        if ctx.get("identityRisk") is not None
                        else 100.0)
        f["identity_risk"] = _factor(
            "identity_risk", "身份风险", _clamp(identity),
            weights["identity_risk"],
            "可信" if identity >= 100 else f"风险分{identity:.0f}")

        # ⑥ 时空异常: 凌晨 0-5 点递减(高危时段), 其余满分
        hour = int(ctx.get("hour")
                   if ctx.get("hour") is not None else _now_hour())
        if 0 <= hour <= 5:
            geo_score = 40.0
        else:
            geo_score = 100.0
        f["geo_time"] = _factor(
            "geo_time", "时空异常", geo_score, weights["geo_time"],
            f"时段{hour}时")

        total_score = sum(x["contribution"] for x in f.values())
        if total_score >= 70:
            level_key, action = LEVEL_HIGH, "allow"
        elif total_score >= 50:
            level_key, action = LEVEL_MEDIUM, "throttle"
        elif total_score >= 25:
            level_key, action = LEVEL_LOW, "challenge"
        else:
            level_key, action = LEVEL_LOW, "block"

        # 确定性攻击硬规则(设计文档 §2.2 注): 注入特征/探针路径为
        # 近零误报信号, 不参与加权合议, 直接触发处置档位下限——
        #   任一特征命中或探针路径 → 至少 challenge(单次 SQLi 尝试
        #     即使 IP 干净/频次正常也不放行)
        #   多类特征叠加(特征分≤0, 如 SQLi+扫描器) → block
        #   (与 IP 黑名单直封同口径的硬防线, 防单信号被合议淹没)
        severity = {"allow": 0, "throttle": 1,
                    "challenge": 2, "block": 3}
        if payload <= 0.0:
            hard = "block"
        elif payload < 100.0 or path_anom < 100.0:
            hard = "challenge"
        else:
            hard = None
        if hard and severity[hard] > severity[action]:
            action = hard
        result = {
            "success": True, "scorer": "security_threat_gate",
            "modelVersion": MODEL_VERSION,
            "ip": ctx.get("ip"),
            "memberId": ctx.get("memberId"),
            "score": round(total_score, 1), "level": level_key,
            "levelName": {LEVEL_HIGH: "可信", LEVEL_MEDIUM: "可疑",
                          LEVEL_LOW: "高危"}[level_key],
            "action": action,
            "actionName": {"allow": "放行", "throttle": "渐进减速",
                           "challenge": "挑战验证",
                           "block": "封禁拦截"}[action],
            "confidence": _confidence(ctx, self.REQUIRED),
            "factors": list(f.values()), "scoredAt": ts(),
        }
        logger.info("ai_threat_gate_scored ip=%s member=%s score=%s "
                    "action=%s", ctx.get("ip"), ctx.get("memberId"),
                    result["score"], action)
        return result


SCORERS["security_threat_gate"] = ThreatGateScorer()
