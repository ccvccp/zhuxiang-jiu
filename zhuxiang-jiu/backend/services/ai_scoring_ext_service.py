"""AI 语义评分层·第二批(v7.2 → v7.3 升级: 剩余 B 级模块补齐 AI 语义)

为 8 个剩余 B 级模块(02会员/03积分/08信息/12钱包/14团购/17后台/18合同/19财务)
补齐 AI 评分语义, 沿用第一批(ai_scoring_service)的多因子加权评分模式:

    输入上下文 → 多因子评分(0-100) → 等级映射(3-4级) → 决策动作 → 因子明细+置信度

评分器清单(第二批):
    MemberProfileScorer    会员智能画像评分(02): 7因子 → 价值分 → high_value/standard/at_risk
    PointsRiskScorer       积分防薅羊毛评分(03): 6因子 → 风险分 → pass/review/block
    MessageContentScorer   信息内容审核评分(08): 6因子 → 风险分 → pass/review/reject
    WithdrawRiskScorer     提现风控评分(12): 6因子 → 风险分 → auto_approve/manual_review/freeze
    GroupbuyQualifyScorer  团购资格评分(14): 5因子 → 资格分 → T3/T2/T1/rejected
    AdminOperationScorer   后台操作风险评分(17): 5因子 → 风险分 → allow/confirm_2fa/block
    AgreementRiskScorer    合同条款风险评分(18): 5因子 → 风险分 → low/medium/high(+修订建议)
    FinanceAnomalyScorer   财务异常检测评分(19): 5因子 → 异常分 → normal/attention/alert

排除说明: 30用户认证按架构文档 v7.2 决议保留规则引擎(确定性安全规则, 不做 AI 语义化)。

设计约定(与第一批一致):
    - 纯函数式评分(输入 dict → 输出 dict), 不落库, 不改现有模块(零侵入)
    - 全 async(项目约定); 异常约定: ValueError → 409(输入非法)
    - 置信度 = 输入字段完整度(缺失字段越多置信度越低, 下限 0.3)
    - 复用第一批的评分原语(_clamp/_factor/_risk_level/_confidence)
"""

import logging
from typing import ClassVar

from core.helpers import ts
from services.ai_scoring_service import (
    _clamp, _confidence, _factor, _now_hour, _risk_level,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-ext"


# ============================================================
# 1. 会员智能画像评分(模块 02 会员管理)
# ============================================================

class MemberProfileScorer:
    """会员价值画像评分: 消费能力/活跃度/信用 → 价值分层与运营动作

    7 因子加权 → 价值分(0-100, 越高越有价值) → 3级分层 + 运营动作
    """

    WEIGHTS: ClassVar[dict] = {
        "profile": 0.10,       # 资料完整度
        "account_age": 0.10,   # 账户年龄
        "activity": 0.15,      # 月活跃度
        "consumption": 0.20,   # 月均消费
        "repurchase": 0.15,    # 复购率
        "refund": 0.15,        # 退款率(逆向因子)
        "credit": 0.15,        # 竹信分
    }
    REQUIRED: ClassVar[list] = ["monthlyLogins", "monthlyConsumption", "bambooScore"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                profileFieldCount: int 已填资料字段数, profileFieldTotal: int 总字段数,
                accountAgeDays: float 账户年龄(天),
                monthlyLogins: int 月登录次数, monthlyConsumption: float 月消费金额,
                repurchaseRate: float 复购率(0-1), refundRate: float 退款率(0-1),
                bambooScore: int 竹信分(0-1000)
            }

        Raises:
            ValueError: 月消费金额为负
        """
        consumption = float(ctx.get("monthlyConsumption") or 0)
        if consumption < 0:
            raise ValueError("月消费金额不能为负")

        f = {}
        # 资料完整度: 已填字段占比 × 100
        filled = int(ctx.get("profileFieldCount") or 0)
        total = int(ctx.get("profileFieldTotal") or 8)
        ratio = (filled / total) if total > 0 else 0.0
        f["profile"] = _factor("profile", "资料完整度", _clamp(ratio * 100),
                               self.WEIGHTS["profile"], f"资料 {filled}/{total}")
        # 账户年龄: ≥365 天满分, 线性
        days = float(ctx.get("accountAgeDays") or 0)
        f["account_age"] = _factor("account_age", "账户年龄", _clamp(days / 3.65),
                                   self.WEIGHTS["account_age"], f"注册 {days:.0f} 天")
        # 活跃度: 月登录 ≥20 次满分
        logins = int(ctx.get("monthlyLogins") or 0)
        f["activity"] = _factor("activity", "月活跃度", _clamp(logins * 5),
                                self.WEIGHTS["activity"], f"月登录 {logins} 次")
        # 消费能力: 月消费 ≥5000 满分, 线性
        f["consumption"] = _factor("consumption", "消费能力", _clamp(consumption / 50),
                                   self.WEIGHTS["consumption"], f"月消费 ¥{consumption:,.0f}")
        # 复购率: 越高越好
        repurchase = float(ctx.get("repurchaseRate") or 0)
        f["repurchase"] = _factor("repurchase", "复购率", _clamp(repurchase * 100),
                                  self.WEIGHTS["repurchase"], f"复购率 {repurchase:.0%}")
        # 退款率: 逆向因子(≥30% 零分)
        refund = float(ctx.get("refundRate") or 0)
        f["refund"] = _factor("refund", "退款率", _clamp(100 - refund * 333),
                              self.WEIGHTS["refund"], f"退款率 {refund:.0%}")
        # 竹信分: ≥700 满分, 线性
        credit = float(ctx.get("bambooScore") if ctx.get("bambooScore") is not None else 600)
        f["credit"] = _factor("credit", "会员信用", _clamp(credit / 7),
                              self.WEIGHTS["credit"], f"竹信分 {credit:.0f}")

        value = round(sum(x["contribution"] for x in f.values()), 1)
        if value >= 70:
            tier, action = "high_value", "专属客服/优先权益/精准营销推送"
        elif value >= 40:
            tier, action = "standard", "常规运营策略"
        else:
            tier, action = "at_risk", "流失预警/挽留策略(优惠券+回访)"

        result = {
            "success": True, "scorer": "member_profile", "module": "02会员管理",
            "score": value, "tier": tier,
            "tierName": {"high_value": "高价值会员", "standard": "普通会员",
                         "at_risk": "风险/流失会员"}[tier],
            "action": action, "factors": list(f.values()),
            "confidence": _confidence(ctx, self.REQUIRED),
            "modelVersion": MODEL_VERSION, "scoredAt": ts(),
        }
        logger.info("会员画像评分: score=%s tier=%s confidence=%s",
                    value, tier, result["confidence"])
        return result


# ============================================================
# 2. 积分防薅羊毛评分(模块 03 会员积分)
# ============================================================

class PointsRiskScorer:
    """积分防薅羊毛评分: 获取爆发/小号关联/异常行为 → 薅羊毛识别

    6 因子加权 → 风险分(0-100, 越高越危险) → 3级风险 + 处置动作
    """

    WEIGHTS: ClassVar[dict] = {
        "earn_burst": 0.25,       # 当日获取爆发
        "redeem_frequency": 0.15, # 兑换频率
        "channel_concentration": 0.15,  # 获取渠道集中度
        "device_accounts": 0.20,  # 同设备账号数(小号关联)
        "violations": 0.15,       # 历史违规
        "night_activity": 0.10,   # 凌晨操作占比
    }
    REQUIRED: ClassVar[list] = ["todayEarned", "sameDeviceAccounts"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                todayEarned: int 当日获取积分, dailyEarnCap: int 日获取上限(缺省 200),
                dailyRedeemCount: int 当日兑换次数,
                singleChannelRatio: float 单渠道获取占比(0-1),
                sameDeviceAccounts: int 同设备关联账号数(含本人),
                violationCount: int 历史违规次数, nightActionRatio: float 凌晨操作占比(0-1)
            }

        Raises:
            ValueError: 当日积分获取量为负
        """
        today_earned = float(ctx.get("todayEarned") or 0)
        if today_earned < 0:
            raise ValueError("当日积分获取量不能为负")

        f = {}
        # 获取爆发: 当日获取/上限, ≥1 倍满分
        cap = float(ctx.get("dailyEarnCap") or 200)
        cap = cap if cap > 0 else 200.0
        f["earn_burst"] = _factor("earn_burst", "获取爆发",
                                  _clamp(today_earned / cap * 100),
                                  self.WEIGHTS["earn_burst"],
                                  f"当日获取 {today_earned:.0f} 分/上限 {cap:.0f} 分")
        # 兑换频率: 当日兑换 ≥5 次满分
        redeems = int(ctx.get("dailyRedeemCount") or 0)
        f["redeem_frequency"] = _factor("redeem_frequency", "兑换频率",
                                        _clamp(redeems * 20),
                                        self.WEIGHTS["redeem_frequency"],
                                        f"当日兑换 {redeems} 次")
        # 渠道集中度: 单渠道占比 ≥80% 满分
        concentration = float(ctx.get("singleChannelRatio") or 0)
        f["channel_concentration"] = _factor(
            "channel_concentration", "渠道集中度", _clamp(concentration * 125),
            self.WEIGHTS["channel_concentration"], f"单渠道占比 {concentration:.0%}")
        # 小号关联: 同设备账号 ≥3 个满分(1=仅本人 零风险)
        devices = int(ctx.get("sameDeviceAccounts") or 1)
        f["device_accounts"] = _factor(
            "device_accounts", "小号关联", _clamp((devices - 1) * 50),
            self.WEIGHTS["device_accounts"], f"同设备 {devices} 个账号")
        # 历史违规: 每次 25 分
        violations = int(ctx.get("violationCount") or 0)
        f["violations"] = _factor("violations", "历史违规", _clamp(violations * 25),
                                  self.WEIGHTS["violations"], f"违规 {violations} 次")
        # 凌晨操作: ≥40% 满分
        night = float(ctx.get("nightActionRatio") or 0)
        f["night_activity"] = _factor("night_activity", "凌晨操作",
                                      _clamp(night * 250),
                                      self.WEIGHTS["night_activity"],
                                      f"凌晨占比 {night:.0%}")

        risk = round(sum(x["contribution"] for x in f.values()), 1)
        level = _risk_level(risk, medium_at=30.0, high_at=60.0)
        action = {"low": "正常积分行为", "medium": "积分暂扣, 转人工核实",
                  "high": "积分冻结 + 账号风控"}[level]

        result = {
            "success": True, "scorer": "points_risk", "module": "03会员积分",
            "score": risk, "level": level, "levelName": {"low": "低风险",
            "medium": "中风险", "high": "高风险"}[level],
            "action": action, "factors": list(f.values()),
            "confidence": _confidence(ctx, self.REQUIRED),
            "modelVersion": MODEL_VERSION, "scoredAt": ts(),
        }
        logger.info("积分风控评分: score=%s level=%s confidence=%s",
                    risk, level, result["confidence"])
        return result


# ============================================================
# 3. 信息内容审核评分(模块 08 信息管理)
# ============================================================

class MessageContentScorer:
    """信息内容审核评分: 敏感词/垃圾链接/重复群发 → 内容风险识别

    6 因子加权 → 风险分(0-100, 越高越危险) → 3级风险 + 审核动作
    """

    WEIGHTS: ClassVar[dict] = {
        "sensitive_words": 0.30,  # 敏感词命中
        "link_spam": 0.15,        # 链接堆砌
        "duplicate_content": 0.15,  # 重复内容
        "send_frequency": 0.15,   # 发送频率
        "length_anomaly": 0.10,   # 长度异常
        "night_send": 0.15,       # 凌晨发送
    }
    SENSITIVE_WORDS = ("代开发票", "博彩", "贷款包过", "添加微信", "兼职日结",
                       "刷单", "返现", "高利贷", "股票内幕")
    REQUIRED: ClassVar[list] = ["content"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                content: str 消息内容(必填),
                linkCount: int 链接数量(缺省自动统计 http 出现次数),
                duplicateRatio: float 与历史内容重复度(0-1),
                hourlySendCount: int 当小时发送数, sendHour: int 发送小时(0-23, 缺省当前)
            }

        Raises:
            ValueError: 消息内容为空
        """
        content = str(ctx.get("content") or "").strip()
        if not content:
            raise ValueError("消息内容不能为空")

        # 敏感词自动识别(允许显式传入 sensitiveHitCount 覆盖)
        explicit_hits = ctx.get("sensitiveHitCount")
        if explicit_hits is not None:
            hit_count = int(explicit_hits)
        else:
            hit_count = sum(content.count(w) for w in self.SENSITIVE_WORDS)
        hit_words = [w for w in self.SENSITIVE_WORDS if w in content]

        # 发送时间(缺省取当前小时)
        hour = int(ctx.get("sendHour") if ctx.get("sendHour") is not None else _now_hour())

        f = {}
        # 敏感词: 每次 33 分
        f["sensitive_words"] = _factor(
            "sensitive_words", "敏感词", _clamp(hit_count * 33),
            self.WEIGHTS["sensitive_words"],
            f"命中 {hit_count} 处" + (f"({','.join(hit_words[:3])})" if hit_words else ""))
        # 链接堆砌: ≥3 条满分
        links = int(ctx.get("linkCount") if ctx.get("linkCount") is not None
                    else content.count("http"))
        f["link_spam"] = _factor("link_spam", "链接堆砌", _clamp(links * 40),
                                 self.WEIGHTS["link_spam"], f"外链 {links} 条")
        # 重复内容: ≥60% 满分
        duplicate = float(ctx.get("duplicateRatio") or 0)
        f["duplicate_content"] = _factor("duplicate_content", "重复内容",
                                         _clamp(duplicate * 166),
                                         self.WEIGHTS["duplicate_content"],
                                         f"重复度 {duplicate:.0%}")
        # 发送频率: 当小时 ≥10 条满分
        sends = int(ctx.get("hourlySendCount") or 0)
        f["send_frequency"] = _factor("send_frequency", "发送频率",
                                      _clamp(sends * 10),
                                      self.WEIGHTS["send_frequency"],
                                      f"当小时 {sends} 条")
        # 长度异常: 空白或超 2000 字
        length = len(content)
        length_score = 100 if (length < 5 or length > 2000) else 0
        f["length_anomaly"] = _factor("length_anomaly", "长度异常", length_score,
                                      self.WEIGHTS["length_anomaly"], f"长度 {length} 字")
        # 凌晨发送: 0-6 点满分
        night_score = 100 if 0 <= hour < 6 else 0
        f["night_send"] = _factor("night_send", "发送时段", night_score,
                                  self.WEIGHTS["night_send"], f"{hour} 点发送")

        risk = round(sum(x["contribution"] for x in f.values()), 1)
        level = _risk_level(risk, medium_at=30.0, high_at=60.0)
        action = {"low": "自动放行", "medium": "转人工审核",
                  "high": "拦截发送并通知发送者"}[level]

        result = {
            "success": True, "scorer": "message_content", "module": "08信息管理",
            "score": risk, "level": level, "levelName": {"low": "低风险",
            "medium": "中风险", "high": "高风险"}[level],
            "action": action, "factors": list(f.values()),
            "confidence": _confidence(ctx, self.REQUIRED),
            "modelVersion": MODEL_VERSION, "scoredAt": ts(),
        }
        logger.info("内容审核评分: score=%s level=%s hits=%s confidence=%s",
                    risk, level, hit_count, result["confidence"])
        return result


# ============================================================
# 4. 提现风控评分(模块 12 钱包盈利)
# ============================================================

class WithdrawRiskScorer:
    """提现风控评分: 金额占比/频率/收益来源 → 提现欺诈识别

    6 因子加权 → 风险分(0-100, 越高越危险) → 3级风险 + 处置动作
    对齐 wallet_service 双阈值惯例(自动打款/人工审核/冻结上报)。
    """

    WEIGHTS: ClassVar[dict] = {
        "amount_ratio": 0.20,      # 提现占比
        "frequency": 0.15,         # 提现频率
        "account_age": 0.15,       # 账户年龄
        "income_anomaly": 0.20,    # 收益来源异常
        "history_rejects": 0.15,   # 历史驳回
        "status_flags": 0.15,      # 账户状态
    }
    REQUIRED: ClassVar[list] = ["amount", "balance"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                amount: float 提现金额(必填, >0), balance: float 可用余额(必填),
                monthlyWithdrawCount: int 当月提现次数,
                accountAgeDays: float 账户年龄(天),
                abnormalIncomeRatio: float 异常收益占比(0-1),
                rejectedCount: int 历史驳回次数,
                accountFrozen: bool 账户是否冻结, identityVerified: bool 是否实名
            }

        Raises:
            ValueError: 提现金额非法或超过可用余额
        """
        amount = float(ctx.get("amount") or 0)
        balance = float(ctx.get("balance") or 0)
        if amount <= 0:
            raise ValueError("提现金额必须大于 0")
        if amount > balance:
            raise ValueError("提现金额超过可用余额")

        f = {}
        # 提现占比: 一次性提取全部余额风险高(盗号/跑路信号), 线性
        ratio = amount / balance if balance > 0 else 1.0
        f["amount_ratio"] = _factor("amount_ratio", "提现占比",
                                    _clamp(ratio * 100),
                                    self.WEIGHTS["amount_ratio"],
                                    f"提取余额的 {ratio:.0%}")
        # 提现频率: 当月 ≥5 次满分
        count = int(ctx.get("monthlyWithdrawCount") or 0)
        f["frequency"] = _factor("frequency", "提现频率", _clamp(count * 20),
                                 self.WEIGHTS["frequency"], f"当月 {count} 次")
        # 账户年龄: <7 天满分, ≥90 天零分
        days = float(ctx.get("accountAgeDays") or 0)
        f["account_age"] = _factor(
            "account_age", "账户年龄", _clamp(max(0.0, (90 - days) / 90 * 100)),
            self.WEIGHTS["account_age"], f"开户 {days:.0f} 天")
        # 收益来源异常: ≥50% 满分
        abnormal = float(ctx.get("abnormalIncomeRatio") or 0)
        f["income_anomaly"] = _factor("income_anomaly", "收益来源异常",
                                      _clamp(abnormal * 200),
                                      self.WEIGHTS["income_anomaly"],
                                      f"异常收益占比 {abnormal:.0%}")
        # 历史驳回: 每次 30 分
        rejects = int(ctx.get("rejectedCount") or 0)
        f["history_rejects"] = _factor("history_rejects", "历史驳回",
                                       _clamp(rejects * 30),
                                       self.WEIGHTS["history_rejects"],
                                       f"驳回 {rejects} 次")
        # 账户状态: 冻结满分; 未实名 +60; 正常 0
        status_score = 0.0
        flags = []
        if ctx.get("accountFrozen"):
            status_score = 100.0
            flags.append("已冻结")
        elif ctx.get("identityVerified") is False:
            status_score = 60.0
            flags.append("未实名")
        f["status_flags"] = _factor("status_flags", "账户状态", status_score,
                                    self.WEIGHTS["status_flags"],
                                    "异常状态: " + ("、".join(flags) if flags else "无"))

        risk = round(sum(x["contribution"] for x in f.values()), 1)
        if risk < 25:
            level, action = "low", "自动打款"
        elif risk < 55:
            level, action = "medium", "转人工审核"
        else:
            level, action = "high", "冻结提现并上报风控"

        result = {
            "success": True, "scorer": "withdraw_risk", "module": "12钱包盈利",
            "score": risk, "level": level, "levelName": {"low": "低风险",
            "medium": "中风险", "high": "高风险"}[level],
            "action": action, "factors": list(f.values()),
            "confidence": _confidence(ctx, self.REQUIRED),
            "modelVersion": MODEL_VERSION, "scoredAt": ts(),
        }
        logger.info("提现风控评分: score=%s level=%s confidence=%s",
                    risk, level, result["confidence"])
        return result


# ============================================================
# 5. 团购资格评分(模块 14 团购模块)
# ============================================================

class GroupbuyQualifyScorer:
    """团购资格评分: 资质/采购历史/付款信用 → 团购档位推荐

    5 因子加权 → 资格分(0-100, 越高越优) → 4级档位(T3/T2/T1/拒绝)
    对齐 groupbuy_service 团购类型与 SVIP 资格校验惯例。
    """

    WEIGHTS: ClassVar[dict] = {
        "qualification_docs": 0.25,  # 资质材料
        "purchase_history": 0.20,    # 采购历史
        "payment_credit": 0.20,      # 付款信用
        "fulfillment": 0.20,         # 履约记录
        "demand_match": 0.15,        # 需求规模
    }
    REQUIRED: ClassVar[list] = ["qualificationDocs", "annualPurchaseAmount"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                qualificationDocs: int 资质材料数(营业执照/食品经营许可等, 0-5),
                annualPurchaseAmount: float 年采购金额,
                onTimePaymentRatio: float 按期付款率(0-1),
                violationCount: int 历史违约次数,
                targetQuantity: int 意向团购数量(件)
            }

        Raises:
            ValueError: 团购数量非法
        """
        quantity = int(ctx.get("targetQuantity") or 0)
        if quantity < 1:
            raise ValueError("团购数量至少为 1 件")

        f = {}
        # 资质材料: 5 项齐全满分, 线性
        docs = int(ctx.get("qualificationDocs") or 0)
        f["qualification_docs"] = _factor(
            "qualification_docs", "资质材料", _clamp(docs * 20),
            self.WEIGHTS["qualification_docs"], f"已提交 {docs}/5 项")
        # 采购历史: 年采购 ≥5 万满分, 线性
        purchase = float(ctx.get("annualPurchaseAmount") or 0)
        f["purchase_history"] = _factor("purchase_history", "采购历史",
                                        _clamp(purchase / 500),
                                        self.WEIGHTS["purchase_history"],
                                        f"年采购 ¥{purchase:,.0f}")
        # 付款信用: 按期付款率
        on_time = float(ctx.get("onTimePaymentRatio") or 0)
        f["payment_credit"] = _factor("payment_credit", "付款信用",
                                      _clamp(on_time * 100),
                                      self.WEIGHTS["payment_credit"],
                                      f"按期付款率 {on_time:.0%}")
        # 履约记录: 每次违约扣 40
        violations = int(ctx.get("violationCount") or 0)
        f["fulfillment"] = _factor("fulfillment", "履约记录",
                                   _clamp(100 - violations * 40),
                                   self.WEIGHTS["fulfillment"],
                                   f"违约 {violations} 次")
        # 需求规模: ≥50 件满分, 线性
        f["demand_match"] = _factor("demand_match", "需求规模",
                                    _clamp(quantity * 2),
                                    self.WEIGHTS["demand_match"],
                                    f"意向 {quantity} 件")

        qualify = round(sum(x["contribution"] for x in f.values()), 1)
        if qualify >= 80:
            tier, action = "T3", "核心客户档: 专属价格 + 大客户经理对接"
        elif qualify >= 60:
            tier, action = "T2", "进阶档: 团购折扣 + 优先排产"
        elif qualify >= 40:
            tier, action = "T1", "基础档: 标准团购价"
        else:
            tier, action = "rejected", "暂不开放团购, 建议补充资质后重新申请"

        result = {
            "success": True, "scorer": "groupbuy_qualify", "module": "14团购模块",
            "score": qualify, "tier": tier,
            "tierName": {"T3": "T3 核心档", "T2": "T2 进阶档", "T1": "T1 基础档",
                         "rejected": "不予通过"}[tier],
            "action": action, "factors": list(f.values()),
            "confidence": _confidence(ctx, self.REQUIRED),
            "modelVersion": MODEL_VERSION, "scoredAt": ts(),
        }
        logger.info("团购资格评分: score=%s tier=%s confidence=%s",
                    qualify, tier, result["confidence"])
        return result


# ============================================================
# 6. 后台操作风险评分(模块 17 后台管理)
# ============================================================

class AdminOperationScorer:
    """后台敏感操作风险评分: 操作敏感级/时段/频率 → 越权与误操作识别

    5 因子加权 → 风险分(0-100, 越高越危险) → 3级风险 + 处置动作
    对齐 admin_service 审计日志 action 类型(reset_password/assign_permissions/delete 等)。
    """

    WEIGHTS: ClassVar[dict] = {
        "sensitivity": 0.30,      # 操作敏感级
        "off_hours": 0.15,        # 非常规时段
        "frequency_burst": 0.15,  # 操作频率
        "permission_gap": 0.25,   # 越权嫌疑
        "pending_review": 0.15,   # 复核缺失
    }
    # 操作敏感级映射(对齐 admin_service 审计 action)
    SENSITIVITY: ClassVar[dict] = {
        "delete": 100, "reset_password": 100, "assign_permissions": 100,
        "update": 60, "update_price": 80, "refund": 80,
        "create": 30, "read": 10, "login": 10, "login_success": 10,
    }
    REQUIRED: ClassVar[list] = ["operationType"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                operationType: str 操作类型(必填, 见 SENSITIVITY 映射表),
                operationHour: int 操作小时(0-23, 缺省当前), isWeekend: bool 是否周末,
                operationsLast10Min: int 近 10 分钟操作数,
                operatesOnSelf: bool 是否操作自身账号(自我提权嫌疑),
                hasSecondReviewer: bool 是否已有第二复核人
            }

        Raises:
            ValueError: 未知操作类型
        """
        op_type = str(ctx.get("operationType") or "").strip()
        if op_type not in self.SENSITIVITY:
            raise ValueError(f"未知操作类型: {op_type}")

        hour = int(ctx.get("operationHour") if ctx.get("operationHour") is not None
                   else _now_hour())

        f = {}
        # 操作敏感级: 查映射表
        base = self.SENSITIVITY[op_type]
        f["sensitivity"] = _factor("sensitivity", "操作敏感级", base,
                                   self.WEIGHTS["sensitivity"],
                                   f"操作类型 {op_type}")
        # 非常规时段: 0-6 点满分, 周末 50
        if 0 <= hour < 6:
            off_score, off_detail = 100.0, f"凌晨 {hour} 点"
        elif ctx.get("isWeekend"):
            off_score, off_detail = 50.0, "周末"
        else:
            off_score, off_detail = 0.0, "工作时间"
        f["off_hours"] = _factor("off_hours", "操作时段", off_score,
                                 self.WEIGHTS["off_hours"], off_detail)
        # 操作频率: 近 10 分钟 ≥10 次满分
        burst = int(ctx.get("operationsLast10Min") or 0)
        f["frequency_burst"] = _factor("frequency_burst", "操作频率",
                                       _clamp(burst * 10),
                                       self.WEIGHTS["frequency_burst"],
                                       f"近 10 分钟 {burst} 次")
        # 越权嫌疑: 操作自身账号满分(如给自己提权/重置自己密码)
        self_op = bool(ctx.get("operatesOnSelf"))
        f["permission_gap"] = _factor("permission_gap", "越权嫌疑",
                                      100.0 if self_op else 0.0,
                                      self.WEIGHTS["permission_gap"],
                                      "操作对象为自身账号" if self_op else "操作对象为他人/资源")
        # 复核缺失: 敏感操作无第二人复核满分
        has_reviewer = ctx.get("hasSecondReviewer")
        if has_reviewer is None:
            review_score, review_detail = 50.0, "复核状态未知"
        elif has_reviewer:
            review_score, review_detail = 0.0, "已有复核"
        else:
            review_score, review_detail = 100.0, "敏感操作无复核"
        f["pending_review"] = _factor("pending_review", "复核状态", review_score,
                                      self.WEIGHTS["pending_review"], review_detail)

        risk = round(sum(x["contribution"] for x in f.values()), 1)
        level = _risk_level(risk, medium_at=30.0, high_at=60.0)
        action = {"low": "直接执行并记审计日志", "medium": "二次确认(短信/U 盾)",
                  "high": "拦截并上报安全审计"}[level]

        result = {
            "success": True, "scorer": "admin_operation", "module": "17后台管理",
            "score": risk, "level": level, "levelName": {"low": "低风险",
            "medium": "中风险", "high": "高风险"}[level],
            "action": action, "factors": list(f.values()),
            "confidence": _confidence(ctx, self.REQUIRED),
            "modelVersion": MODEL_VERSION, "scoredAt": ts(),
        }
        logger.info("后台操作评分: op=%s score=%s level=%s confidence=%s",
                    op_type, risk, level, result["confidence"])
        return result


# ============================================================
# 7. 合同条款风险评分(模块 18 条款规则合同)
# ============================================================

class AgreementRiskScorer:
    """合同条款风险评分: 免责密度/违约金异常/单方条款 → 条款风险识别+修订建议

    5 因子加权 → 风险分(0-100, 越高越危险) → 3级风险 + 修订建议
    """

    WEIGHTS: ClassVar[dict] = {
        "exemption_density": 0.25,  # 免责条款密度
        "penalty_anomaly": 0.20,    # 违约金异常
        "unilateral_clauses": 0.20, # 单方权利条款
        "jurisdiction_risk": 0.15,  # 管辖条款
        "missing_clauses": 0.20,    # 关键条款缺失
    }
    KEY_CLAUSES = ("交付条款", "付款条款", "违约责任", "争议解决", "保密条款")
    REQUIRED: ClassVar[list] = ["exemptionClauseCount", "penaltyRatio"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                exemptionClauseCount: int 免责条款数量,
                penaltyRatio: float 违约金/合同金额比例(0-1),
                unilateralClauseCount: int 单方权利条款数量,
                jurisdictionType: str 管辖类型(court_standard/arbitration/unilateral_far),
                missingKeyClauses: list[str] 缺失关键条款(缺省自动对照 KEY_CLAUSES 中
                    presentKeyClauses 之外的部分)
            }

        Raises:
            ValueError: 违约金比例为负
        """
        penalty = float(ctx.get("penaltyRatio") or 0)
        if penalty < 0:
            raise ValueError("违约金比例不能为负")

        f = {}
        # 免责密度: ≥3 条满分
        exemptions = int(ctx.get("exemptionClauseCount") or 0)
        f["exemption_density"] = _factor(
            "exemption_density", "免责密度", _clamp(exemptions * 33),
            self.WEIGHTS["exemption_density"], f"免责条款 {exemptions} 条")
        # 违约金异常: >30% 满分, 线性
        f["penalty_anomaly"] = _factor("penalty_anomaly", "违约金",
                                       _clamp(penalty * 333),
                                       self.WEIGHTS["penalty_anomaly"],
                                       f"违约金占比 {penalty:.0%}")
        # 单方权利条款: ≥2 条满分
        unilateral = int(ctx.get("unilateralClauseCount") or 0)
        f["unilateral_clauses"] = _factor("unilateral_clauses", "单方条款",
                                          _clamp(unilateral * 50),
                                          self.WEIGHTS["unilateral_clauses"],
                                          f"单方权利条款 {unilateral} 条")
        # 管辖条款: 单方远地管辖满分, 仲裁 30, 标准法院 0
        jurisdiction = str(ctx.get("jurisdictionType") or "court_standard")
        jur_map = {"unilateral_far": 100.0, "arbitration": 30.0,
                   "court_standard": 0.0}
        jur_score = jur_map.get(jurisdiction, 50.0)
        jur_name = {"unilateral_far": "单方远地管辖", "arbitration": "仲裁管辖",
                    "court_standard": "标准法院管辖"}.get(jurisdiction, "未明确管辖")
        f["jurisdiction_risk"] = _factor("jurisdiction_risk", "管辖条款",
                                         jur_score, self.WEIGHTS["jurisdiction_risk"],
                                         jur_name)
        # 关键条款缺失: 显式清单优先, 否则对照 presentKeyClauses 自动求差集
        missing = ctx.get("missingKeyClauses")
        if missing is None:
            present = set(ctx.get("presentKeyClauses") or [])
            missing = [c for c in self.KEY_CLAUSES if c not in present]
        missing = list(missing or [])
        f["missing_clauses"] = _factor("missing_clauses", "条款缺失",
                                       _clamp(len(missing) * 20),
                                       self.WEIGHTS["missing_clauses"],
                                       f"缺失 {len(missing)} 项")

        risk = round(sum(x["contribution"] for x in f.values()), 1)
        level = _risk_level(risk, medium_at=30.0, high_at=60.0)

        # 修订建议(按触发因子生成)
        suggestions = []
        if exemptions >= 3:
            suggestions.append("压缩免责条款数量, 免责范围需与责任对等")
        if penalty > 0.3:
            suggestions.append("违约金比例过高, 建议不超过合同金额 30%")
        if unilateral >= 2:
            suggestions.append("删除或改写单方权利条款为双向对等条款")
        if jurisdiction == "unilateral_far":
            suggestions.append("管辖地改为被告住所地或合同履行地法院")
        if missing:
            suggestions.append("补充缺失关键条款: " + "、".join(missing))
        if not suggestions:
            suggestions.append("条款结构合规, 无需修订")

        result = {
            "success": True, "scorer": "agreement_risk", "module": "18条款规则合同",
            "score": risk, "level": level, "levelName": {"low": "低风险",
            "medium": "中风险", "high": "高风险"}[level],
            "action": {"low": "标准用印流程", "medium": "法务复核后用印",
                       "high": "法务介入重拟条款"}[level],
            "revisionSuggestions": suggestions, "factors": list(f.values()),
            "confidence": _confidence(ctx, self.REQUIRED),
            "modelVersion": MODEL_VERSION, "scoredAt": ts(),
        }
        logger.info("合同条款评分: score=%s level=%s suggestions=%s confidence=%s",
                    risk, level, len(suggestions), result["confidence"])
        return result


# ============================================================
# 8. 财务异常检测评分(模块 19 财务管理)
# ============================================================

class FinanceAnomalyScorer:
    """财务异常检测评分: 金额偏离/摘要匹配/试算平衡 → 记账异常识别

    5 因子加权 → 异常分(0-100, 越高越异常) → 3级异常 + 处置动作
    对齐 finance_service 凭证审核(audit_voucher)与试算平衡惯例。
    """

    WEIGHTS: ClassVar[dict] = {
        "amount_deviation": 0.25,   # 金额偏离
        "summary_mismatch": 0.20,   # 摘要匹配度
        "off_hours": 0.15,          # 记账时段
        "frequency_spike": 0.20,    # 频率突增
        "balance_deviation": 0.20,  # 借贷不平衡
    }
    REQUIRED: ClassVar[list] = ["amount", "accountAverageAmount"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                amount: float 本笔金额(必填), accountAverageAmount: float 该科目均值(必填),
                summaryMatchScore: float 摘要与业务单据匹配分(0-100, 越高越匹配),
                entryHour: int 记账小时(0-23, 缺省当前), isWeekend: bool 是否周末,
                entriesToday: int 当日凭证数, dailyAverageEntries: int 日均凭证数,
                unbalanceAmount: float 借贷不平衡差额(≥0, 0 为平衡)
            }

        Raises:
            ValueError: 记账金额为负
        """
        amount = float(ctx.get("amount") or 0)
        if amount < 0:
            raise ValueError("记账金额不能为负")

        f = {}
        # 金额偏离: |金额-均值|/均值 ≥3 倍满分, 线性
        avg = float(ctx.get("accountAverageAmount") or 0)
        if avg > 0:
            deviation = abs(amount - avg) / avg
            dev_detail = f"偏离均值 {deviation:.1f} 倍"
        else:
            deviation, dev_detail = 0.5, "科目无历史均值(按中等风险计)"
        f["amount_deviation"] = _factor("amount_deviation", "金额偏离",
                                        _clamp(deviation * 33),
                                        self.WEIGHTS["amount_deviation"], dev_detail)
        # 摘要匹配: 匹配分越高风险越低(逆向)
        match = ctx.get("summaryMatchScore")
        if match is None:
            match_score, match_detail = 50.0, "匹配度未评估"
        else:
            match = _clamp(float(match))
            match_score, match_detail = _clamp(100 - match), f"匹配分 {match:.0f}"
        f["summary_mismatch"] = _factor("summary_mismatch", "摘要匹配",
                                        match_score, self.WEIGHTS["summary_mismatch"],
                                        match_detail)
        # 记账时段: 凌晨满分, 周末 50
        hour = int(ctx.get("entryHour") if ctx.get("entryHour") is not None
                   else _now_hour())
        if 0 <= hour < 6:
            hour_score, hour_detail = 100.0, f"凌晨 {hour} 点记账"
        elif ctx.get("isWeekend"):
            hour_score, hour_detail = 50.0, "周末记账"
        else:
            hour_score, hour_detail = 0.0, "工作时间"
        f["off_hours"] = _factor("off_hours", "记账时段", hour_score,
                                 self.WEIGHTS["off_hours"], hour_detail)
        # 频率突增: 当日凭证数/日均 ≥3 倍满分
        today = float(ctx.get("entriesToday") or 0)
        daily_avg = float(ctx.get("dailyAverageEntries") or 0)
        if daily_avg > 0:
            spike = today / daily_avg
            spike_detail = f"当日 {today:.0f} 笔/日均 {daily_avg:.0f} 笔"
        else:
            spike, spike_detail = 0.0, "无日均基线"
        f["frequency_spike"] = _factor("frequency_spike", "频率突增",
                                       _clamp(spike * 33),
                                       self.WEIGHTS["frequency_spike"], spike_detail)
        # 借贷平衡: 差额 >0 满分(直接不合格)
        unbalance = float(ctx.get("unbalanceAmount") or 0)
        f["balance_deviation"] = _factor("balance_deviation", "借贷平衡",
                                         100.0 if unbalance > 0 else 0.0,
                                         self.WEIGHTS["balance_deviation"],
                                         f"不平衡差额 ¥{unbalance:,.2f}")

        anomaly = round(sum(x["contribution"] for x in f.values()), 1)
        if anomaly < 25:
            level, action = "low", "自动过账"
        elif anomaly < 50:
            level, action = "medium", "标记复核(主管抽查)"
        else:
            level, action = "high", "冻结凭证 + 通知财务主管"

        result = {
            "success": True, "scorer": "finance_anomaly", "module": "19财务管理",
            "score": anomaly, "level": level, "levelName": {"low": "正常",
            "medium": "关注", "high": "告警"}[level],
            "action": action, "factors": list(f.values()),
            "confidence": _confidence(ctx, self.REQUIRED),
            "modelVersion": MODEL_VERSION, "scoredAt": ts(),
        }
        logger.info("财务异常评分: score=%s level=%s confidence=%s",
                    anomaly, level, result["confidence"])
        return result
