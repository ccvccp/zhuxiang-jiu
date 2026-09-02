"""AI 自学习层服务(v7.4 → v7.5: 评分器自学习与自我优化能力)

参照成熟 AI 大模型的在线学习(Online Learning)闭环, 为全部 14 个评分器
提供「决策 → 反馈 → 学习 → 评估 → 晋升 → 漂移监控」的自我优化能力:

    ┌────────────────────────────────────────────────────────────┐
    │  评分器(生产) ──决策(因子快照+动作)──→ 反馈记录              │
    │      ↑                              │(真实结果标注)         │
    │  冠军权重(生效)                      ↓                      │
    │      ↑←──晋升(评估更优)←── 挑战者权重 ←── Hedge 在线学习 ──┘ │
    │                                   (护栏约束+归一化)          │
    │  漂移监控: 因子分数 EMA 偏离基线 → 漂移告警                   │
    └────────────────────────────────────────────────────────────┘

核心算法: Hedge(乘性权重更新, 在线学习的经典算法)
    每条反馈按因子影响度(influence = contribution / total)对权重做乘性更新:
        correct  → w[f] *= exp(eta × influence)   (奖励贡献大的因子)
        incorrect→ w[f] *= exp(-eta × influence)  (惩罚误导因子)
    更新后经护栏约束(相对默认值 [1/guardrail, guardrail] 倍)与归一化,
    保证权重演进始终在业务可解释的安全区间内(成熟 MLOps 惯例)。

版本管理: 冠军/挑战者(Champion/Challenger)双轨制
    - champion: 生产生效权重(评分器通过权重缓存读取)
    - challenger: 学习产出或人工调整的影子版本, 评估更优后晋升
    - 全部退役版本进入历史, 支持审计与回滚(重置/人工覆盖即回滚手段)

设计约定:
    - 评分路径零侵入: load_effective_weights 只读 + 进程内缓存(TTL 30s)
      + 失败回退默认值, 自学习层任何异常都不阻塞评分
    - 读-改-写操作(学习/晋升/覆盖/重置)统一走 core.locks.get_lock
    - 异常约定: KeyError → 404(未知评分器), ValueError → 409(业务冲突)
    - 全 async(项目约定)
"""

import logging
import math
import time

from core.helpers import ts
from core.locks import get_lock
from repositories.ai_learning_repository import AiLearningRepository

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-learning"

# ============================================================
# 评分器注册表(13 评分器 + 物流路由 3 个策略子键 + 36号热点蹭点
# = 17 个可学习档案)
# ============================================================

SCORER_REGISTRY = {
    # ---- 第一批(ai_scoring_service) ----
    "order_risk":                  {"label": "订单风控评分", "module": "04订单管理", "batch": 1},
    "payment_routing":             {"label": "支付路由评分", "module": "05收款管理", "batch": 1},
    "logistics_routing:speed":     {"label": "物流路由评分(时效优先)", "module": "06物流接口", "batch": 1},
    "logistics_routing:cost":      {"label": "物流路由评分(成本优先)", "module": "06物流接口", "batch": 1},
    "logistics_routing:balanced":  {"label": "物流路由评分(均衡)", "module": "06物流接口", "batch": 1},
    "traffic_antifraud":           {"label": "流量防作弊评分", "module": "11流量管理", "batch": 1},
    "promotion_antifraud":         {"label": "推广码防作弊评分", "module": "29推广码矩阵获利", "batch": 1},
    # ---- 第二批(ai_scoring_ext_service) ----
    "member_profile":              {"label": "会员智能画像评分", "module": "02会员管理", "batch": 2},
    "points_risk":                 {"label": "积分防薅羊毛评分", "module": "03会员积分", "batch": 2},
    "message_content":             {"label": "信息内容审核评分", "module": "08信息管理", "batch": 2},
    "withdraw_risk":               {"label": "提现风控评分", "module": "12钱包盈利", "batch": 2},
    "groupbuy_qualify":            {"label": "团购资格评分", "module": "14团购模块", "batch": 2},
    "admin_operation":             {"label": "后台操作风险评分", "module": "17后台管理", "batch": 2},
    "agreement_risk":              {"label": "合同条款风险评分", "module": "18条款规则合同", "batch": 2},
    "finance_anomaly":             {"label": "财务异常检测评分", "module": "19财务管理", "batch": 2},
    # ---- 第三批(ai_scoring_auth_service) ----
    "auth_risk":                   {"label": "认证风控评分", "module": "30用户认证", "batch": 3},
    # ---- 第四批(36号智能推广 P2: 热点蹭点评分 Hedge 效果回流) ----
    "promo_hotspot":               {"label": "热点蹭点评分", "module": "36智能推广", "batch": 4},
    # ---- 第五批(37号网站同盟 P0/P1: 入盟预审+评价审评) ----
    "alliance_onboarding":         {"label": "同盟入驻预审评分", "module": "37网站同盟", "batch": 5},
    "alliance_review":             {"label": "同盟评价审评评分", "module": "37网站同盟", "batch": 5},
    # ---- 第六批(38号AI智能产品管理 P0: 商品上架预审) ----
    "product_gate":                {"label": "商品上架预审评分", "module": "38产品管理", "batch": 6},
    # ---- 第七批(40号平台流量DV博主 P0: 作品跟随价值评分) ----
    "blogger_work_gate":           {"label": "博主作品跟随价值评分", "module": "40博主引流", "batch": 7},
}

# 决策阈值表(用于冠军/挑战者回放评估: 因子快照 × 权重 → 模拟动作 → 与期望动作比对)
# 与各评分器 score() 内的等级映射保持一致; 路由类评分器(payment/logistics)无阈值,
# 评估改用奖励对齐度(rewardAlignment)。
DECISION_THRESHOLDS = {
    "order_risk":         [(65.0, "block"), (35.0, "review"), (0.0, "pass")],
    "traffic_antifraud":  [(60.0, "block"), (30.0, "review"), (0.0, "pass")],
    "promotion_antifraud": [(60.0, "review"), (30.0, "hold"), (0.0, "pay")],
    "member_profile":     [(70.0, "high_value"), (40.0, "standard"), (0.0, "at_risk")],
    "points_risk":        [(60.0, "high"), (30.0, "medium"), (0.0, "low")],
    "message_content":    [(60.0, "high"), (30.0, "medium"), (0.0, "low")],
    "withdraw_risk":      [(55.0, "high"), (25.0, "medium"), (0.0, "low")],
    "groupbuy_qualify":   [(80.0, "T3"), (60.0, "T2"), (40.0, "T1"), (0.0, "rejected")],
    "admin_operation":    [(60.0, "high"), (30.0, "medium"), (0.0, "low")],
    "agreement_risk":     [(60.0, "high"), (30.0, "medium"), (0.0, "low")],
    "finance_anomaly":    [(50.0, "high"), (25.0, "medium"), (0.0, "low")],
    "auth_risk":          [(70.0, "block"), (50.0, "challenge"),
                           (25.0, "step_up"), (0.0, "allow")],
}

# 学习配置默认值(可按评分器覆盖)
DEFAULT_LEARNING_CONFIG = {
    "eta": 0.5,           # Hedge 学习率(乘性更新强度)
    "min_feedback": 10,   # 触发一轮学习所需最小待学习反馈数
    "auto_apply": False,  # 学习结果评估更优时是否自动晋升为冠军
    "guardrail": 2.0,     # 权重护栏: 相对默认值允许的最大倍数(下限为 1/guardrail)
    # v7.9 经验回放(参照强化学习 Replay Buffer, 默认关闭保证零影响):
    # 开启后每轮学习把知识库近期案例混入 Hedge 更新样本,
    # 与最新反馈混合训练, 防止在线学习对最近批次过拟合(灾难性遗忘)
    "replay": False,
    "replay_sample": 50,  # 每轮回放的近期案例数上限
}

DRIFT_ALPHA = 0.1          # 漂移 EMA 平滑系数
DRIFT_LEVELS = [(0.25, "high"), (0.10, "medium"), (0.0, "low")]


def default_weights(scorer_id: str) -> dict:
    """获取评分器默认权重(懒加载评分器类, 避免循环导入)"""
    if scorer_id == "promo_hotspot":
        # 36号智能推广: 热点评分四因子(热度/速度/相关度/持续),
        # 常量定义在 promo_radar_service(单一事实源)
        from services.promo_radar_service import DEFAULT_RADAR_WEIGHTS
        return dict(DEFAULT_RADAR_WEIGHTS)
    if scorer_id == "alliance_onboarding":
        # 37号网站同盟: 入盟预审五因子, 单一事实源在评分器类
        from services.ai_scoring_service import AllianceOnboardingScorer
        return dict(AllianceOnboardingScorer.WEIGHTS)
    if scorer_id == "alliance_review":
        # 37号网站同盟: 评价审评五因子
        from services.ai_scoring_service import AllianceReviewScorer
        return dict(AllianceReviewScorer.WEIGHTS)
    if scorer_id == "product_gate":
        # 38号产品管理: 上架预审五因子, 单一事实源在评分器类
        from services.ai_scoring_service import ProductGateScorer
        return dict(ProductGateScorer.WEIGHTS)
    if scorer_id == "blogger_work_gate":
        # 40号博主引流: 作品跟随价值五因子, 单一事实源在评分器类
        from services.ai_scoring_service import BloggerWorkScorer
        return dict(BloggerWorkScorer.WEIGHTS)
    if scorer_id.startswith("logistics_routing:"):
        from services.ai_scoring_service import _BUDGET_WEIGHTS
        budget = scorer_id.split(":", 1)[1]
        if budget not in _BUDGET_WEIGHTS:
            raise KeyError(f"未知的评分器: {scorer_id}")
        return dict(_BUDGET_WEIGHTS[budget])
    _SCORER_CLASSES = {}
    try:
        from services.ai_scoring_service import (
            OrderRiskScorer, PaymentRoutingScorer,
            PromotionAntiFraudScorer, TrafficAntiFraudScorer,
        )
        from services.ai_scoring_auth_service import AuthRiskScorer
        from services.ai_scoring_ext_service import (
            AdminOperationScorer, AgreementRiskScorer, FinanceAnomalyScorer,
            GroupbuyQualifyScorer, MemberProfileScorer, MessageContentScorer,
            PointsRiskScorer, WithdrawRiskScorer,
        )
        _SCORER_CLASSES = {
            "order_risk": OrderRiskScorer,
            "payment_routing": PaymentRoutingScorer,
            "traffic_antifraud": TrafficAntiFraudScorer,
            "promotion_antifraud": PromotionAntiFraudScorer,
            "member_profile": MemberProfileScorer,
            "points_risk": PointsRiskScorer,
            "message_content": MessageContentScorer,
            "withdraw_risk": WithdrawRiskScorer,
            "groupbuy_qualify": GroupbuyQualifyScorer,
            "admin_operation": AdminOperationScorer,
            "agreement_risk": AgreementRiskScorer,
            "finance_anomaly": FinanceAnomalyScorer,
            "auth_risk": AuthRiskScorer,
        }
    except ImportError as exc:  # pragma: no cover - 环境异常兜底
        raise KeyError(f"评分器模块不可用: {exc}") from exc
    cls = _SCORER_CLASSES.get(scorer_id)
    if cls is None:
        raise KeyError(f"未知的评分器: {scorer_id}")
    return dict(cls.WEIGHTS)


def _require_scorer(scorer_id: str) -> None:
    if scorer_id not in SCORER_REGISTRY:
        raise KeyError(f"未知的评分器: {scorer_id}")


def _normalize_weights(weights: dict, target_sum: float) -> dict:
    """归一化权重到目标和(标准评分器=1.0, 物流路由策略=0.5), 修正舍入残差"""
    total = sum(weights.values())
    if total <= 0:
        return {k: round(v, 4) for k, v in weights.items()}
    result = {k: round(v / total * target_sum, 4) for k, v in weights.items()}
    residual = round(target_sum - sum(result.values()), 4)
    if result and abs(residual) >= 0.0001:
        key = max(result, key=result.get)
        result[key] = round(result[key] + residual, 4)
    return result


def _action_for_score(scorer_id: str, score: float) -> str | None:
    """按评分器阈值表把加权分映射为动作(路由类评分器返回 None)"""
    for threshold, action in DECISION_THRESHOLDS.get(scorer_id, []):
        if score >= threshold:
            return action
    return None


# ============================================================
# 权重缓存(评分路径零侵入: 只读 + TTL + 回退默认值)
# ============================================================

WEIGHT_CACHE_TTL = 30.0  # 秒
_weight_cache: dict[str, dict] = {}
_weight_cache_at: dict[str, float] = {}
_weight_version_cache: dict[str, str] = {}


async def load_effective_weights(scorer_id: str, defaults: dict,
                                 ttl: float = WEIGHT_CACHE_TTL) -> dict:
    """评分器读取生效权重(champion), 无档案/异常时回退默认权重

    - 进程内缓存 TTL 30s, 权重更新时主动失效(invalidate_weight_cache)
    - Redis 模式下多 worker 共享档案, 各 worker 缓存最多滞后 TTL
    - 任何异常(存储不可用/档案损坏)都不阻塞评分
    """
    now = time.monotonic()
    cached = _weight_cache.get(scorer_id)
    if cached is not None and now - _weight_cache_at.get(scorer_id, 0.0) < ttl:
        return cached
    weights, version = None, "v1"
    try:
        profile = await AiLearningRepository().get_profile(scorer_id)
        champion = (profile or {}).get("champion") or {}
        raw = champion.get("weights")
        if isinstance(raw, dict) and set(raw) == set(defaults):
            weights = {k: float(v) for k, v in raw.items()}
            version = champion.get("version", "v1")
    except Exception as exc:
        logger.warning("读取权重档案失败(scorer=%s), 回退默认权重: %s",
                       scorer_id, exc)
    if weights is None:
        weights = dict(defaults)
    _weight_cache[scorer_id] = weights
    _weight_cache_at[scorer_id] = now
    _weight_version_cache[scorer_id] = version
    return weights


def get_active_weight_version(scorer_id: str) -> str:
    """当前生效权重版本(来自缓存, 未加载过则为 v1 默认)"""
    return _weight_version_cache.get(scorer_id, "v1")


def invalidate_weight_cache(scorer_id: str | None = None) -> None:
    """失效权重缓存(权重更新/晋升/重置后调用, 立即生效)"""
    if scorer_id is None:
        _weight_cache.clear()
        _weight_cache_at.clear()
        _weight_version_cache.clear()
        return
    _weight_cache.pop(scorer_id, None)
    _weight_cache_at.pop(scorer_id, None)
    _weight_version_cache.pop(scorer_id, None)


# ============================================================
# 版本管理内部工具
# ============================================================

def _version_number(version: str) -> int:
    try:
        return int(str(version).lstrip("v").split("-")[0])
    except (ValueError, TypeError):
        return 1


def _next_version(scorer_id: str, repo: AiLearningRepository = None,
                  profile: dict = None, history: list = None) -> str:
    """计算下一版本号(取档案+历史中最大版本 +1)"""
    versions = []
    if profile:
        for role in ("champion", "challenger"):
            rec = profile.get(role) or {}
            versions.append(_version_number(rec.get("version", "v1")))
    for rec in (history or []):
        versions.append(_version_number(rec.get("version", "v1")))
    return f"v{max(versions, default=0) + 1}"


def _build_version_record(version: str, weights: dict, source: str,
                          parent: str, stats: dict = None,
                          note: str = "") -> dict:
    return {
        "version": version, "weights": dict(weights), "source": source,
        "parentVersion": parent, "stats": stats or {},
        "note": note, "createdAt": ts(),
    }


async def _load_profile(scorer_id: str, repo: AiLearningRepository) -> dict:
    """读取档案, 无冠军时初始化 v1 默认权重"""
    profile = await repo.get_profile(scorer_id) or {}
    if not profile.get("champion"):
        profile["champion"] = _build_version_record(
            "v1", default_weights(scorer_id), "default", "-")
        await repo.save_profile(scorer_id, profile)
    return profile


async def _get_config(scorer_id: str, repo: AiLearningRepository) -> dict:
    cfg = await repo.get_config(scorer_id)
    return {**DEFAULT_LEARNING_CONFIG, **(cfg or {})}


# ============================================================
# 反馈提交 + 漂移监控
# ============================================================

def _update_drift(stats: dict | None, factors: list[dict],
                  score: float) -> dict:
    """按因子分数 EMA 更新漂移统计(首条反馈建立基线)"""
    alpha = DRIFT_ALPHA
    factor_scores = {f["name"]: float(f.get("score", 0)) for f in factors}
    if not stats:
        return {
            "count": 1, "baselineScore": score, "emaScore": score,
            "baselineFactors": factor_scores, "emaFactors": factor_scores,
            "driftScore": 0.0, "driftLevel": "low", "lastFeedbackAt": ts(),
        }
    ema_factors = dict(stats.get("emaFactors", {}))
    for name, value in factor_scores.items():
        prev = ema_factors.get(name, value)
        ema_factors[name] = round(prev * (1 - alpha) + value * alpha, 2)
    ema_score = round(stats.get("emaScore", score) * (1 - alpha) + score * alpha, 2)
    # 漂移度 = 因子 EMA 相对基线的平均相对偏差
    baseline = stats.get("baselineFactors", {})
    deviations = [
        abs(ema_factors.get(name, base) - base) / max(abs(base), 10.0)
        for name, base in baseline.items()
    ]
    drift = round(sum(deviations) / len(deviations), 4) if deviations else 0.0
    level = next((name for threshold, name in DRIFT_LEVELS if drift >= threshold), "low")
    return {
        "count": stats.get("count", 0) + 1,
        "baselineScore": stats.get("baselineScore", score),
        "emaScore": ema_score,
        "baselineFactors": baseline,
        "emaFactors": ema_factors,
        "driftScore": drift, "driftLevel": level, "lastFeedbackAt": ts(),
    }


async def submit_feedback(payload: dict) -> dict:
    """提交一条决策反馈(评分结果 + 真实结果标注)

    Args:
        payload: {scorerId, factors: [{name, score, ...}],
                  scoreAtDecision, actualAction,
                  expectedAction(可选, 与 correct 二选一),
                  correct(可选 bool), note(可选), weightVersion(可选)}

    Raises:
        KeyError: 未知评分器
        ValueError: 反馈缺少真实结果标注 / 因子快照为空
    """
    scorer_id = str(payload.get("scorerId") or "")
    _require_scorer(scorer_id)
    factors = payload.get("factors") or []
    if not factors:
        raise ValueError("反馈必须包含因子快照(factors)")
    names = {f.get("name") for f in factors}
    defaults = default_weights(scorer_id)
    unknown = names - set(defaults)
    if unknown:
        raise ValueError(f"因子快照包含未知因子: {sorted(unknown)}")

    actual = str(payload.get("actualAction") or "")
    expected = payload.get("expectedAction")
    correct = payload.get("correct")
    if expected is not None:
        correct = (actual == str(expected))
    elif correct is None:
        raise ValueError("必须提供 expectedAction(期望动作)或 correct(决策是否正确)之一")

    record = {
        "scorerId": scorer_id,
        "weightVersion": payload.get("weightVersion")
                          or get_active_weight_version(scorer_id),
        "scoreAtDecision": round(float(payload.get("scoreAtDecision") or 0), 1),
        "actualAction": actual,
        "expectedAction": expected,
        "correct": bool(correct),
        "factors": [{"name": f["name"], "score": float(f.get("score") or 0),
                     "weight": float(f.get("weight") or 0),
                     "contribution": float(f.get("contribution") or 0)}
                    for f in factors],
        "note": str(payload.get("note") or ""),
        "source": str(payload.get("source") or "manual"),
        "status": "pending", "createdAt": ts(),
    }
    repo = AiLearningRepository()
    feedback_id = await repo.add_feedback(record)

    # 漂移监控(失败不阻塞反馈主流程)
    drift = None
    try:
        drift = _update_drift(await repo.get_drift(scorer_id),
                              record["factors"], record["scoreAtDecision"])
        await repo.save_drift(scorer_id, drift)
    except Exception as exc:
        logger.warning("更新漂移统计失败(scorer=%s): %s", scorer_id, exc)

    logger.info("ai_learning_feedback scorer=%s id=%s correct=%s",
                scorer_id, feedback_id, record["correct"])
    return {"success": True, "feedbackId": feedback_id,
            "scorerId": scorer_id, "correct": record["correct"],
            "drift": drift}


# ============================================================
# Hedge 在线学习引擎
# ============================================================

def _hedge_update(weights: dict, defaults: dict, feedback_list: list[dict],
                  eta: float, guardrail: float) -> dict:
    """Hedge 乘性权重更新 + 护栏约束 + 归一化"""
    w = dict(weights)
    for fb in feedback_list:
        reward = 1.0 if fb.get("correct") else -1.0
        total = sum(f.get("contribution", 0) for f in fb.get("factors", []))
        if total <= 0:
            continue
        for f in fb.get("factors", []):
            name = f.get("name")
            if name not in w:
                continue
            influence = f.get("contribution", 0) / total  # 因子影响度 0~1
            w[name] *= math.exp(eta * reward * influence)
    # 护栏: 相对默认值 [1/guardrail, guardrail] 倍
    for name, value in w.items():
        d = defaults[name]
        lo, hi = d / guardrail, d * guardrail
        w[name] = min(max(value, lo), hi)
    return _normalize_weights(w, target_sum=sum(defaults.values()))


def _evaluate(weights: dict, feedback_list: list[dict],
              scorer_id: str) -> dict:
    """回放评估: 因子快照 × 权重 → 模拟动作/奖励对齐度

    - accuracy: 有期望动作的反馈中, 模拟动作与期望一致的占比
    - rewardAlignment: mean(reward × 加权分), 通用可比较指标(路由类亦适用)
    """
    matched = labeled = 0
    reward_sum = 0.0
    for fb in feedback_list:
        score = sum(f.get("score", 0) * weights.get(f.get("name"), 0)
                    for f in fb.get("factors", []))
        reward = 1.0 if fb.get("correct") else -1.0
        reward_sum += reward * score
        expected = fb.get("expectedAction")
        if expected:
            labeled += 1
            if _action_for_score(scorer_id, score) == expected:
                matched += 1
    count = len(feedback_list)
    return {
        "samples": count,
        "accuracy": round(matched / labeled, 4) if labeled else None,
        "rewardAlignment": round(reward_sum / count, 2) if count else 0.0,
    }


def _challenger_better(champion_metrics: dict, challenger_metrics: dict) -> bool:
    """挑战者是否不劣于冠军(优先奖励对齐度, 平局看动作准确率)

    rewardAlignment 是 Hedge 直接优化的目标(正确决策分拉高/错误决策分压低),
    作为主比较指标; accuracy(动作级一致率)受阈值离散化影响, 仅作平局裁决。
    """
    champion_align = champion_metrics.get("rewardAlignment") or 0.0
    challenger_align = challenger_metrics.get("rewardAlignment") or 0.0
    if abs(challenger_align - champion_align) > 1e-9:
        return challenger_align >= champion_align
    champion_acc = champion_metrics.get("accuracy")
    challenger_acc = challenger_metrics.get("accuracy")
    if champion_acc is not None and challenger_acc is not None:
        return challenger_acc >= champion_acc
    return True


async def _sample_replay(scorer_id: str, limit: int) -> list[dict]:
    """从案例知识库采样经验回放记录(转换为 Hedge 更新格式)

    案例因子 {name: score} → [{name, score, contribution: score}],
    与路由类快照的合成约定一致(contribution = 因子分)。
    未标注(correct=None)的案例不参与回放(reward 语义不明确)。
    采样失败返回空列表(回放是增强手段, 不阻塞学习)。
    """
    try:
        from repositories.ai_knowledge_repository import (
            AiKnowledgeRepository,
        )
        cases = await AiKnowledgeRepository().list_cases(
            scorer_id, limit=limit)
        records = []
        for case in cases:
            if case.get("correct") is None:
                continue
            factors = [{"name": name, "score": val,
                        "contribution": val}
                       for name, val in (case.get("factors") or {}).items()]
            if not factors:
                continue
            records.append({
                "factors": factors,
                "correct": bool(case["correct"]),
                "actualAction": case.get("actualAction"),
                "source": "replay",
            })
        return records
    except Exception as exc:
        logger.warning("经验回放采样失败(scorer=%s): %s", scorer_id, exc)
        return []


async def run_learning_cycle(scorer_id: str) -> dict:
    """执行一轮在线学习: 待学习反馈 → Hedge 更新 → 挑战者/自动晋升

    Raises:
        KeyError: 未知评分器
        ValueError: 待学习反馈不足
    """
    _require_scorer(scorer_id)
    repo = AiLearningRepository()
    async with get_lock(f"ai_learning:{scorer_id}"):
        profile = await _load_profile(scorer_id, repo)
        config = await _get_config(scorer_id, repo)
        pending = await repo.list_feedback(scorer_id, status="pending")
        if len(pending) < config["min_feedback"]:
            raise ValueError(
                f"待学习反馈不足: {len(pending)}/{config['min_feedback']}, "
                f"可通过 PUT /api/ai-learning/config/{scorer_id} 调低 min_feedback")

        champion = profile["champion"]
        defaults = default_weights(scorer_id)

        # v7.9 经验回放: 知识库近期案例混入 Hedge 更新样本(防过拟合)
        replay_records: list[dict] = []
        if config.get("replay"):
            replay_records = await _sample_replay(
                scorer_id, int(config.get("replay_sample") or 50))

        update_sample = pending + replay_records
        new_weights = _hedge_update(
            champion["weights"], defaults, update_sample,
            config["eta"], config["guardrail"])

        # 回放评估(用全部近期反馈, 含已学习部分)
        recent = await repo.list_feedback(scorer_id, limit=200)
        champion_metrics = _evaluate(champion["weights"], recent, scorer_id)
        challenger_metrics = _evaluate(new_weights, recent, scorer_id)

        history = await repo.list_history(scorer_id, limit=100)
        new_version = _next_version(scorer_id, profile=profile, history=history)
        record = _build_version_record(
            new_version, new_weights, "learning", champion["version"],
            stats=challenger_metrics, note=f"learned from {len(pending)} feedback")

        promoted = False
        if config["auto_apply"] and _challenger_better(champion_metrics,
                                                       challenger_metrics):
            # 自动晋升: 旧冠军退役入历史, 新版本成为冠军
            await repo.add_history(scorer_id, champion)
            profile["champion"] = record
            profile["challenger"] = None
            promoted = True
        else:
            # 影子模式: 现有挑战者(若有)退役, 新版本成为挑战者
            if profile.get("challenger"):
                await repo.add_history(scorer_id, profile["challenger"])
            profile["challenger"] = record
        await repo.save_profile(scorer_id, profile)

        learned_ids = [fb["feedbackId"] for fb in pending]
        await repo.mark_feedback_learned(scorer_id, learned_ids)
        invalidate_weight_cache(scorer_id)

        # v7.9 知识库: 学习后的反馈归档为案例(经验回放缓冲, 火后不管)
        try:
            from repositories.ai_knowledge_repository import (
                AiKnowledgeRepository,
            )
            archived = await AiKnowledgeRepository().archive_feedback(
                scorer_id, pending)
            if archived:
                logger.info("ai_kb_archive scorer=%s cases=%s",
                            scorer_id, archived)
        except Exception as exc:
            logger.warning("知识案例归档失败(scorer=%s): %s", scorer_id, exc)

    delta = {k: round(new_weights[k] - champion["weights"].get(k, 0), 4)
             for k in new_weights}
    result = {
        "success": True, "scorerId": scorer_id,
        "learnedFrom": len(pending),
        "replayedFrom": len(replay_records),
        "parentVersion": champion["version"],
        "newVersion": new_version,
        "newStatus": "champion" if promoted else "challenger",
        "promoted": promoted,
        "weights": new_weights, "weightDelta": delta,
        "championMetrics": champion_metrics,
        "challengerMetrics": challenger_metrics,
        "modelVersion": MODEL_VERSION, "learnedAt": ts(),
    }
    logger.info("ai_learning_cycle scorer=%s %s→%s promoted=%s samples=%s",
                scorer_id, champion["version"], new_version,
                promoted, len(pending))
    return result


# ============================================================
# 权重管理(查看/人工覆盖/晋升/重置)
# ============================================================

async def get_weights_view(scorer_id: str) -> dict:
    """查看评分器权重档案(冠军/挑战者/默认值/配置)"""
    _require_scorer(scorer_id)
    repo = AiLearningRepository()
    profile = await _load_profile(scorer_id, repo)
    config = await _get_config(scorer_id, repo)
    meta = SCORER_REGISTRY[scorer_id]
    return {
        "success": True, "scorerId": scorer_id,
        "label": meta["label"], "module": meta["module"],
        "defaults": default_weights(scorer_id),
        "champion": profile["champion"],
        "challenger": profile.get("challenger"),
        "config": config,
        "activeVersion": get_active_weight_version(scorer_id),
        "decisionThresholds": DECISION_THRESHOLDS.get(scorer_id),
    }


async def manual_override_weights(scorer_id: str, weights: dict,
                                  reason: str = "") -> dict:
    """人工覆盖权重(立即生效为新冠军, 旧冠军入历史)

    Raises:
        KeyError: 未知评分器
        ValueError: 因子集合不匹配/权重非法/超出护栏
    """
    _require_scorer(scorer_id)
    if not isinstance(weights, dict) or not weights:
        raise ValueError("权重不能为空")
    defaults = default_weights(scorer_id)
    if set(weights) != set(defaults):
        missing = set(defaults) - set(weights)
        extra = set(weights) - set(defaults)
        raise ValueError(
            f"因子集合不匹配(缺失: {sorted(missing) or '无'}, "
            f"多余: {sorted(extra) or '无'})")
    try:
        raw = {k: float(v) for k, v in weights.items()}
    except (TypeError, ValueError) as exc:
        raise ValueError(f"权重必须为数值: {exc}") from exc
    if any(v <= 0 for v in raw.values()):
        raise ValueError("权重必须全部大于 0")
    target_sum = sum(defaults.values())
    if abs(sum(raw.values()) - target_sum) > target_sum * 0.10:
        raise ValueError(
            f"权重和 {round(sum(raw.values()), 4)} 偏离目标 {target_sum} 超过 10%")

    repo = AiLearningRepository()
    async with get_lock(f"ai_learning:{scorer_id}"):
        profile = await _load_profile(scorer_id, repo)
        config = await _get_config(scorer_id, repo)
        guardrail = config["guardrail"]
        violations = []
        for name, value in raw.items():
            d = defaults[name]
            if not (d / guardrail <= value <= d * guardrail):
                violations.append(
                    f"{name}: {value}(允许区间 [{round(d/guardrail, 4)}, "
                    f"{round(d*guardrail, 4)}])")
        if violations:
            raise ValueError("权重超出护栏(相对默认值 "
                             f"1/{guardrail}~{guardrail} 倍): " + "; ".join(violations))

        normalized = _normalize_weights(raw, target_sum)
        champion = profile["champion"]
        history = await repo.list_history(scorer_id, limit=100)
        new_version = _next_version(scorer_id, profile=profile, history=history)
        record = _build_version_record(
            new_version, normalized, "manual", champion["version"],
            note=reason or "人工覆盖")
        await repo.add_history(scorer_id, champion)
        if profile.get("challenger"):
            await repo.add_history(scorer_id, profile["challenger"])
        profile["champion"] = record
        profile["challenger"] = None
        await repo.save_profile(scorer_id, profile)
        invalidate_weight_cache(scorer_id)

    logger.info("ai_learning_manual_override scorer=%s %s→%s",
                scorer_id, champion["version"], new_version)
    return {"success": True, "scorerId": scorer_id,
            "previousVersion": champion["version"],
            "newVersion": new_version, "weights": normalized,
            "reason": reason or "人工覆盖", "appliedAt": record["createdAt"]}


async def promote_challenger(scorer_id: str) -> dict:
    """晋升挑战者为冠军(人工决策通道)

    Raises:
        KeyError: 未知评分器
        ValueError: 无可晋升的挑战者
    """
    _require_scorer(scorer_id)
    repo = AiLearningRepository()
    async with get_lock(f"ai_learning:{scorer_id}"):
        profile = await _load_profile(scorer_id, repo)
        challenger = profile.get("challenger")
        if not challenger:
            raise ValueError(f"评分器 {scorer_id} 当前没有挑战者版本可晋升")
        champion = profile["champion"]
        await repo.add_history(scorer_id, champion)
        profile["champion"] = challenger
        profile["challenger"] = None
        await repo.save_profile(scorer_id, profile)
        invalidate_weight_cache(scorer_id)

    logger.info("ai_learning_promote scorer=%s %s→%s",
                scorer_id, champion["version"], challenger["version"])
    return {"success": True, "scorerId": scorer_id,
            "previousVersion": champion["version"],
            "promotedVersion": challenger["version"],
            "weights": challenger["weights"], "promotedAt": ts()}


async def discard_challenger(scorer_id: str, reason: str | None = None) -> dict:
    """丢弃挑战者版本(审批拒绝通道, v7.9)

    挑战者退役进历史(note 标记 rejected), 影子位清空; 冠军不受影响。
    与 promote 同为读-改-写操作, 统一走 get_lock。

    Raises:
        KeyError: 未知评分器
        ValueError: 无可丢弃的挑战者
    """
    _require_scorer(scorer_id)
    repo = AiLearningRepository()
    async with get_lock(f"ai_learning:{scorer_id}"):
        profile = await _load_profile(scorer_id, repo)
        challenger = profile.get("challenger")
        if not challenger:
            raise ValueError(f"评分器 {scorer_id} 当前没有挑战者版本可拒绝")
        challenger = dict(challenger)
        challenger["note"] = (f"rejected: {reason}" if reason
                              else "rejected: 审批拒绝")
        await repo.add_history(scorer_id, challenger)
        profile["challenger"] = None
        await repo.save_profile(scorer_id, profile)
        # 拒绝不影响冠军生效权重, 无需失效权重缓存

    logger.info("ai_learning_discard scorer=%s %s",
                scorer_id, challenger["version"])
    return {"success": True, "scorerId": scorer_id,
            "discardedVersion": challenger["version"],
            "reason": reason, "discardedAt": ts()}


async def reset_weights(scorer_id: str) -> dict:
    """重置为默认权重(新冠军版本, 挑战者清除, 历史保留支持审计)"""
    _require_scorer(scorer_id)
    repo = AiLearningRepository()
    async with get_lock(f"ai_learning:{scorer_id}"):
        profile = await _load_profile(scorer_id, repo)
        champion = profile["champion"]
        history = await repo.list_history(scorer_id, limit=100)
        new_version = _next_version(scorer_id, profile=profile, history=history)
        record = _build_version_record(
            new_version, default_weights(scorer_id), "reset",
            champion["version"], note="重置为默认权重")
        await repo.add_history(scorer_id, champion)
        if profile.get("challenger"):
            await repo.add_history(scorer_id, profile["challenger"])
        profile["champion"] = record
        profile["challenger"] = None
        await repo.save_profile(scorer_id, profile)
        invalidate_weight_cache(scorer_id)

    logger.info("ai_learning_reset scorer=%s %s→%s(defaults)",
                scorer_id, champion["version"], new_version)
    return {"success": True, "scorerId": scorer_id,
            "previousVersion": champion["version"],
            "newVersion": new_version,
            "weights": record["weights"], "resetAt": record["createdAt"]}


async def get_history(scorer_id: str, limit: int = 50) -> dict:
    """查看版本历史(含在役冠军/挑战者)"""
    _require_scorer(scorer_id)
    repo = AiLearningRepository()
    profile = await _load_profile(scorer_id, repo)
    history = await repo.list_history(scorer_id, limit=limit)
    return {
        "success": True, "scorerId": scorer_id,
        "champion": profile["champion"]["version"],
        "challenger": (profile.get("challenger") or {}).get("version"),
        "historyCount": len(history), "history": history,
    }


async def get_drift_view(scorer_id: str) -> dict:
    """查看漂移统计"""
    _require_scorer(scorer_id)
    repo = AiLearningRepository()
    stats = await repo.get_drift(scorer_id)
    return {
        "success": True, "scorerId": scorer_id,
        "drift": stats or {"count": 0, "driftScore": 0.0,
                           "driftLevel": "low", "message": "暂无反馈, 未建立基线"},
    }


async def update_learning_config(scorer_id: str, updates: dict) -> dict:
    """更新学习配置(eta/min_feedback/auto_apply/guardrail/replay)"""
    _require_scorer(scorer_id)
    allowed = {"eta", "min_feedback", "auto_apply", "guardrail",
               "replay", "replay_sample"}
    invalid = set(updates) - allowed
    if invalid:
        raise ValueError(f"不支持的配置项: {sorted(invalid)}")
    repo = AiLearningRepository()
    config = await _get_config(scorer_id, repo)
    config.update(updates)
    if not (0 < config["eta"] <= 5):
        raise ValueError("eta 必须在 (0, 5] 区间")
    if not (1 <= config["min_feedback"] <= 1000):
        raise ValueError("min_feedback 必须在 [1, 1000] 区间")
    if not (1.1 <= config["guardrail"] <= 10):
        raise ValueError("guardrail 必须在 [1.1, 10] 区间")
    if not isinstance(config["replay"], bool):
        raise ValueError("replay 必须为布尔值")
    if not (1 <= config["replay_sample"] <= 1000):
        raise ValueError("replay_sample 必须在 [1, 1000] 区间")
    await repo.save_config(scorer_id, config)
    logger.info("ai_learning_config_updated scorer=%s updates=%s",
                scorer_id, updates)
    return {"success": True, "scorerId": scorer_id, "config": config}


async def overview() -> dict:
    """全部评分器自学习状态总览"""
    repo = AiLearningRepository()
    scorers = []
    for scorer_id, meta in SCORER_REGISTRY.items():
        profile = await repo.get_profile(scorer_id) or {}
        champion = profile.get("champion") or {}
        challenger = profile.get("challenger") or {}
        config = {**DEFAULT_LEARNING_CONFIG, **(await repo.get_config(scorer_id) or {})}
        drift = await repo.get_drift(scorer_id) or {}
        pending = await repo.count_feedback(scorer_id, status="pending")
        feedback = await repo.list_feedback(scorer_id, limit=0)
        # 近 24h 自动反馈数(v7.6 自动闭环健康度指标)
        auto_24h = 0
        cutoff = time.time() - 24 * 3600
        for fb in feedback:
            if fb.get("source") == "auto" and fb.get("createdAt", "") >= \
                    time.strftime("%Y-%m-%dT%H:%M", time.gmtime(cutoff)):
                auto_24h += 1
        scorers.append({
            "scorerId": scorer_id, "label": meta["label"],
            "module": meta["module"], "batch": meta["batch"],
            "championVersion": champion.get("version", "v1"),
            "championSource": champion.get("source", "default"),
            "challengerVersion": challenger.get("version"),
            "pendingFeedback": pending,
            "totalFeedback": await repo.count_feedback(scorer_id),
            "autoFeedback24h": auto_24h,
            "driftScore": drift.get("driftScore", 0.0),
            "driftLevel": drift.get("driftLevel", "low"),
            "autoApply": config["auto_apply"],
            "learnableThreshold": DECISION_THRESHOLDS.get(scorer_id) is not None,
        })
    scheduler_stats = await repo.get_scheduler_stats() or {}
    return {"success": True, "scorerCount": len(scorers),
            "scorers": scorers,
            "scheduler": {
                "runs": scheduler_stats.get("runs", 0),
                "lastRunAt": scheduler_stats.get("lastRunAt"),
                "lastLearnedScorers": scheduler_stats.get("lastLearnedScorers", 0),
            },
            "generatedAt": ts()}


async def learning_report(scorer_id: str) -> dict:
    """学习效果报表: 按权重版本聚合反馈正确率 + 版本演进曲线

    Returns:
        {versions: [{version, feedbackCount, correctRate}], curve: [...],
         recentTrend: {last10CorrectRate, previous10CorrectRate}}
    """
    _require_scorer(scorer_id)
    repo = AiLearningRepository()
    feedback = await repo.list_feedback(scorer_id, limit=0)

    # 按权重版本聚合(正确率随版本演进是核心观测指标)
    by_version: dict[str, dict] = {}
    for fb in feedback:
        version = str(fb.get("weightVersion") or "v1")
        bucket = by_version.setdefault(
            version, {"version": version, "feedbackCount": 0, "correct": 0})
        bucket["feedbackCount"] += 1
        if fb.get("correct"):
            bucket["correct"] += 1
    versions = [
        {"version": v["version"], "feedbackCount": v["feedbackCount"],
         "correctRate": round(v["correct"] / v["feedbackCount"], 4)
         if v["feedbackCount"] else None,
         "incorrectCount": v["feedbackCount"] - v["correct"]}
        for v in by_version.values()
    ]
    versions.sort(key=lambda x: _version_number(x["version"]))

    # 近期趋势(最近 10 条 vs 之前 10 条)
    ordered = list(feedback)
    last10 = ordered[-10:]
    prev10 = ordered[-20:-10]
    rate = lambda chunk: (round(sum(1 for f in chunk if f.get("correct"))
                                 / len(chunk), 4) if chunk else None)
    recent_trend = {
        "last10CorrectRate": rate(last10),
        "previous10CorrectRate": rate(prev10),
        "last10Size": len(last10), "previous10Size": len(prev10),
    }

    # 版本演进曲线(历史 + 在役, 各版本评估指标)
    profile = await repo.get_profile(scorer_id) or {}
    curve = []
    for rec in await repo.list_history(scorer_id, limit=50):
        curve.append({
            "version": rec.get("version"), "source": rec.get("source"),
            "parentVersion": rec.get("parentVersion"),
            "createdAt": rec.get("createdAt"),
            "stats": rec.get("stats") or {},
            "role": "retired",
        })
    for role in ("challenger", "champion"):
        rec = profile.get(role)
        if rec:
            curve.append({
                "version": rec.get("version"), "source": rec.get("source"),
                "parentVersion": rec.get("parentVersion"),
                "createdAt": rec.get("createdAt"),
                "stats": rec.get("stats") or {},
                "role": role,
            })
    curve.sort(key=lambda x: _version_number(x["version"]))

    # 自动 vs 手动反馈占比
    auto_count = sum(1 for fb in feedback if fb.get("source") == "auto")
    drift = await repo.get_drift(scorer_id) or {}
    return {
        "success": True, "scorerId": scorer_id,
        "label": SCORER_REGISTRY[scorer_id]["label"],
        "totalFeedback": len(feedback),
        "autoFeedback": auto_count,
        "manualFeedback": len(feedback) - auto_count,
        "versions": versions,
        "recentTrend": recent_trend,
        "curve": curve,
        "drift": {"driftScore": drift.get("driftScore", 0.0),
                  "driftLevel": drift.get("driftLevel", "low")},
        "generatedAt": ts(),
    }
