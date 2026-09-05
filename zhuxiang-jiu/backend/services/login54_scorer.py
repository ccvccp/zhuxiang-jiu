"""54号·小竹AI智能登录引擎大模型 评分器(login54_scorer)

计划(docs/54号_小竹AI智能登录引擎大模型实施计划.md §一-1.3):
    八因子权重模型(登录场景原生——数据源全部在
    53号 events 六字段审计已留痕):

    channel_success     通道历史成功率(byChannel 聚合)
    credential_quality  凭证类型强度(bio>voice>qr)
    device_match        基线指纹匹配度档位
    budget_sufficiency  隐私预算余量
    member_maturity     账龄+登录频次
    fail_history         同通道失败计数
    voice_confidence    声纹初筛+活体分
    portal_state        角色四态(new/dormant 加权)

输出: 0-100 信任分(高分=低风险——对齐 43号
ThreatGateScorer 高分可信口径, 区别于 auth_risk
的低分低风险) → 四级响应(silent/one_tap/
step_up/enhanced——53号 RISK_TIERS 对齐)。

混合架构铁律(计划 §一-1.3):
    - 与 43号 auth_risk **并行评分取 max 合成**
      (互补不替换): 54号输出信任分, 合成时
      转(100-信任分)风险分与 auth_risk 取 max
    - 纯函数式(输入 dict → 输出 dict), 零落库,
      零既有模块侵入
    - 权重经 load_effective_weights 读取 champion
      (44号自学习闭环——异常回退默认, 不阻塞)

设计约定(与三批评评分器一致):
    - 全 async; ValueError → 409(输入非法)
    - 置信度 = 输入字段完整度(下限 0.3)
    - 复用 ai_scoring_service 评分原语
"""

import logging
from typing import ClassVar

from core.helpers import ts
from services.ai_learning_service import (
    get_active_weight_version, load_effective_weights,
)
from services.ai_scoring_service import (
    _clamp, _confidence, _factor,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-login54"

# 四级响应(53号 RISK_TIERS 对齐——高分信任低打扰)
TIER_SILENT, TIER_ONE_TAP, TIER_STEP_UP, TIER_ENHANCED = (
    "silent", "one_tap", "step_up", "enhanced")
TIER_NAMES = {
    TIER_SILENT: "静默通过(零打扰)",
    TIER_ONE_TAP: "一键确认(轻量)",
    TIER_STEP_UP: "追加轻量验证",
    TIER_ENHANCED: "强制多因子+人工客服",
}

# 凭证类型强度映射(bio 挑战制最强>声纹双因子>
# 扫码票据>默认; 53号 AUTH_CHANNELS 对齐)
CREDENTIAL_STRENGTH = {
    "passkey": 0.9, "fingerprint": 0.85,
    "voice": 0.75, "qr": 0.7,
}

# 角色四态信任基线(成熟会员高信任;
# new/dormant 谨慎——53号 PORTAL_STATES 对齐)
PORTAL_TRUST_BASE = {
    "active": 0.9, "new": 0.5,
    "dormant": 0.4, "high_risk": 0.1,
}


class Login54Scorer:
    """登录引擎编排评分(54号·八因子信任分→四级响应)

    输入 ctx 字段(全部可选——缺失走中性口径, 置信
    度相应下降; 数据源=53号 events 审计上下文):
        channelSuccess: float 通道历史成功率 [0,1]
        channel: str 本次通道(passkey/face/voice/qr/
                 fingerprint——credential_quality 因子)
        baselineMatch: float 基线指纹匹配度 [0,1]
        budgetRemaining: float 隐私预算余量 [0,1]
        accountAgeDays: float 账龄(天)
        loginFrequency: float 近 30 日登录频次(次)
        channelFailCount: int 同通道失败计数
        voiceConfidence: float 声纹置信度 [0,1]
        portalState: str 角色四态
    """

    WEIGHTS: ClassVar[dict] = {
        "channel_success": 0.15,
        "credential_quality": 0.15,
        "device_match": 0.15,
        "budget_sufficiency": 0.10,
        "member_maturity": 0.10,
        "fail_history": 0.15,
        "voice_confidence": 0.10,
        "portal_state": 0.10,
    }

    REQUIRED: ClassVar[list] = [
        "channelSuccess", "channel", "portalState"]

    async def score(self, ctx: dict) -> dict:
        """评分入口: 八因子加权 → 信任分(0-100,
        高分=低风险) → 四级响应

        Raises:
            ValueError: 输入非法(成功率/匹配度越界)
        """
        if not ctx:
            raise ValueError("评分上下文不能为空")

        # 自学习层生效权重(champion; 异常回退默认)
        weights = await load_effective_weights(
            "login_orchestration", self.WEIGHTS)

        # ① 通道历史成功率(直接输入 [0,1] → 0-100)
        ch_success = ctx.get("channelSuccess")
        if ch_success is not None:
            ch_success = float(ch_success)
            if not 0.0 <= ch_success <= 1.0:
                raise ValueError("通道成功率须在 [0,1]")
            s1 = ch_success * 100
            d1 = f"通道历史成功率 {ch_success:.0%}"
        else:
            s1, d1 = 50.0, "无通道历史(新通道)"
        f1 = _factor("channel_success", "通道成功率",
                     s1, weights["channel_success"], d1)

        # ② 凭证类型强度(通道映射——未知识别中性)
        channel = str(ctx.get("channel") or "")
        strength = CREDENTIAL_STRENGTH.get(channel)
        if strength is None:
            s2, d2 = 50.0, f"未知通道({channel or '无'})"
        else:
            s2 = strength * 100
            d2 = f"{channel} 凭证强度"
        f2 = _factor("credential_quality", "凭证强度",
                     s2, weights["credential_quality"], d2)

        # ③ 设备匹配(基线匹配度 [0,1]——缺省中性)
        match = ctx.get("baselineMatch")
        if match is None:
            s3, d3 = 50.0, "无基线指纹"
        else:
            match = float(match)
            if not 0.0 <= match <= 1.0:
                raise ValueError("基线匹配度须在 [0,1]")
            s3 = match * 100
            d3 = f"基线匹配 {match:.0%}"
        f3 = _factor("device_match", "设备匹配",
                     s3, weights["device_match"], d3)

        # ④ 预算余量(0=耗尽谨慎, 1=充足;
        #    仅影响体验档位不阻断——49号红线继承)
        budget = ctx.get("budgetRemaining")
        if budget is None:
            s4, d4 = 70.0, "预算未探(中性)"
        else:
            budget = max(0.0, min(1.0, float(budget)))
            s4 = 40.0 + budget * 60.0
            d4 = f"预算余量 {budget:.0%}"
        f4 = _factor("budget_sufficiency", "预算余量",
                     s4, weights["budget_sufficiency"], d4)

        # ⑤ 会员成熟度(账龄 90 天满分+频次加成)
        age = ctx.get("accountAgeDays")
        freq = ctx.get("loginFrequency")
        age_score = _clamp(
            (float(age or 0) / 90 * 100)
            if age is not None else 50.0)
        if freq is not None:
            # 频次加成: 20 次/月满分, 与账龄 7:3 合成
            freq_score = _clamp(
                float(freq) / 20 * 100)
            s5 = age_score * 0.7 + freq_score * 0.3
            d5 = (f"账龄 {float(age or 0):.0f} 天"
                  f" × 月登录 {float(freq):.0f} 次")
        else:
            s5 = age_score
            d5 = f"账龄 {float(age or 0):.0f} 天"
        f5 = _factor("member_maturity", "会员成熟度",
                     s5, weights["member_maturity"], d5)

        # ⑥ 失败历史(每 +1 扣 25 分, 4 次归零)
        fails = int(ctx.get("channelFailCount") or 0)
        if fails < 0:
            raise ValueError("失败计数不能为负")
        s6 = _clamp(100 - fails * 25)
        d6 = f"同通道失败 {fails} 次"
        f6 = _factor("fail_history", "失败历史",
                     s6, weights["fail_history"], d6)

        # ⑦ 声纹置信(仅语音通道相关; 其他通道中性)
        voice = ctx.get("voiceConfidence")
        if voice is None:
            s7, d7 = 50.0, "非语音通道(中性)"
        else:
            voice = max(0.0, min(1.0, float(voice)))
            s7 = voice * 100
            d7 = f"声纹置信 {voice:.2f}"
        f7 = _factor("voice_confidence", "声纹置信",
                     s7, weights["voice_confidence"], d7)

        # ⑧ 角色四态(信任基线——high_risk 归 10)
        state = str(ctx.get("portalState") or "")
        base = PORTAL_TRUST_BASE.get(state)
        if base is None:
            s8, d8 = 50.0, "未建档(中性)"
        else:
            s8 = base * 100
            d8 = f"角色态 {state}"
        f8 = _factor("portal_state", "角色状态",
                     s8, weights["portal_state"], d8)

        factors = [f1, f2, f3, f4, f5, f6, f7, f8]
        trust = round(sum(x["contribution"]
                          for x in factors), 1)

        # 四级响应(高分信任低打扰——阈值对齐
        # DECISION_THRESHOLDS login_orchestration)
        if trust >= 75.0:
            tier = TIER_SILENT
        elif trust >= 50.0:
            tier = TIER_ONE_TAP
        elif trust >= 25.0:
            tier = TIER_STEP_UP
        else:
            tier = TIER_ENHANCED

        confidence = _confidence(ctx, self.REQUIRED)
        return {
            "success": True,
            "scorer": "login_orchestration",
            "modelVersion": MODEL_VERSION,
            "weightVersion": get_active_weight_version(
                "login_orchestration"),
            "trustScore": trust,
            "riskScoreEquivalent": round(
                100 - trust, 1),   # 风险分等价(max 合成用)
            "tier": tier,
            "tierName": TIER_NAMES[tier],
            "factors": factors,
            "confidence": confidence,
            "scoredAt": ts(),
        }
