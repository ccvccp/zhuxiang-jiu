"""54号·小竹AI智能登录引擎大模型 自主学习进化
(login54_learn_service, P2)

计划(docs/54号_小竹AI智能登录引擎大模型实施计划.md §六 P2):
    - 学习轮次触发(min_feedback=10)+Hedge 更新
      (44号 run_learning_cycle 复用——不重造轮子)
    - 护栏验证([0.5,2.0] 倍)+归一化断言
    - 影子评分对比(challenger vs champion——
      模拟决策对比)

设计(44号薄包装层):
    - run_learning: 包装 run_learning_cycle
      (login_orchestration) → login54_model_events
      留痕(learning/promoted 两类事件)——版本溯源
    - shadow_compare: challenger vs champion 对
      同一上下文的双轨试算对比(决策档位差异
      ——shadow 语义, 不落库不生效)
    - learn 前置检查: 44号 冻结守卫已在
      run_learning_cycle 内建(46号联动继承)

QC(计划 §六 P2):
    - 权重演进单调收敛: 护栏约束下权重演进
      数学断言(测试覆盖)
    - 灾难性遗忘防护: 44号 v7.9 replay 机制
      (replay 配置开关联动——测试覆盖)

零侵入红线: 44号 学习引擎零改动(纯调用式包装)。
"""

import logging

from core.helpers import ts

from services.login54_scorer import Login54Scorer

logger = logging.getLogger("login54_learn_service")

MODEL_VERSION = "v1-login54-learn"

SCORER_ID = "login_orchestration"

# 影子对比差异语义(档位差 → 处置建议)
TIER_ORDER = {"silent": 0, "one_tap": 1,
              "step_up": 2, "enhanced": 3}


class Login54LearnService:
    """54号自主学习进化(44号 Hedge 引擎包装+
    模型事件留痕+影子对比)"""

    # --------------------------------------------------------
    # 学习轮次(44号 run_learning_cycle 复用)
    # --------------------------------------------------------

    async def run_learning(self) -> dict:
        """触发一轮在线学习(Hedge→challenger/auto 晋升)

        44号引擎内建: min_feedback 门槛/护栏 [0.5,2.0]
        倍/归一化/冻结守卫(46号 fail-soft)/replay 回放。

        Raises:
            ValueError: 待学习反馈不足/治理冻结中
        """
        from services.ai_learning_service import (
            run_learning_cycle,
        )
        result = await run_learning_cycle(SCORER_ID)

        # 模型事件留痕(learning 轮次——版本溯源)
        try:
            from services.login54_service import (
                Login54Service,
            )
            promoted = bool(result.get("promoted"))
            await Login54Service().record_model_event(
                "promoted" if promoted else "learning", {
                    "scorerId": SCORER_ID,
                    "learnedFrom":
                        result.get("learnedFrom"),
                    "replayedFrom":
                        result.get("replayedFrom"),
                    "parentVersion":
                        result.get("parentVersion"),
                    "newVersion":
                        result.get("newVersion"),
                    "promoted": promoted,
                    "weightDelta":
                        result.get("weightDelta"),
                    "championMetrics":
                        result.get(
                            "championMetrics"),
                    "challengerMetrics":
                        result.get(
                            "challengerMetrics"),
                })
        except Exception as exc:  # noqa: BLE001
            # 留痕 fail-soft——学习本体不受影响
            logger.warning(
                "login54_learn_event_failed: %s", exc)

        result.update({
            "module": "login54",
            "note": "44号 Hedge 引擎复用——护栏 [0.5,2.0]"
                    " 倍+归一化+冻结守卫内建",
            "learnedAt": ts(),
        })
        return result

    # --------------------------------------------------------
    # 影子评分对比(challenger vs champion)
    # --------------------------------------------------------

    async def shadow_compare(self, ctx: dict) -> dict:
        """双轨影子对比(同一上下文: challenger 权重 vs
        champion 权重——模拟决策对比, 不落库不生效)

        实现口径: challenger 权重临时注入 scorer 评分
        (load_effective_weights 覆盖点), 与 champion
        当前口径并排呈现+档位差异语义。
        """
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(SCORER_ID)
        champion = (view.get("champion") or {})
        challenger = (view.get("challenger") or {})
        champion_weights = champion.get("weights") or {}
        challenger_weights = challenger.get("weights") or {}

        # champion 轨(当前生效口径)
        champion_result = await Login54Scorer().score(
            ctx)

        # challenger 轨(影子权重注入试算)
        if challenger_weights:
            challenger_result = await self._score_with(
                challenger_weights, ctx)
        else:
            challenger_result = None

        comparison = None
        if challenger_result is not None:
            c_tier = champion_result.get("tier")
            h_tier = challenger_result.get("tier")
            diff = (TIER_ORDER.get(h_tier, 0)
                    - TIER_ORDER.get(c_tier, 0))
            if diff > 0:
                verdict = "challenger更严"
            elif diff < 0:
                verdict = "challenger更宽"
            else:
                verdict = "档位一致"
            comparison = {
                "championTier": c_tier,
                "challengerTier": h_tier,
                "tierDiff": diff,
                "verdict": verdict,
                "championTrust":
                    champion_result.get("trustScore"),
                "challengerTrust":
                    challenger_result.get("trustScore"),
            }

        return {
            "success": True,
            "scorerId": SCORER_ID,
            "champion": {
                "version": champion.get("version"),
                "result": champion_result,
            },
            "challenger": {
                "version": challenger.get("version"),
                "result": challenger_result,
            } if challenger_result else None,
            "comparison": comparison,
            "note": "影子对比——双轨试算不落库不生效",
            "comparedAt": ts(),
        }

    @staticmethod
    async def _score_with(weights: dict,
                          ctx: dict) -> dict:
        """指定权重试算(load_effective_weights 单点
        覆盖——44号缓存失效边界内安全)"""
        import services.ai_learning_service as als

        original = als.load_effective_weights

        async def _patched(scorer_id, defaults):
            # 仅拦截 login54 档案——其他档案原轨
            if scorer_id == SCORER_ID:
                return dict(weights)
            return await original(scorer_id, defaults)

        als.load_effective_weights = _patched
        try:
            return await Login54Scorer().score(ctx)
        finally:
            als.load_effective_weights = original

    # --------------------------------------------------------
    # 学习就绪态(观测面)
    # --------------------------------------------------------

    async def learning_readiness(self) -> dict:
        """学习就绪态(pending 反馈数/门槛/护栏配置
        ——观测面, 不触发学习)"""
        from repositories.ai_learning_repository \
            import AiLearningRepository
        repo = AiLearningRepository()
        pending = await repo.list_feedback(
            SCORER_ID, status="pending", limit=1000)
        view = await self._weights_view()
        config = view.get("config") or {}
        return {
            "success": True,
            "scorerId": SCORER_ID,
            "pendingFeedback": len(pending),
            "minFeedback":
                config.get("min_feedback", 10),
            "ready": len(pending) >= int(
                config.get("min_feedback", 10)),
            "config": {
                "eta": config.get("eta"),
                "guardrail": config.get("guardrail"),
                "autoApply": config.get("auto_apply"),
                "replay": config.get("replay"),
            },
            "championVersion":
                (view.get("champion") or {})
                .get("version"),
            "challengerVersion":
                (view.get("challenger") or {})
                .get("version"),
            "note": "min_feedback 门槛+护栏+replay"
                    "(44号 DEFAULT_LEARNING_CONFIG)",
            "checkedAt": ts(),
        }

    @staticmethod
    async def _weights_view() -> dict:
        from services.ai_learning_service import (
            get_weights_view,
        )
        return await get_weights_view(SCORER_ID)
