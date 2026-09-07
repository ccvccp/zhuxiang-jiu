"""66号·AI智能工程师大模块 评分器
(engineer_service, 第56档案 batch40, 55→56 全站同步)

《66号_AI智能工程师大模型实施计划》§七:
    智能工程师服务评分——支持对话/自愈编排的
    服务路由决策门:

    | 档案 | batch | 模块 | 挂门 | 回流终态 |
    |------|-------|------|------|---------|
    | engineer_service | 40 | 66号AI智能工程师 | support/chat + heal | 服务终态/自愈终态/补偿执行 |

八因子(确定性加权, 对齐批次一~六范式):
    value_sensitivity 0.20   涉信值/账本 → 100, 涉订单 → 60, 纯咨询 → 20
    issue_complexity  0.15   跨模块数(1→30 / 2→60 / ≥3→100)
    sla_pressure      0.15   SLA 剩余<25% 或 urgent → 100, 依级递减
    emotion_intensity 0.10   情绪轨分档(calm 10 … angry 100)
    recurrence_rate   0.10   同实体同问题 30 日复发次数 ×25 封顶 100
    role_tier         0.10   restricted 100 / watched 70 / standard 40 / trusted 20
    self_service      0.10   案例库命中度(0→100 可自助)
    root_cause_risk   0.10   100 − 诊断置信度

铁律: 确定性加权/LLM 不进判定链/
observe 默认只评分快照/处置路由三档。
"""

import logging
from typing import ClassVar

from core.helpers import ts

logger = logging.getLogger("xx66_scorer")

MODEL_VERSION = "v1-xx66-engineer"

# 情绪轨分档 → 分值(与 P1 情绪轨共用口径)
EMOTION_BAND_SCORES = {
    "calm": 10.0, "confused": 40.0,
    "frustrated": 70.0, "angry": 100.0,
}

# tier → 风险分(高 tier 低风险——信任回报)
TIER_RISK_SCORES = {
    "trusted": 20.0, "standard": 40.0,
    "watched": 70.0, "restricted": 100.0,
}


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


class EngineerServiceScorer:
    """智能工程师服务评分(P0 入册, 八因子确定性)"""

    WEIGHTS: ClassVar[dict] = {
        "value_sensitivity": 0.20,
        "issue_complexity": 0.15,
        "sla_pressure": 0.15,
        "emotion_intensity": 0.10,
        "recurrence_rate": 0.10,
        "role_tier": 0.10,
        "self_service": 0.10,
        "root_cause_risk": 0.10,
    }

    LEVEL_NAMES: ClassVar[dict] = {
        "high": "高敏感服务",
        "medium": "标准服务",
        "low": "轻量服务"}
    LEVEL_ACTIONS: ClassVar[dict] = {
        "high": "升级人工+安抚模式+优先通道",
        "medium": "AI诊断执行+教学模式",
        "low": "自助向导+彩蛋等待"}

    async def score(self, ctx: dict) -> dict:
        """评分(ctx 缺关键键 → ValueError——对齐全站空上下文拒绝惯例)

        ctx:
            subjectKind: 咨询对象类别
                (trust|order|exchange|payment|profile|general)
            modulesInvolved: 涉及模块数(int ≥1)
            slaLevel: 工单/会话优先级
                (urgent|high|medium|low)
            slaRemainingRatio: SLA 剩余比(0-1, 可缺省)
            emotionBand: 情绪轨分档
                (calm|confused|frustrated|angry, 可缺省)
            recurrenceCount: 同实体同问题 30 日复发次数
                (int, 可缺省)
            roleTier: 47号画像 tier
                (trusted|standard|watched|restricted, 可缺省)
            caseHitScore: 案例库命中度(0-1, 可缺省)
            diagnoseConfidence: 诊断置信度(0-100, 可缺省)
        """
        if not isinstance(ctx, dict) or not ctx:
            raise ValueError("评分上下文不可为空")

        kind = str(ctx.get("subjectKind") or "general")
        if kind not in ("trust", "order", "exchange",
                        "payment", "profile", "general"):
            raise ValueError(f"未知咨询对象类别: {kind}")

        # ① 信值敏感度
        if kind in ("trust", "exchange", "payment"):
            sens, sens_detail = 100.0, "涉余额/账本/兑换"
        elif kind == "order":
            sens, sens_detail = 60.0, "涉订单状态"
        else:
            sens, sens_detail = 20.0, "纯咨询/画像"

        # ② 跨模块复杂度
        try:
            modules = int(ctx.get("modulesInvolved") or 1)
        except (TypeError, ValueError):
            modules = 1
        modules = max(1, modules)
        cpx = (30.0 if modules == 1
               else 60.0 if modules == 2 else 100.0)

        # ③ SLA 压力
        sla_level = str(ctx.get("slaLevel") or "medium")
        if sla_level not in ("urgent", "high", "medium", "low"):
            sla_level = "medium"
        base_sla = {"urgent": 100.0, "high": 70.0,
                    "medium": 40.0, "low": 20.0}[sla_level]
        try:
            remaining = float(ctx.get("slaRemainingRatio"))
        except (TypeError, ValueError):
            remaining = None
        if remaining is not None and remaining < 0.25:
            base_sla = 100.0
        sla = base_sla

        # ④ 情绪烈度
        band = str(ctx.get("emotionBand") or "calm")
        emo = EMOTION_BAND_SCORES.get(band, 10.0)

        # ⑤ 复发率
        try:
            recur = int(ctx.get("recurrenceCount") or 0)
        except (TypeError, ValueError):
            recur = 0
        recurrence = _clamp(max(0, recur) * 25.0)

        # ⑥ 角色分层(47号 tier——缺省 standard)
        tier = str(ctx.get("roleTier") or "standard")
        tier_risk = TIER_RISK_SCORES.get(tier, 40.0)

        # ⑦ 自助可行性(案例命中 → 可自助)
        try:
            hit = float(ctx.get("caseHitScore") or 0.0)
        except (TypeError, ValueError):
            hit = 0.0
        self_service = _clamp(hit * 100.0)

        # ⑧ 根因风险(100 − 置信度)
        try:
            conf = float(ctx.get("diagnoseConfidence"))
        except (TypeError, ValueError):
            conf = 60.0
        root_risk = _clamp(100.0 - _clamp(conf))

        factors = [
            _factor("value_sensitivity", "信值敏感度",
                    sens, self.WEIGHTS["value_sensitivity"],
                    sens_detail),
            _factor("issue_complexity", "跨模块复杂度",
                    cpx, self.WEIGHTS["issue_complexity"],
                    f"涉及 {modules} 个模块"),
            _factor("sla_pressure", "SLA 压力",
                    sla, self.WEIGHTS["sla_pressure"],
                    f"{sla_level} 级"
                    + (f", 剩余 {remaining:.0%}"
                       if remaining is not None else "")),
            _factor("emotion_intensity", "情绪烈度",
                    emo, self.WEIGHTS["emotion_intensity"],
                    f"情绪轨 {band} 档"),
            _factor("recurrence_rate", "复发率",
                    recurrence, self.WEIGHTS["recurrence_rate"],
                    f"30 日内复发 {max(0, recur)} 次"),
            _factor("role_tier", "角色分层",
                    tier_risk, self.WEIGHTS["role_tier"],
                    f"47号画像 {tier}"),
            _factor("self_service", "自助可行性",
                    self_service, self.WEIGHTS["self_service"],
                    f"案例命中度 {hit:.2f}"),
            _factor("root_cause_risk", "根因风险",
                    root_risk, self.WEIGHTS["root_cause_risk"],
                    f"诊断置信度 {conf:.0f}"),
        ]

        risk = round(sum(f["contribution"]
                         for f in factors), 1)
        level = _three_level(risk)
        return {
            "success": True,
            "scorer": "engineer_service",
            "module": "66号AI智能工程师",
            "score": risk,
            "level": level,
            "levelName": self.LEVEL_NAMES[level],
            "action": self.LEVEL_ACTIONS[level],
            "factors": factors,
            "confidence": 1.0,
            "modelVersion": MODEL_VERSION,
            "scoredAt": ts(),
        }
