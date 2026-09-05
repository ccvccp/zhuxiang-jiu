"""54号·小竹AI智能登录引擎大模型 服务层(login54_service)

P0 范围(计划 §六 P0):
    - 双模型并行评分: Login54Scorer(八因子信任分)
      与 43号 auth_risk **取 max 合成**(互补不替换
      ——混合架构铁律): 54号信任分→风险分等价
      (100-trust)与 auth_risk 风险分取 max →
      53号四级响应档位
    - 模型状态视图(champion/challenger/漂移——
      44号 get_weights_view 复用)
    - 影子评分预览(不生效——challenger 试算对比)

off 语义:
    LOGIN54_MODE=off(默认) → 模型面关闭(53号编排
    用 auth_risk 原轨——零影响); registry/status
    观测面不受影响。
"""

import logging
import os

from core.helpers import ts

from repositories.login54_repository import (
    Login54Repository,
)
from services.login54_scorer import (
    Login54Scorer, TIER_ENHANCED, TIER_NAMES,
    TIER_ONE_TAP, TIER_SILENT, TIER_STEP_UP,
)

logger = logging.getLogger("login54_service")

MODE_KEY = "LOGIN54_MODE"

SCORER_ID = "login_orchestration"


def current_mode() -> str:
    """模块开关(动态读取——运行时可切换)"""
    return os.environ.get(MODE_KEY, "off")


def _risk_tier(risk_score: float) -> str:
    """风险分→53号四级响应档位(RISK_TIERS 对齐)"""
    if risk_score < 25:
        return TIER_SILENT
    if risk_score < 50:
        return TIER_ONE_TAP
    if risk_score < 70:
        return TIER_STEP_UP
    return TIER_ENHANCED


class Login54Service:
    """54号登录大模型服务(P0: 双模型接入+状态视图)"""

    def __init__(self):
        self.repo = Login54Repository()

    # --------------------------------------------------------
    # 双模型并行评分(max 合成——混合架构铁律)
    # --------------------------------------------------------

    async def dual_score(self, ctx: dict,
                         auth_risk_result: dict | None = None,
                         ) -> dict:
        """双模型合成评分(54号+43号并行取 max)

        - 54号 Login54Scorer: 八因子信任分 →
          riskScoreEquivalent(100-trust)
        - 43号 auth_risk: 传入结果(或 None 跳过)
        - 合成: max(54号等价风险分, 43号风险分) →
          53号四级响应档位

        双模型互补语义: 任一模型判险即取高——保守
        合成不替换不侵入(auth_risk 保持独立)。
        """
        mode = current_mode()
        if mode != "on":
            raise ValueError(
                f"LOGIN54_MODE={mode}(默认 off——"
                f"模型面关闭, 53号编排走 auth_risk 原轨)")

        result = await Login54Scorer().score(ctx)
        trust = float(result.get("trustScore") or 0.0)
        equiv_risk = float(
            result.get("riskScoreEquivalent") or 0.0)

        # 43号 auth_risk 合成(传入结果缺失→仅 54号)
        auth_risk_score = None
        if auth_risk_result:
            try:
                auth_risk_score = float(
                    (auth_risk_result or {})
                    .get("score") or 0.0)
            except (TypeError, ValueError):
                auth_risk_score = None

        if auth_risk_score is not None:
            combined = round(
                max(equiv_risk, auth_risk_score), 1)
            source = ("dual_max" if equiv_risk
                      >= auth_risk_score
                      else "auth_risk_dominant")
        else:
            combined = round(equiv_risk, 1)
            source = "login54_only"

        tier = _risk_tier(combined)
        return {
            "success": True,
            "combinedRiskScore": combined,
            "tier": tier,
            "tierName": TIER_NAMES[tier],
            "source": source,
            "login54": result,
            "authRisk": auth_risk_result,
            "note": "双模型并行取 max(保守合成——"
                    "任一模型判险即取高; auth_risk "
                    "保持独立零侵入)",
            "scoredAt": ts(),
        }

    # --------------------------------------------------------
    # 模型状态视图(44号复用——观测面)
    # --------------------------------------------------------

    async def model_status(self) -> dict:
        """模型状态(champion/challenger/漂移/因子)"""
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(SCORER_ID)
        view.update({
            "module": "login54",
            "mode": current_mode(),
            "scorerId": SCORER_ID,
            "factorsMeta": {
                "channel_success": "通道历史成功率",
                "credential_quality": "凭证类型强度",
                "device_match": "基线指纹匹配度",
                "budget_sufficiency": "隐私预算余量",
                "member_maturity": "账龄+登录频次",
                "fail_history": "同通道失败计数",
                "voice_confidence": "声纹置信度",
                "portal_state": "角色四态基线",
            },
            "note": "44号学习闭环复用——champion/"
                    "challenger 双轨+护栏+漂移全继承",
        })
        return {"success": True, "status": view}

    # --------------------------------------------------------
    # 影子评分预览(challenger 试算——不生效)
    # --------------------------------------------------------

    async def score_preview(self, ctx: dict) -> dict:
        """影子评分预览(输入上下文试算——
        champion 当前口径, 不产生任何落库/生效)"""
        mode = current_mode()
        if mode != "on":
            raise ValueError(
                f"LOGIN53_MODE={mode}(默认 off——"
                f"模型面关闭)")
        result = await Login54Scorer().score(ctx)
        return {"success": True, "preview": result,
                "note": "影子评分——纯试算不落库不生效"}

    # --------------------------------------------------------
    # 模型生命周期事件留痕(P0 基础——后续期接入)
    # --------------------------------------------------------

    async def record_model_event(
            self, event_type: str, detail: dict) -> dict:
        """模型事件留痕(学习/晋升/回滚/漂移)"""
        event_id = await self.repo.next_model_event_id()
        record = {
            "modelEventId": event_id,
            "eventType": event_type,
            "detail": detail,
            "createdAt": ts(),
        }
        await self.repo.save_model_event(record)
        return record

    async def model_history(self) -> dict:
        """模型事件历史(最新在前——版本溯源)"""
        records = await self.repo.list_model_events(
            limit=100)
        return {"success": True,
                "total": len(records),
                "events": records}

    # --------------------------------------------------------
    # 注册表视图(观测面——不受开关影响)
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        """模型注册表自描述"""
        from services.login54_scorer import (
            CREDENTIAL_STRENGTH, PORTAL_TRUST_BASE,
        )
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        meta = SCORER_REGISTRY.get(SCORER_ID) or {}
        return {
            "module": "login54",
            "mode": current_mode(),
            "scorer": {
                "scorerId": SCORER_ID,
                "label": meta.get("label"),
                "batch": meta.get("batch"),
                "factorCount": len(Login54Scorer.WEIGHTS),
                "weights": Login54Scorer.WEIGHTS,
                "tiers": list(TIER_NAMES),
                "credentialStrength":
                    CREDENTIAL_STRENGTH,
                "portalTrustBase": PORTAL_TRUST_BASE,
            },
            "learning": {
                "engine": "44号 Hedge 在线学习",
                "versioning": "champion/challenger 双轨",
                "guardrail": "[0.5, 2.0] 倍护栏",
                "autoApply": "登录档案可配(默认关闭)",
            },
            "dualModel": {
                "strategy": "max 合成(保守)",
                "authRiskIndependent": True,
                "note": "54号等价风险分与 43号 "
                        "auth_risk 取 max——互补不替换",
            },
            "note": "自进化闭环: 决策回流→Hedge 学习"
                    "→评估晋升→漂移监控→治理红线"
                    "(46号范式全继承)",
        }
