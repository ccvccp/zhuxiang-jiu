"""54号·小竹AI智能登录引擎大模型 自主学习进化
(login54_learn_service, P2+P3)

计划(docs/54号_小竹AI智能登录引擎大模型实施计划.md §六 P2/P3):
    P2:
    - 学习轮次触发(min_feedback=10)+Hedge 更新
      (44号 run_learning_cycle 复用——不重造轮子)
    - 护栏验证([0.5,2.0] 倍)+归一化断言
    - 影子评分对比(challenger vs champion——
      模拟决策对比)
    P3:
    - 自主升级: auto_apply 晋升+版本快照
      (login54_model_events——审计+回滚依据)
    - 自主回滚: 滑动窗口指标回退检测 →
      自动回退上一版本+冻结学习+告警
    - 手动通道: promote/rollback 端点(人工兜底)

设计(44号薄包装层):
    - run_learning: 包装 run_learning_cycle
      (login_orchestration) → login54_model_events
      留痕(learning/promoted 两类事件)——版本溯源
    - shadow_compare: challenger vs champion 对
      同一上下文的双轨试算对比(决策档位差异
      ——shadow 语义, 不落库不生效)
    - promote: 44号 promote_challenger 包装
      +promoted 事件留痕(手动通道)
    - rollback: 44号历史版本权重回滚为新冠军
      (source=rollback 版本记录, 旧冠军入历史)
    - check_regression: 滑动窗口指标回退检测
      (晋升 baseline vs 当前 recent 反馈评估
      ——超阈值自动回滚+冻结+告警)

QC(计划 §六 P3):
    - 晋升-回滚闭环: 手动晋升→回滚→版本历史
      可溯(测试覆盖)
    - 自主升级翻车防护: 滑动窗口回退自动回滚
      (测试覆盖)

零侵入红线: 44号 学习引擎零改动(纯调用式包装
——rollback 直接操作 repo 与 44号 reset_weights
同构范式)。
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

# 滑动窗口回归检测(P3——自主升级翻车防护)
REGRESSION_WINDOW = 50        # 滑动窗口反馈数
REGRESSION_THRESHOLD = 0.3    # rewardAlignment 下降阈值
REGRESSION_MIN_SAMPLES = 5   # 晋升后最少反馈数(不足不判)


def _regression_threshold() -> float:
    """回归阈值(环境变量可覆盖——LOGIN54_REGRESSION_THRESHOLD)"""
    import os
    try:
        return abs(float(os.environ.get(
            "LOGIN54_REGRESSION_THRESHOLD",
            str(REGRESSION_THRESHOLD))))
    except ValueError:
        return REGRESSION_THRESHOLD


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

    # ============================================================
    # P3: 手动晋升(人工通道——auto_apply 外兜底)
    # ============================================================

    async def promote(self) -> dict:
        """手动晋升挑战者为冠军(44号 promote_challenger
        包装+promoted 事件留痕)

        Raises:
            ValueError: 无可晋升的挑战者
        """
        from services.ai_learning_service import (
            get_weights_view, promote_challenger,
        )
        result = await promote_challenger(SCORER_ID)
        # 晋升基线(新冠军版本记录 stats——
        # 学习时点的回放评估指标, 供回归检测参照)
        view = await get_weights_view(SCORER_ID)
        stats = (view.get("champion") or {}) \
            .get("stats") or {}
        try:
            from services.login54_service import (
                Login54Service,
            )
            await Login54Service().record_model_event(
                "promoted", {
                    "scorerId": SCORER_ID,
                    "channel": "manual",
                    "previousVersion":
                        result.get("previousVersion"),
                    "newVersion":
                        result.get("promotedVersion"),
                    "promoted": True,
                    "challengerMetrics": stats,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "login54_promote_event_failed: %s", exc)
        result.update({
            "module": "login54",
            "note": "手动晋升通道(44号引擎复用)",
            "promotedAt": ts(),
        })
        return result

    # ============================================================
    # P3: 版本回滚(历史版本权重→新冠军)
    # ============================================================

    async def rollback(self, version_id: str = None,
                       reason: str = "") -> dict:
        """回滚到历史版本(指定 versionId; 缺省→
        退役前一代冠军——44号 history 最新一条)

        实现: 44号 reset_weights 同构范式(直接 repo
        操作——目标版本权重作为新冠军 source=rollback,
        旧冠军+挑战者入历史, 版本号递增可溯)。

        Raises:
            ValueError: 无可回滚历史/版本不存在
        """
        from core.locks import get_lock
        from repositories.ai_learning_repository \
            import AiLearningRepository
        import services.ai_learning_service as als

        repo = AiLearningRepository()
        async with get_lock(f"ai_learning:{SCORER_ID}"):
            profile = await als._load_profile(
                SCORER_ID, repo)
            champion = profile["champion"]
            history = await repo.list_history(
                SCORER_ID, limit=100)
            if not history:
                raise ValueError(
                    "无可回滚历史版本(仅默认冠军在役)")

            if version_id:
                target = next(
                    (h for h in history
                     if h.get("version") == version_id),
                    None)
                if target is None:
                    raise ValueError(
                        f"历史版本 {version_id} 不存在"
                        f"(可用: {[h.get('version') for h in history[:10]]})")
            else:
                target = history[0]   # 最新退役

            new_version = als._next_version(
                SCORER_ID, profile=profile,
                history=history)
            note = reason or f"回滚自 {target.get('version')}"
            record = als._build_version_record(
                new_version, target.get("weights") or {},
                "rollback", champion["version"],
                stats=target.get("stats") or {},
                note=note)
            await repo.add_history(SCORER_ID, champion)
            if profile.get("challenger"):
                await repo.add_history(
                    SCORER_ID, profile["challenger"])
            profile["champion"] = record
            profile["challenger"] = None
            await repo.save_profile(SCORER_ID, profile)
            als.invalidate_weight_cache(SCORER_ID)

        # 模型事件留痕(rollback——审计溯源)
        try:
            from services.login54_service import (
                Login54Service,
            )
            await Login54Service().record_model_event(
                "rollback", {
                    "scorerId": SCORER_ID,
                    "channel": "manual",
                    "fromVersion": champion["version"],
                    "toVersion": new_version,
                    "targetVersion":
                        target.get("version"),
                    "reason": reason,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "login54_rollback_event_failed: %s", exc)

        logger.info("login54_rollback %s→%s(target=%s)",
                    champion["version"], new_version,
                    target.get("version"))
        return {
            "success": True, "scorerId": SCORER_ID,
            "fromVersion": champion["version"],
            "newVersion": new_version,
            "targetVersion": target.get("version"),
            "weights": record["weights"],
            "reason": note, "rolledBackAt": ts(),
        }

    # ============================================================
    # P3: 滑动窗口回归检测(自主升级翻车防护)
    # ============================================================

    async def check_regression(self) -> dict:
        """滑动窗口指标回退检测(晋升 baseline vs
        当前 recent 反馈评估)

        判定口径: 最近 promoted 事件的
        challengerMetrics.rewardAlignment 为 baseline;
        44号池 recent 反馈按当前 champion 权重回放
        评估——下降超阈值 → 自动回滚+冻结+告警。

        触发动作: rollback(晋升前版本)+46号冻结
        +regression_rollback 事件留痕。
        """
        from repositories.login54_repository import (
            Login54Repository,
        )
        events = await Login54Repository(
        ).list_model_events(limit=100)
        promoted = [e for e in events
                    if e.get("eventType") == "promoted"]
        if not promoted:
            return {
                "success": True, "applicable": False,
                "reason": "无晋升基线(默认冠军在役——"
                          "无升级可检)",
                "checkedAt": ts(),
            }

        # 晋升 baseline(promoted 事件时点指标)
        detail = promoted[0].get("detail") or {}
        baseline = ((detail.get("challengerMetrics")
                     or {}).get("rewardAlignment"))
        baseline_version = detail.get("newVersion")
        if baseline is None:
            baseline = 0.0

        # 当前指标(recent 反馈 × champion 权重回放)
        import services.ai_learning_service as als
        from repositories.ai_learning_repository \
            import AiLearningRepository
        repo = AiLearningRepository()
        recent = await repo.list_feedback(
            SCORER_ID, limit=REGRESSION_WINDOW)
        if len(recent) < REGRESSION_MIN_SAMPLES:
            return {
                "success": True, "applicable": False,
                "reason": f"晋升后反馈不足"
                          f"({len(recent)}/"
                          f"{REGRESSION_MIN_SAMPLES})",
                "baseline": baseline,
                "checkedAt": ts(),
            }
        view = await self._weights_view()
        champion_weights = ((view.get("champion")
                             or {}).get("weights") or {})
        metrics = als._evaluate(
            champion_weights, recent, SCORER_ID)
        current = metrics.get("rewardAlignment") or 0.0

        drop = round(baseline - current, 4)
        threshold = _regression_threshold()
        result = {
            "success": True, "applicable": True,
            "baselineVersion": baseline_version,
            "baseline": baseline,
            "current": current,
            "drop": drop,
            "threshold": threshold,
            "regressed": drop > threshold,
            "checkedAt": ts(),
        }

        if not result["regressed"]:
            return result

        # 回退确认 → 自动回滚+冻结+告警
        rollback_result = await self.rollback(
            reason=f"滑动窗口回退({drop}>{threshold})"
                    f"——自动回滚")
        freeze_result = await self._freeze_for_regression(
            f"版本 {baseline_version} 晋升后指标回退"
            f"(baseline={baseline} current={current})")
        try:
            from services.login54_service import (
                Login54Service,
            )
            await Login54Service().record_model_event(
                "regression_rollback", {
                    "scorerId": SCORER_ID,
                    "channel": "auto",
                    "baselineVersion": baseline_version,
                    "baseline": baseline,
                    "current": current,
                    "drop": drop,
                    "threshold": threshold,
                    "rollback": {
                        "fromVersion":
                            rollback_result
                            .get("fromVersion"),
                        "toVersion":
                            rollback_result
                            .get("newVersion"),
                    },
                    "frozen": freeze_result.get("frozen"),
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "login54_regression_event_failed: %s",
                exc)
        result.update({
            "action": "auto_rollback",
            "rollback": rollback_result,
            "freeze": freeze_result,
        })
        return result

    @staticmethod
    async def _freeze_for_regression(
            note: str) -> dict:
        """回归回滚后冻结学习(46号审批总线——
        人工复核解锁; fail-soft; 冷态自愈: 注册中心
        未入册时幂等 sync 后再申请)"""
        try:
            from services.ai_governance_service import (
                AiGovernanceService,
            )
            gov = AiGovernanceService()
            # 冷态自愈(sync 幂等——治理状态保留)
            if await gov.repo.get_gov(SCORER_ID) is None:
                await gov.sync_registry()
            change = await gov.submit_change(
                SCORER_ID, "freeze", {},
                f"54号自动回滚联动冻结: {note}")
            review = await gov.review_change(
                change["changeId"], True, "login54-auto")
            return {"frozen": True,
                    "changeId": change["changeId"],
                    "review": review.get("status")}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "login54_regression_freeze_failed: %s",
                exc)
            return {"frozen": False,
                    "error": str(exc)[:80]}
