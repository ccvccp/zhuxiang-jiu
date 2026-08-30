"""AI 语义评分层·第三批(v7.3 → v7.4: 用户认证模块 AI 语义升级·混合模式)

为最后一个 B 级模块(30用户认证)补齐 AI 评分语义。

混合模式设计(行业 RBA 风险自适应认证惯例):
    - 确定性安全核心保持规则引擎, 不受评分影响:
        JWT 签名校验 / PBKDF2 密码哈希 / 令牌吊销(JTI 黑名单) / 账号状态校验
    - AI 评分仅作用于「登录决策」: 何时直接放行 / 何时要求二次验证 / 何时拦截
      (auth_service.login 可在密码校验通过后调用本评分器决定后续动作)

评分器清单(第三批):
    AuthRiskScorer  认证风控评分(30): 8因子 → 风险分 → allow/step_up/challenge/block

硬约束(规则引擎兜底, 契合项目「AI智能优先 + 规则引擎兜底」原则):
    - IP 在黑名单 → 无论总分直接拦截
    - 密码已泄露(撞库库命中) → 无论总分直接拦截

设计约定(与第一/二批一致):
    - 纯函数式评分(输入 dict → 输出 dict), 不落库, 不改现有模块(零侵入)
    - 全 async(项目约定); 异常约定: ValueError → 409(输入非法)
    - 置信度 = 输入字段完整度(缺失字段越多置信度越低, 下限 0.3)
    - 复用第一批的评分原语(_clamp/_factor/_risk_level/_confidence)
"""

import logging
from typing import ClassVar

from core.helpers import ts
from services.ai_learning_service import (
    get_active_weight_version, load_effective_weights,
)
from services.ai_scoring_service import (
    _clamp, _confidence, _factor, _now_hour,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-auth"

# 4 级认证决策
ACTION_ALLOW, ACTION_STEP_UP, ACTION_CHALLENGE, ACTION_BLOCK = (
    "allow", "step_up", "challenge", "block")
ACTION_NAMES = {
    ACTION_ALLOW: "直接放行",
    ACTION_STEP_UP: "二次验证(短信/邮箱验证码)",
    ACTION_CHALLENGE: "强核验(安全问题/人脸)",
    ACTION_BLOCK: "拦截登录",
}


class AuthRiskScorer:
    """认证风控评分: 登录时点风险画像(撞库/盗号/异地/新设备识别)

    8 因子加权 → 风险分(0-100, 越高越危险) → 4级决策动作
    """

    WEIGHTS: ClassVar[dict] = {
        "failed_attempts": 0.20,     # 近期失败次数(撞库/暴破信号)
        "geo_velocity": 0.15,        # 地理速度(不可能行程)
        "device_match": 0.20,        # 新设备
        "ip_reputation": 0.15,       # IP 信誉
        "time_pattern": 0.05,        # 异常时段
        "account_age": 0.05,         # 账户年龄
        "password_strength": 0.10,   # 密码强度
        "behavior_deviation": 0.10,  # 行为偏离度
    }
    IP_RISK_SCORES: ClassVar[dict] = {"clean": 0.0, "proxy": 40.0, "vpn": 60.0,
                      "tor": 90.0, "blacklist": 100.0}
    PASSWORD_SCORES: ClassVar[dict] = {"strong": 0.0, "medium": 20.0, "weak": 60.0,
                       "breached": 100.0}
    REQUIRED: ClassVar[list] = ["failedAttempts", "newDevice", "ipRiskType"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                failedAttempts: int 近1小时登录失败次数,
                distanceKm: float 与上次登录地距离(公里),
                hoursSinceLastLogin: float 距上次登录小时数,
                newDevice: bool 是否新设备(缺省视为未知, 中性),
                ipRiskType: str IP 信誉(clean/proxy/vpn/tor/blacklist),
                loginHour: int 登录小时(0-23, 缺省当前),
                accountAgeDays: float 账户年龄(天, 缺省按 365 中性),
                passwordStatus: str 密码状态(strong/medium/weak/breached),
                behaviorDeviationScore: float 行为偏离度(0-100)
            }

        Raises:
            ValueError: 输入非法(负数失败次数/负距离/未知 IP 类型/未知密码状态)
        """
        failed = float(ctx.get("failedAttempts") or 0)
        if failed < 0:
            raise ValueError("登录失败次数不能为负")
        distance = ctx.get("distanceKm")
        if distance is not None:
            distance = float(distance)
            if distance < 0:
                raise ValueError("登录距离不能为负")
        hours = ctx.get("hoursSinceLastLogin")

        ip_risk = ctx.get("ipRiskType")
        if ip_risk is not None and ip_risk not in self.IP_RISK_SCORES:
            raise ValueError(f"未知 IP 信誉类型: {ip_risk}")
        password_status = ctx.get("passwordStatus")
        if password_status is not None and password_status not in self.PASSWORD_SCORES:
            raise ValueError(f"未知密码状态: {password_status}")

        hour = int(ctx.get("loginHour") if ctx.get("loginHour") is not None
                   else _now_hour())

        # 自学习层生效权重(champion), 无档案/异常时回退类默认值
        weights = await load_effective_weights("auth_risk", self.WEIGHTS)

        f = {}
        # 失败次数: 每次 10 分(≥10 次满分), 撞库/暴力破解信号
        f["failed_attempts"] = _factor("failed_attempts", "失败次数",
                                       _clamp(failed * 10),
                                       weights["failed_attempts"],
                                       f"近1小时失败 {failed:.0f} 次")
        # 地理速度: 速度 = 距离/时长, ≥900km/h(民航) 视为不可能行程满分
        if distance is None or hours is None:
            geo_score, geo_detail = 0.0, "无历史位置可比对"
        elif distance == 0:
            geo_score, geo_detail = 0.0, "同地登录"
        else:
            h = max(float(hours), 0.01)
            speed = distance / h
            geo_score = _clamp(speed / 9)  # 900km/h → 100
            geo_detail = f"位移 {distance:.0f}km/{h:.1f}h(≈{speed:.0f}km/h)"
        f["geo_velocity"] = _factor("geo_velocity", "地理速度", geo_score,
                                    weights["geo_velocity"], geo_detail)
        # 新设备: 已知设备零风险, 新设备满分, 未知中性
        new_device = ctx.get("newDevice")
        if new_device is None:
            dev_score, dev_detail = 50.0, "设备状态未知"
        elif new_device:
            dev_score, dev_detail = 100.0, "新设备首次登录"
        else:
            dev_score, dev_detail = 0.0, "常用设备"
        f["device_match"] = _factor("device_match", "设备匹配", dev_score,
                                    weights["device_match"], dev_detail)
        # IP 信誉: 查映射表, 未知 30 中性
        if ip_risk is None:
            ip_score, ip_detail = 30.0, "IP 未检测"
        else:
            ip_score = self.IP_RISK_SCORES[ip_risk]
            ip_detail = f"IP 类型 {ip_risk}"
        f["ip_reputation"] = _factor("ip_reputation", "IP信誉", ip_score,
                                     weights["ip_reputation"], ip_detail)
        # 登录时段: 0-6 点满分
        night_score = 100.0 if 0 <= hour < 6 else 0.0
        f["time_pattern"] = _factor("time_pattern", "登录时段", night_score,
                                    weights["time_pattern"], f"{hour} 点登录")
        # 账户年龄: <7 天满分, ≥90 天零分(缺省按 365 中性)
        days = float(ctx["accountAgeDays"]) if ctx.get("accountAgeDays") is not None else 365.0
        f["account_age"] = _factor(
            "account_age", "账户年龄", _clamp(max(0.0, (90 - days) / 90 * 100)),
            weights["account_age"], f"账龄 {days:.0f} 天")
        # 密码强度: 查映射表(breached=撞库库命中), 未知 30 中性
        if password_status is None:
            pwd_score, pwd_detail = 30.0, "密码强度未评估"
        else:
            pwd_score = self.PASSWORD_SCORES[password_status]
            pwd_detail = f"密码状态 {password_status}"
        f["password_strength"] = _factor("password_strength", "密码强度", pwd_score,
                                         weights["password_strength"], pwd_detail)
        # 行为偏离度: 与历史登录习惯(设备+时段+地点)的偏离, 直接输入
        deviation = ctx.get("behaviorDeviationScore")
        if deviation is None:
            beh_score, beh_detail = 30.0, "无行为基线"
        else:
            beh_score = _clamp(float(deviation))
            beh_detail = f"偏离度 {beh_score:.0f}"
        f["behavior_deviation"] = _factor("behavior_deviation", "行为偏离",
                                          beh_score, weights["behavior_deviation"],
                                          beh_detail)

        risk = round(sum(x["contribution"] for x in f.values()), 1)

        # 硬约束(规则兜底): 黑名单 IP / 已泄露密码 → 无论总分直接拦截
        hard_reasons = []
        if ip_risk == "blacklist":
            hard_reasons.append("IP 在黑名单")
        if password_status == "breached":
            hard_reasons.append("密码已在泄露库命中")
        if hard_reasons:
            action = ACTION_BLOCK
            hard_blocked = True
        else:
            hard_blocked = False
            if risk < 25:
                action = ACTION_ALLOW
            elif risk < 50:
                action = ACTION_STEP_UP
            elif risk < 70:
                action = ACTION_CHALLENGE
            else:
                action = ACTION_BLOCK

        action_detail = {
            ACTION_ALLOW: "密码校验通过后直接颁发双令牌",
            ACTION_STEP_UP: "密码校验通过后追加短信/邮箱验证码核验",
            ACTION_CHALLENGE: "改用安全问题/人脸核验, 通过后才颁发令牌",
            ACTION_BLOCK: "拒绝登录, 临时冻结账号并通知用户",
        }[action]

        result = {
            "success": True, "scorer": "auth_risk", "module": "30用户认证",
            "score": risk, "action": action, "actionName": ACTION_NAMES[action],
            "actionDetail": action_detail,
            "weightVersion": get_active_weight_version("auth_risk"),
            "hardBlocked": hard_blocked,
            "hardBlockReasons": hard_reasons,
            "factors": list(f.values()),
            "confidence": _confidence(ctx, self.REQUIRED),
            "modelVersion": MODEL_VERSION, "scoredAt": ts(),
        }
        logger.info("认证风控评分: score=%s action=%s hard=%s confidence=%s",
                    risk, action, hard_blocked, result["confidence"])
        return result
