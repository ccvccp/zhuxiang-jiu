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
from datetime import datetime, UTC

from core.helpers import ts

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

    WEIGHTS = {
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
    REQUIRED = ["bambooScore", "registerHours", "orderAmount", "historyOrders"]

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

        f = {}
        # 竹信分: <300 高风险, ≥700 零风险
        credit = float(ctx.get("bambooScore") if ctx.get("bambooScore") is not None else 600)
        f["credit"] = _factor("credit", "会员信用", _clamp((700 - credit) / 4),
                              self.WEIGHTS["credit"], f"竹信分 {credit:.0f}")
        # 注册时长: <24h 满风险, ≥720h(30天) 零风险
        hours = float(ctx.get("registerHours") or 0)
        f["register_age"] = _factor(
            "register_age", "注册时长", _clamp((720 - hours) / 7.2),
            self.WEIGHTS["register_age"],
            f"注册 {hours:.0f} 小时" + ("(新号)" if hours < 24 else ""))
        # 金额: ≥10000 满风险, 线性
        f["amount"] = _factor("amount", "订单金额", _clamp(amount / 100),
                              self.WEIGHTS["amount"], f"金额 ¥{amount:,.2f}")
        # 数量: ≥20 件满风险
        qty = int(ctx.get("totalQuantity") or 0)
        f["quantity"] = _factor("quantity", "购买数量", _clamp(qty * 5),
                                self.WEIGHTS["quantity"], f"共 {qty} 件")
        # 历史取消率
        hist = int(ctx.get("historyOrders") or 0)
        cancels = int(ctx.get("historyCancels") or 0)
        rate = (cancels / hist) if hist > 0 else 0.0
        f["cancel_rate"] = _factor("cancel_rate", "历史取消率", _clamp(rate * 150),
                                   self.WEIGHTS["cancel_rate"],
                                   f"取消率 {rate:.0%}({cancels}/{hist})")
        # 地址完整性
        addr_ok = bool(ctx.get("addressComplete", True))
        f["address"] = _factor("address", "收货地址", 0 if addr_ok else 85,
                               self.WEIGHTS["address"],
                               "完整" if addr_ok else "地址缺失关键字段")
        # 备注风险词
        remark = str(ctx.get("remark") or "")
        hit = [w for w in self.RISK_WORDS if w in remark]
        f["remark"] = _factor("remark", "备注风险词", 100 if hit else 0,
                              self.WEIGHTS["remark"],
                              f"命中 {hit}" if hit else "无风险词")
        # 下单时段(0-5 点凌晨)
        hour = int(ctx.get("orderHour", _now_hour()))
        night = hour < 6
        f["time_pattern"] = _factor("time_pattern", "下单时段",
                                    70 if night else 0,
                                    self.WEIGHTS["time_pattern"],
                                    f"{hour} 点" + ("(凌晨)" if night else ""))

        risk = sum(x["contribution"] for x in f.values())
        level = _risk_level(risk)
        action = {"low": "pass", "medium": "review", "high": "block"}[level]
        result = {
            "success": True, "scorer": "order_risk", "modelVersion": MODEL_VERSION,
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

    WEIGHTS = {"availability": 0.30, "limit_fit": 0.25, "cost": 0.20,
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
        w_speed = _BUDGET_WEIGHTS[budget]["speed"]
        w_cost = _BUDGET_WEIGHTS[budget]["cost"]
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

    WEIGHTS = {
        "burst": 0.20,            # 短时爆发
        "new_account": 0.20,      # 新账号占比
        "promoter_history": 0.15, # 历史作弊
        "conversion": 0.15,       # 转化率异常
        "source": 0.15,           # 来源集中度
        "night": 0.10,            # 凌晨占比
        "effective_rate": 0.05,   # 有效率过低
    }
    REQUIRED = ["recentCount", "totalRecords", "newAccountRatio"]

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
        f = {}
        # ① 爆发: 近1小时 >20 条满风险; 间隔 <10s 也视为脚本
        recent = int(ctx.get("recentCount") or 0)
        interval = float(ctx.get("avgIntervalSeconds") or 999)
        burst = max(recent * 5, 100 if interval < 10 else 0)
        f["burst"] = _factor("burst", "短时爆发", burst, self.WEIGHTS["burst"],
                             f"近1小时 {recent} 条, 平均间隔 {interval:.0f}s")
        # ② 新账号占比
        new_ratio = float(ctx.get("newAccountRatio") or 0)
        f["new_account"] = _factor("new_account", "新账号占比",
                                   _clamp(new_ratio * 125), self.WEIGHTS["new_account"],
                                   f"新账号占比 {new_ratio:.0%}")
        # ③ 历史作弊
        fraud_hist = int(ctx.get("fraudCount") or 0)
        f["promoter_history"] = _factor(
            "promoter_history", "历史作弊", _clamp(fraud_hist * 40),
            self.WEIGHTS["promoter_history"], f"历史作弊 {fraud_hist} 次")
        # ④ 转化率异常(>0.9 视为刷量特征)
        conv = float(ctx.get("conversionRate") or 0)
        conv_score = _clamp((conv - 0.9) * 1000) if conv > 0.9 else 0
        f["conversion"] = _factor("conversion", "转化率异常", conv_score,
                                  self.WEIGHTS["conversion"], f"转化率 {conv:.0%}")
        # ⑤ 来源集中度: 仅 1 个来源满风险, ≥5 个零风险
        sources = int(ctx.get("uniqueSources") or 0)
        src_score = _clamp((5 - sources) * 25) if sources > 0 else 0
        f["source"] = _factor("source", "来源集中度", src_score,
                              self.WEIGHTS["source"], f"{sources} 个来源")
        # ⑥ 凌晨占比
        night = float(ctx.get("nightRatio") or 0)
        f["night"] = _factor("night", "凌晨占比", _clamp(night * 120),
                             self.WEIGHTS["night"], f"凌晨占比 {night:.0%}")
        # ⑦ 有效率过低(<30% 视为垃圾流量)
        total = int(ctx.get("totalRecords") or 0)
        effective = int(ctx.get("effectiveRecords") or 0)
        eff_rate = (effective / total) if total > 0 else 1.0
        eff_score = _clamp((0.3 - eff_rate) * 200) if eff_rate < 0.3 else 0
        f["effective_rate"] = _factor("effective_rate", "有效率",
                                      eff_score, self.WEIGHTS["effective_rate"],
                                      f"有效率 {eff_rate:.0%}")

        fraud = sum(x["contribution"] for x in f.values())
        level = _risk_level(fraud, medium_at=30.0, high_at=60.0)
        action = {"low": "pass", "medium": "review", "high": "block"}[level]
        result = {
            "success": True, "scorer": "traffic_antifraud", "modelVersion": MODEL_VERSION,
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

    WEIGHTS = {
        "loop_suspect": 0.20,   # 疑似环/自绑
        "bind_speed": 0.20,     # 绑定到领奖速度
        "zombie": 0.20,         # 僵尸下级占比
        "growth_burst": 0.15,   # 裂变速度异常
        "history": 0.15,        # 历史撤销/申诉
        "night": 0.10,          # 凌晨绑定占比
    }
    REQUIRED = ["relationCount", "avgBindToRewardHours", "inactiveInviteeRatio"]

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
}
