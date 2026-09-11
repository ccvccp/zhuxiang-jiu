"""68号 P0·信值·臻选——信值五维雷达图服务

依据:《信值·臻选大模型》文档"信值五维雷达图算法与权重配置表"
(全链路增强版)+《68号 创新规划方案》§二/§三

核心命题:**编排式只读聚合**——五维数据源全部来自既有模块,
本服务零新建计算源(防止双源真相):
    诚信度(30%) ← 47号风险画像(trustLevel=1−riskEMA)+ 订单履约
    互助值(25%) ← 67号叫帮信值档案(公益/有偿双轨+被评价分)
    专业度(20%) ← 44号评分矩阵活跃度(会员评分行为质量)
    活跃度(15%) ← 登录/订单流水频次(近30天)
    成长力(10%) ← 信值变化斜率(快照差分, 首次冷启动 50)

算法三步法(文档口径, 全确定性 LLM 禁入):
    ① 分段 Sigmoid 归一化: 100/(1+exp(−k×(x−x0)))
       (x0=业务基准值, k=敏感度系数——诚信度 k 高/成长力 k 低)
    ② 动态权重引擎(非固定值, 配置化):
       新用户前30天: 活跃度 15%→25%, 诚信度 30%→20%(冷启动保护)
    ③ 修正层三叠加:
       硬熔断: 任一维度<40 → 总分封顶 59(C 级上限)
       软奖励: 连续6月无违规 且 互助TOP10% → +5(≤100)
       负面衰减: 半衰期 90 天(避免"一次犯错终身受限")

可解释性内建(文档铁律):
    每维附"近期影响因素 TOP3"(如"诚信度↓: 因风险信号命中");
    解释文本与分数构成强制一致(测试断言口径)

红线(宪法域):
    - 权重/阈值全部配置化(redis 热加载, 不发版)——但调整走
      46号审批留痕(P0 硬编码默认, 后台调参为 P5 灰度项)
    - 五维聚合只读消费既有模块, 永不回写数据源
    - 计算全留痕(原始值/中间值/最终分/解释)——审计友好
"""

import logging
from datetime import datetime, UTC

from repositories.xinzhi_repository import (
    XinzhiRepository, DIMENSIONS, DIMENSION_LABELS,
    DIM_INTEGRITY, DIM_MUTUAL, DIM_EXPERT, DIM_ACTIVITY,
    DIM_GROWTH,
)

logger = logging.getLogger(__name__)


# ============================================================
# P0 常量(文档《MVP 权重配置表》口径)
# ============================================================

# 默认权重(后台可调范围——文档):
#   诚信度 25-35 / 互助 20-30 / 专业 15-25 / 活跃 10-20 / 成长 5-15
DEFAULT_WEIGHTS = {
    DIM_INTEGRITY: 0.30, DIM_MUTUAL: 0.25, DIM_EXPERT: 0.20,
    DIM_ACTIVITY: 0.15, DIM_GROWTH: 0.10,
}

# 新用户冷启动(前30天: 活跃↑诚信↓——降低冷启动门槛)
COLD_START_DAYS = 30
COLD_START_WEIGHTS = {
    DIM_INTEGRITY: 0.20, DIM_MUTUAL: 0.25, DIM_EXPERT: 0.20,
    DIM_ACTIVITY: 0.25, DIM_GROWTH: 0.10,
}

# 分段 Sigmoid 参数(x0=基准值, k=敏感度; 诚信k高/成长k低——文档口径)
SIGMOID_PARAMS = {
    DIM_INTEGRITY: {"x0": 0.70, "k": 8.0},
    DIM_MUTUAL: {"x0": 20.0, "k": 0.08},
    DIM_EXPERT: {"x0": 50.0, "k": 0.05},
    DIM_ACTIVITY: {"x0": 3.0, "k": 0.8},
    DIM_GROWTH: {"x0": 0.0, "k": 0.04},
}

# 修正层(文档公式)
HARD_CAP_LINE = 59          # 熔断封顶(任一维<40)
HARD_CAP_MIN_DIM = 40.0
BONUS_POINTS = 5            # 软奖励(连续6月无违规+互助TOP10%)
BONUS_MAX_SCORE = 100
DECAY_HALF_LIFE_DAYS = 90  # 负面半衰期
DECAY_LAMBDA = 0.693       # ln(2)

# 等级线(文档 S/A/B/C/D)
GRADE_LINES = ((90, "S"), (80, "A"), (70, "B"), (60, "C"))
GRADE_D = "D"

# 活跃度窗口(近30天订单+登录)
ACTIVITY_WINDOW_DAYS = 30


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _days_since(iso: str) -> float:
    """距今天数(空/非法 → 大数——非近期)"""
    if not (iso or "").strip():
        return 999.0
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - dt).total_seconds()
                   / 86400.0)
    except (TypeError, ValueError):
        return 999.0


def sigmoid_normalize(dim: str, x: float) -> float:
    """分段 Sigmoid 归一化: 100/(1+exp(−k×(x−x0)))

    区分"及格线"与"卓越线"(文档三步法①)——确定性, 无 LLM。
    """
    p = SIGMOID_PARAMS[dim]
    import math
    z = p["k"] * (float(x) - p["x0"])
    # 防 exp 溢出(|z|>30 时直接走饱和段)
    if z > 30:
        return 100.0
    if z < -30:
        return 0.0
    return round(100.0 / (1.0 + math.exp(-z)), 1)


def grade_of(score: float) -> str:
    """信值等级(S/A/B/C/D——文档五级)"""
    for line, name in GRADE_LINES:
        if score >= line:
            return name
    return GRADE_D


class XinzhiRadarService:
    """68号 P0·信值五维雷达图(编排式只读聚合)"""

    def __init__(self, repo: XinzhiRepository = None):
        self.repo = repo if repo is not None else XinzhiRepository()

    # ============================================================
    # 五维原始值采集(只读消费既有模块——零回写)
    # ============================================================

    async def _collect_raw(self, member_id: int) -> dict:
        """五维原始值 + 归因线索(全部来自既有模块 API)

        Returns:
            {integrity, mutual, expert, activity, growth_raw,
             daysSinceRegister, cleanMonths, mutualRankPct,
             daysSinceViolation, historicalPenalty, sources}
        """
        # ---- 会员档案(注册时长/登录) ----
        from repositories.member_repository import (
            MemberRepository)
        member = await MemberRepository().get_by_id(member_id)
        if member is None:
            raise KeyError(f"会员不存在(memberId={member_id})")
        days_reg = _days_since(member.get("created_at", ""))

        # ---- 诚信度: 47号风险画像 trustLevel(1−riskEMA) ----
        integrity_raw, integrity_src = 0.0, []
        tier = ""
        try:
            from repositories.trust_risk_repository import (
                TrustRisk47Repository)
            # 47号画像按 trustId 组织; P0 以 trustId==memberId
            # 口径匹配(47号 record_risk_event 传 member 主键场景)
            profiles = await TrustRisk47Repository(
            ).list_profiles(limit=500)
            matched = next(
                (p for p in (profiles or [])
                 if int(p.get("trustId") or 0) == member_id),
                None)
            if matched:
                risk = float(matched.get("riskEMA") or 0)
                from services.trust_risk_profile_service import (
                    trust_level_of, tier_of)
                integrity_raw = trust_level_of(risk) * 100
                tier = tier_of(trust_level_of(risk))
                hits = matched.get("hitCounts") or {}
                if hits:
                    integrity_src.append(
                        f"风险画像信号命中: "
                        f"{sum(hits.values())} 次")
                if tier == "restricted":
                    integrity_src.append(
                        "信任分层: restricted")
            else:
                integrity_raw = 70.0
                integrity_src.append("无风险档案(新用户基准)")
        except Exception as exc:  # noqa: BLE001 依赖不可达降级
            logger.warning("xinzhi_p0_47_fail m=%s: %s",
                           member_id, exc)
            integrity_raw = 70.0
            integrity_src.append("风险画像不可达(降级基准)")

        # ---- 互助值: 67号叫帮信值档案(双轨累计+被评价分) ----
        mutual_raw, mutual_src = 0.0, []
        try:
            from services.help_service import HelpService
            tp = await HelpService().trust_profile(member_id)
            total = float(tp.get("totalTrust") or 0)
            rating = tp.get("ratingAvg") or 0
            # 互助原始值 = 双轨累计 + 评价均分×2(帮助质量权重)
            mutual_raw = total + float(rating) * 2
            if total:
                mutual_src.append(
                    f"互助信值累计 {total}(公益 "
                    f"{tp.get('publicTrust', 0)}/有偿 "
                    f"{tp.get('paidTrust', 0)})")
            if rating:
                mutual_src.append(
                    f"被评价均分 {rating}(共 "
                    f"{tp.get('ratingCount', 0)} 条)")
        except Exception as exc:  # noqa: BLE001
            logger.warning("xinzhi_p0_67_fail m=%s: %s",
                           member_id, exc)
            mutual_src.append("互助档案不可达(降级 0)")

        # ---- 专业度: 44号评分回流(评分行为活跃质量) ----
        expert_raw, expert_src = 0.0, []
        try:
            from repositories.ai_learning_repository import (
                AiLearningRepository)
            feedbacks = await AiLearningRepository(
            ).list_feedback("member_rating_gate", limit=1000)
            total_fb = len(feedbacks)
            correct = sum(
                1 for f in feedbacks if f.get("correct"))
            # 评分行为量+正确率(行为质量)→专业度原始值
            expert_raw = min(100.0, total_fb * 0.5
                             + (correct / total_fb * 50
                                if total_fb else 0))
            if total_fb:
                expert_src.append(
                    f"评分回流 {total_fb} 条(正确 "
                    f"{correct})")
        except Exception as exc:  # noqa: BLE001
            logger.warning("xinzhi_p0_44_fail m=%s: %s",
                           member_id, exc)
            expert_src.append("评分矩阵不可达(降级 0)")

        # ---- 活跃度: 订单流水(近30天数) + 登录 ----
        activity_raw, activity_src = 0.0, []
        try:
            from repositories.order_repository import (
                OrderRepository)
            orders = await OrderRepository().get_by_member(
                member_id)
            recent = [o for o in (orders or [])
                      if _days_since(o.get("created_at", ""))
                      <= ACTIVITY_WINDOW_DAYS]
            # 原始值 = 近30天订单数(登录叠加在后)
            activity_raw = float(len(recent))
            login_days = _days_since(
                member.get("last_login_at", ""))
            if activity_raw:
                activity_src.append(
                    f"近{ACTIVITY_WINDOW_DAYS}天下单 "
                    f"{len(recent)} 笔")
            if login_days <= 7:
                activity_src.append("近7天有登录")
        except Exception as exc:  # noqa: BLE001
            logger.warning("xinzhi_p0_order_fail m=%s: %s",
                           member_id, exc)

        # ---- 成长力: 快照差分斜率(首次冷启动 0) ----
        growth_raw, growth_src = 0.0, []
        prev = await self.repo.latest_snapshot(member_id)
        if prev is not None:
            prev_score = float(prev.get("totalScore") or 0)
            gap_days = _days_since(
                prev.get("computedAt", "")) or 1.0
            growth_raw = round(
                (prev_score - float(
                    prev.get("prevScore") or 0)) / gap_days, 2
            ) if prev.get("prevScore") else 0.0
            growth_src.append("快照差分斜率")
        else:
            growth_raw = 0.0
            growth_src.append("首次计算(冷启动)")

        return {
            DIM_INTEGRITY: integrity_raw,
            DIM_MUTUAL: mutual_raw,
            DIM_EXPERT: expert_raw,
            DIM_ACTIVITY: activity_raw,
            "growth_raw": growth_raw,
            "daysSinceRegister": days_reg,
            "cleanMonths": 6 if integrity_raw >= 70 else 0,
            "mutualRankPct": 0.5,  # P0 观测口径(无排名源, 默认中位)
            "daysSinceViolation": (999.0
                                  if integrity_raw >= 70 else 10.0),
            "historicalPenalty": (0.0 if integrity_raw >= 70
                                  else 5.0),
            "tier": tier,
            "sources": {
                "integrity": integrity_src,
                "mutual": mutual_src,
                "expert": expert_src,
                "activity": activity_src,
                "growth": growth_src,
            },
        }

    # ============================================================
    # 动态权重(新用户冷启动——文档口径)
    # ============================================================

    def _dynamic_weights(self, raw: dict) -> dict:
        days = float(raw.get("daysSinceRegister") or 999)
        if days < COLD_START_DAYS:
            return dict(COLD_START_WEIGHTS)
        return dict(DEFAULT_WEIGHTS)

    # ============================================================
    # 修正层(熔断+奖励+衰减——文档公式)
    # ============================================================

    def _apply_corrections(self, base: float, dims: dict,
                           raw: dict) -> tuple:
        """Returns: (最终分, 熔断标记, 奖励标记)"""
        broken = any(dims[d] < HARD_CAP_MIN_DIM
                     for d in DIMENSIONS)
        if broken:
            return min(base, float(HARD_CAP_LINE)), True, False
        bonus = False
        if int(raw.get("cleanMonths") or 0) >= 6 \
                and float(raw.get("mutualRankPct") or 1) <= 0.1:
            base = min(BONUS_MAX_SCORE, base + BONUS_POINTS)
            bonus = True
        # 负面半衰期 90 天
        days_v = float(raw.get("daysSinceViolation") or 999)
        penalty = float(raw.get("historicalPenalty") or 0)
        decay = penalty * pow(
            0.5, days_v / DECAY_HALF_LIFE_DAYS)
        base -= decay
        return round(max(0.0, min(100.0, base)), 1), False, bonus

    # ============================================================
    # 可解释性(文档铁律: 解释与分数构成强制一致)
    # ============================================================

    def _explain(self, dims: dict, weights: dict,
                 total: float) -> str:
        top = max(DIMENSIONS, key=lambda d: dims[d])
        bottom = min(DIMENSIONS, key=lambda d: dims[d])
        return (f"您的信值总分为{total}分"
                f"({grade_of(total)}级)。优势维度: "
                f"{DIMENSION_LABELS[top]}"
                f"({dims[top]}分, 权重"
                f"{round(weights[top] * 100)}%); "
                f"待提升维度: {DIMENSION_LABELS[bottom]}"
                f"({dims[bottom]}分)。")

    # ============================================================
    # 主流程: 计算五维雷达(快照入库+审计留痕)
    # ============================================================

    async def compute_radar(self, member_id: int) -> dict:
        """计算会员信值五维雷达(编排聚合→Sigmoid→权重→修正)

        Raises:
            KeyError: 会员不存在
        """
        raw = await self._collect_raw(member_id)
        weights = self._dynamic_weights(raw)
        dims = {d: sigmoid_normalize(d, raw[d])
                for d in DIMENSIONS if d != DIM_GROWTH}
        # 成长力: 原始值已是分数口径(斜率), 直接送 Sigmoid
        dims[DIM_GROWTH] = sigmoid_normalize(
            DIM_GROWTH, raw.get("growth_raw") or 0)
        base = sum(dims[d] * weights[d] for d in DIMENSIONS)
        total, broken, bonus = self._apply_corrections(
            base, dims, raw)
        prev = await self.repo.latest_snapshot(member_id)
        snapshot_id = await self.repo.next_id("snapshot")
        factors = {d: (raw.get("sources") or {}).get(d, [])[:3]
                   for d in DIMENSIONS}
        snapshot = {
            "snapshotId": snapshot_id,
            "memberId": member_id,
            **{d: dims[d] for d in DIMENSIONS},
            "totalScore": total,
            "grade": grade_of(total),
            "weights": weights,
            "explanation": self._explain(dims, weights, total),
            "recentFactors": factors,
            "circuitBroken": broken,
            "coldStart": float(raw.get("daysSinceRegister")
                               or 999) < COLD_START_DAYS,
            "bonusApplied": bonus,
            "tier": raw.get("tier", ""),
            "prevScore": float(
                (prev or {}).get("totalScore") or 0),
            "rawInputs": {d: raw[d] for d in DIMENSIONS
                          if d in raw},
            "sources": raw.get("sources") or {},
            "computedAt": _now_iso(),
        }
        await self.repo.save_snapshot(snapshot)
        logger.info("xinzhi_p0_radar m=%s total=%s(%s) "
                    "sid=%s", member_id, total,
                    snapshot["grade"], snapshot_id)
        return snapshot

    # ============================================================
    # 观测面
    # ============================================================

    async def get_radar(self, member_id: int) -> dict:
        """最新雷达(无快照则即时计算)"""
        snap = await self.repo.latest_snapshot(member_id)
        if snap is None:
            snap = await self.compute_radar(member_id)
        return self._view(snap)

    async def get_history(self, member_id: int,
                          limit: int = 90) -> list[dict]:
        """90 日历史曲线(快照时序)"""
        snaps = await self.repo.list_snapshots(
            member_id=member_id, limit=limit)
        return [{
            "snapshotId": s.get("snapshotId"),
            "totalScore": s.get("totalScore"),
            "grade": s.get("grade"),
            "computedAt": s.get("computedAt"),
        } for s in snaps]

    @staticmethod
    def _view(snap: dict) -> dict:
        """雷达视图(五维+总分+等级+每维影响因素TOP3)"""
        dims = {d: snap.get(d, 0) for d in DIMENSIONS}
        factors = snap.get("recentFactors") or {}
        return {
            "memberId": snap.get("memberId"),
            "dimensions": [
                {"key": d, "label": DIMENSION_LABELS[d],
                 "score": dims[d],
                 "weight": snap.get("weights", {}).get(d),
                 "factors": factors.get(d, [])}
                for d in DIMENSIONS],
            "totalScore": snap.get("totalScore"),
            "grade": snap.get("grade"),
            "explanation": snap.get("explanation"),
            "circuitBroken": bool(snap.get("circuitBroken")),
            "coldStart": bool(snap.get("coldStart")),
            "tier": snap.get("tier", ""),
            "computedAt": snap.get("computedAt"),
        }
