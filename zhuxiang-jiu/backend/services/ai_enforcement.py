"""AI 决策阻断引擎(v7.8: 观察模式 → 真实拦截)

将 v7.6 的「观察不阻断」挂钩升级为可灰度上线的真实决策门:

    ┌────────────────────────────────────────────────────────────┐
    │  路由层(业务决策点) ──→ enforce_decision()                 │
    │        │                                                   │
    │        ├─ observe  纯观察(v7.6 现状): 评分+快照, 不生效    │
    │        ├─ shadow   影子模式: 决策门完整运行+审计, 不阻断    │
    │        │           (真实流量上验证命中率, 确认无误杀再切)   │
    │        └─ enforce  执行模式: 真实阻断/强制人工审核          │
    │                    └─ 四重误杀保护自动降级 shadow           │
    └────────────────────────────────────────────────────────────┘

三级模式(每评分器粒度, 运行时动态读取):
    AI_ENFORCE_MODE    全局模式: observe|shadow|enforce(默认 observe)
    AI_ENFORCE_SCOPES  域列表(逗号分隔): 列表内评分器取全局模式,
                       列表外自动降一级(enforce→shadow→observe)

四重误杀保护(任一触发, enforce 自动降级 shadow 并落审计):
    1. 冷启动门槛: 反馈样本 < 50 条(没学过不上岗)
    2. 正确率门槛: 已标注反馈正确率 < 70%(模型不可靠不上岗)
    3. 阻断率熔断: 1 小时滑动窗口内阻断率 > 30%(防大面积误杀)
    4. fail-open:  评分器异常/超时 → 放行+告警(业务永不因 AI 卡死)

决策动作映射(阈值类评分器 → 业务处置):
    block/high/hold  → blocked=True      (拒绝业务动作)
    review/medium/challenge → reviewRequired=True (强制人工/二次验证)
    pass/low/allow/pay    → 放行(走原有业务规则)

设计约定(延续项目哲学):
    - 决策门自身永不抛异常: 任何内部失败按 fail-open 放行返回
    - 审计/统计失败不影响决策返回(只记日志)
    - 快照逻辑复用 v7.6 ai_feedback_hooks(反馈闭环保持不变)
"""

import logging
import time

from repositories.ai_learning_repository import AiLearningRepository

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-enforcement"

# 三级模式序: observe < shadow < enforce
_MODE_RANK = {"observe": 0, "shadow": 1, "enforce": 2}

# 四重保护参数
COLD_START_MIN_FEEDBACK = 50     # 冷启动门槛: 反馈样本数
ACCURACY_MIN_RATIO = 0.70        # 正确率门槛
ACCURACY_CACHE_TTL = 60          # 正确率缓存(秒), 避免每次决策全量拉反馈
BURST_MAX_BLOCK_RATIO = 0.30     # 熔断: 窗口内阻断率上限
BURST_MIN_DECISIONS = 10         # 熔断: 窗口决策数低于此值不熔断(样本太少)

# 决策动作 → 业务处置映射(阈值类评分器统一语义)
BLOCK_ACTIONS = {"block", "high", "hold"}
REVIEW_ACTIONS = {"review", "medium", "challenge"}

# 正确率进程内缓存: scorerId → (计算时间, 正确率/None)
_accuracy_cache: dict[str, tuple[float, float | None]] = {}


# ============================================================
# 模式解析(运行时动态读取环境变量, 不冻结)
# ============================================================

def _downgrade(mode: str) -> str:
    return {"enforce": "shadow", "shadow": "observe"}.get(mode, "observe")


def enforcement_mode(scorer_id: str) -> str:
    """解析评分器当前模式(observe|shadow|enforce)

    AI_ENFORCE_SCOPES 配置时: 列表内取全局模式, 列表外降一级;
    未配置时全部评分器取全局模式。
    """
    import os
    mode = os.environ.get("AI_ENFORCE_MODE", "observe").strip().lower()
    if mode not in _MODE_RANK:
        mode = "observe"
    scopes = [s.strip() for s in
              os.environ.get("AI_ENFORCE_SCOPES", "").split(",")
              if s.strip()]
    if scopes and scorer_id not in scopes:
        mode = _downgrade(mode)
    return mode


# ============================================================
# 四重保护检查
# ============================================================

async def _recent_accuracy(scorer_id: str) -> float | None:
    """已标注反馈的正确率(None = 无已标注样本), 60s 进程内缓存"""
    cached = _accuracy_cache.get(scorer_id)
    now = time.time()
    if cached and now - cached[0] < ACCURACY_CACHE_TTL:
        return cached[1]
    try:
        repo = AiLearningRepository()
        feedback = await repo.list_feedback(scorer_id, limit=0)
        labeled = [f for f in feedback if f.get("correct") is not None]
        accuracy = None
        if labeled:
            accuracy = sum(1 for f in labeled if f.get("correct")) / len(labeled)
        _accuracy_cache[scorer_id] = (now, accuracy)
        return accuracy
    except Exception as exc:
        logger.warning("正确率统计失败(scorer=%s): %s", scorer_id, exc)
        return None


async def _protection_check(scorer_id: str) -> tuple[bool, str | None]:
    """enforce 模式保护检查, 返回 (是否放行执行, 降级原因)"""
    repo = AiLearningRepository()
    # 1. 冷启动门槛
    total = await repo.count_feedback(scorer_id)
    if total < COLD_START_MIN_FEEDBACK:
        return False, f"cold_start:反馈样本 {total} < {COLD_START_MIN_FEEDBACK}"
    # 2. 正确率门槛
    accuracy = await _recent_accuracy(scorer_id)
    if accuracy is not None and accuracy < ACCURACY_MIN_RATIO:
        return False, f"low_accuracy:正确率 {accuracy:.0%} < {ACCURACY_MIN_RATIO:.0%}"
    # 3. 阻断率熔断(窗口决策数充足才检查)
    blocked = await repo.get_burst_window(scorer_id, "blocked")
    total_window = await repo.get_burst_window(scorer_id, "total")
    if (total_window >= BURST_MIN_DECISIONS
            and blocked / total_window > BURST_MAX_BLOCK_RATIO):
        return False, (f"burst:窗口阻断率 {blocked}/{total_window} "
                       f"超过 {BURST_MAX_BLOCK_RATIO:.0%}")
    return True, None


# ============================================================
# 决策门
# ============================================================

def _pass_result(scorer_id: str, business_key: str, mode: str,
                 note: str) -> dict:
    """放行结果(fail-open / observe 未启用等场景)"""
    return {
        "scorerId": scorer_id, "businessKey": business_key,
        "mode": mode, "action": None, "effectiveMode": mode,
        "blocked": False, "reviewRequired": False,
        "degraded": False, "degradeReason": None,
        "score": None, "weightVersion": None,
        "note": note, "decidedAt": _now(),
    }


def _now() -> str:
    from core.helpers import ts
    return ts()


async def enforce_decision(scorer_id: str, business_key: str,
                           ctx: dict) -> dict:
    """决策门入口(业务路由调用, 永不抛异常)

    Returns:
        {
            mode: 配置模式, effectiveMode: 实际生效模式(降级后),
            action: 评分器决策动作, blocked: 是否真实阻断,
            reviewRequired: 是否强制人工, degraded: 是否因保护降级,
            degradeReason, score, weightVersion, decidedAt
        }
        业务侧约定: blocked=True → 拒绝业务动作(409);
                    reviewRequired=True → 转人工/二次验证; 其余放行。
    """
    mode = enforcement_mode(scorer_id)
    if mode == "observe":
        # 观察模式 = v7.6 现状: 评分+快照供反馈闭环, 决策不生效
        try:
            from services.ai_feedback_hooks import score_and_snapshot
            await score_and_snapshot(scorer_id, business_key, ctx)
        except Exception as exc:
            logger.warning("观察模式评分失败(scorer=%s): %s", scorer_id, exc)
        return _pass_result(scorer_id, business_key, "observe",
                            "观察模式: 决策不生效")

    # ---- shadow / enforce: 运行决策门 ----
    from services.ai_feedback_hooks import (
        _extract_decision, _factors_from_result, _invoke_scorer,
        snapshot_decision,
    )
    try:
        result = await _invoke_scorer(scorer_id, ctx)
    except Exception as exc:
        logger.warning("决策门评分异常, fail-open 放行(scorer=%s): %s",
                       scorer_id, exc)
        record = _pass_result(scorer_id, business_key, mode,
                              f"fail_open:{exc}")
        record["degraded"] = True
        record["degradeReason"] = f"fail_open:{type(exc).__name__}"
        await _audit_and_stats(record, scored=False)
        return record
    if result is None:
        # 评分器未注册/返回空 → 放行(等价 fail-open)
        record = _pass_result(scorer_id, business_key, mode,
                              "scorer_unavailable")
        record["degraded"] = True
        record["degradeReason"] = "scorer_unavailable"
        await _audit_and_stats(record, scored=False)
        return record

    action = _extract_decision(scorer_id, result)
    score = float(result.get("score")
                  or (result.get("recommendation") or {}).get("score")
                  or 0)
    weight_version = result.get("weightVersion", "v1")

    # enforce 保护检查(shadow 不检查, 保持影子纯度)
    effective_mode = mode
    degrade_reason: str | None = None
    if mode == "enforce":
        try:
            allowed, reason = await _protection_check(scorer_id)
            if not allowed:
                effective_mode = "shadow"
                degrade_reason = reason
                logger.warning("enforce 自动降级 shadow(scorer=%s): %s",
                               scorer_id, reason)
        except Exception as exc:
            logger.warning("保护检查异常(scorer=%s): %s", scorer_id, exc)

    blocked = (effective_mode == "enforce"
               and action in BLOCK_ACTIONS)
    review_required = (effective_mode == "enforce"
                       and action in REVIEW_ACTIONS)

    # v7.9 知识增强: 检索 top-k 相似案例证据(火后不管, 只审计+加严)
    knowledge = None
    try:
        from services.ai_knowledge_service import (
            augment_with_knowledge, should_escalate_review,
        )
        knowledge = await augment_with_knowledge(
            scorer_id, _factors_from_result(scorer_id, result) or [], score)
    except Exception as exc:
        logger.debug("决策门知识增强跳过(scorer=%s): %s", scorer_id, exc)

    # 证据驱动复核升级(只加严: pass → 人工复核; 永不 blocked/降级)
    kb_escalated = False
    if (effective_mode == "enforce" and not blocked
            and should_escalate_review(knowledge)):
        review_required = True
        kb_escalated = True
        logger.info("知识证据升级人工复核(scorer=%s key=%s wrongRate=%s "
                    "evidence=%s)", scorer_id, business_key,
                    knowledge.get("wrongRate"),
                    knowledge.get("evidenceCount"))

    record = {
        "scorerId": scorer_id, "businessKey": business_key,
        "mode": mode, "action": action, "effectiveMode": effective_mode,
        "blocked": blocked, "reviewRequired": review_required,
        "degraded": degrade_reason is not None,
        "degradeReason": degrade_reason,
        "score": score, "weightVersion": weight_version,
        "note": ("kb_evidence:相似案例错误率 "
                 f"{knowledge.get('wrongRate')}" if kb_escalated else ""),
        "decidedAt": _now(),
    }
    if knowledge:
        record["knowledge"] = {
            "evidenceCount": knowledge.get("evidenceCount"),
            "wrongRate": knowledge.get("wrongRate"),
            "avgSimilarity": knowledge.get("avgSimilarity"),
            "calibratedScore": knowledge.get("calibratedScore"),
            "regionUnreliable": knowledge.get("regionUnreliable"),
        }

    # 决策快照(复用 v7.6: 终态事件配对自动反馈)
    try:
        await snapshot_decision(scorer_id, business_key, result)
    except Exception as exc:
        logger.warning("决策快照失败(scorer=%s key=%s): %s",
                       scorer_id, business_key, exc)

    # 审计 + 统计 + 熔断窗口计数
    await _audit_and_stats(record, scored=True)

    if blocked:
        logger.info("AI 阻断生效(scorer=%s key=%s action=%s score=%s)",
                    scorer_id, business_key, action, score)
    return record


async def _audit_and_stats(record: dict, *, scored: bool) -> None:
    """落审计 + 累计统计(失败只记日志, 不影响决策)"""
    try:
        repo = AiLearningRepository()
        scorer_id = record["scorerId"]
        await repo.add_enforcement_audit(scorer_id, dict(record))
        fields = ["total"]
        if record.get("blocked"):
            fields.append("blocked")
        if record.get("reviewRequired"):
            fields.append("reviews")
        if record.get("degraded"):
            fields.append("degraded")
        await repo.incr_enforcement_stats(scorer_id, *fields)
        # 熔断窗口: 真实阻断才计 blocked, 决策总数始终累计
        if scored:
            await repo.incr_burst_window(scorer_id, "total")
            if record.get("blocked"):
                await repo.incr_burst_window(scorer_id, "blocked")
    except Exception as exc:
        logger.warning("阻断审计/统计失败(scorer=%s): %s",
                       record.get("scorerId"), exc)


# ============================================================
# 查询接口(路由/驾驶舱用)
# ============================================================

async def enforcement_overview(scorer_id: str) -> dict:
    """阻断运行概览(模式/统计/熔断窗口/保护参数)"""
    repo = AiLearningRepository()
    stats = await repo.get_enforcement_stats(scorer_id)
    return {
        "success": True, "scorerId": scorer_id,
        "mode": enforcement_mode(scorer_id),
        "stats": {
            "total": stats.get("total", 0),
            "blocked": stats.get("blocked", 0),
            "reviews": stats.get("reviews", 0),
            "degraded": stats.get("degraded", 0),
            "blockRate": (round(stats.get("blocked", 0)
                                / stats["total"], 4)
                          if stats.get("total") else 0.0),
        },
        "burstWindow": {
            "seconds": repo.BURST_WINDOW_SECONDS,
            "total": await repo.get_burst_window(scorer_id, "total"),
            "blocked": await repo.get_burst_window(scorer_id, "blocked"),
        },
        "protections": {
            "coldStartMinFeedback": COLD_START_MIN_FEEDBACK,
            "accuracyMinRatio": ACCURACY_MIN_RATIO,
            "burstMaxBlockRatio": BURST_MAX_BLOCK_RATIO,
            "burstMinDecisions": BURST_MIN_DECISIONS,
        },
        "modelVersion": MODEL_VERSION, "generatedAt": _now(),
    }
