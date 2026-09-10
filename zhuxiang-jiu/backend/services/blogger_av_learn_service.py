"""40号 P6a·多模态自主学习引擎服务(设计文档《40号 P6 升级方案》§3)

视听信号扩展(完播/分享/收藏/弹幕情感) → 情感对齐复合奖励(γ≥α 铁律)
→ 44号 Hedge 学习闭环(blogger_work_gate) → 跨平台调性映射 + 封禁元素库

架构口径:
    - 视听信号复用 blogger_signals 表(新增 kind, 不建新表); 原始流可
      重复采样, 学习轮按 consumed 幂等(信号与反馈分离——P5a 先例)
    - 双轨采样: report(单条上传轨——平台回执落地) + collect(批量滚动轨
      ——从 follow.avMetrics 重采样), 三采样点 1h/6h/24h 由多次上报构成
    - 情感对齐奖励双轨: 完播样本 ≥ AV_COLD_START_SAMPLES 走情感公式;
      样本不足回退 P5a 信值对齐公式(平滑过渡不破坏既有闭环)
    - "视听语义理解"全确定性: 弹幕/评论情感=词表密度, 完播/分享/收藏=
      行为率聚合, 调性迁移=静态映射表——LLM 禁入判定链铁律不因模态松动

红线(宪法域, 永不可校准):
    - γ 情感共鸣权重 ≥ α 转化权重——参数写入校验直接拒绝(与 P5a β 拒改
      同构, 双保险: β 保合规底线, γ 保情感温度)
    - β 合规权重继承 P5a 三级防护(函数级拒传参+服务级拒写入+端点级只读)
    - 封禁视听元素: 某平台封禁的 BGM/转场实时同步全平台约束库
"""

import logging
from datetime import datetime, UTC

from repositories.blogger_repository import (
    BloggerRepository, PLATFORMS, FOLLOW_STATUS_PUBLISHED,
)
from services.blogger_service import BloggerService
from services.blogger_auto_learn_service import (
    SIGNAL_CHANNEL_POSITIVE, SIGNAL_CHANNEL_NEGATIVE,
    SIGNAL_KIND_CLICKS, SIGNAL_KIND_REGISTERED, SIGNAL_KIND_ORDERED,
    SIGNAL_KIND_COMPLIANCE_SCORE, SIGNAL_KIND_REPORTS,
    SIGNAL_KIND_RATE_LIMIT, SIGNAL_KIND_COMPLAINTS,
    NEGATIVE_SENTIMENT_WORDS, negative_density,
    compute_conversion_efficiency, compute_risk_loss,
    compute_value_aligned_reward, REWARD_BETA,
)

logger = logging.getLogger(__name__)


# ============================================================
# P6a 平台扩展(设计文档 §1: 六平台口径, 最小侵入——不动既有四平台)
# ============================================================

PLATFORM_BILIBILI = "bilibili"          # B站·中视频深度解说
PLATFORM_XIAOYUZHOU = "xiaoyuzhou"     # 小宇宙·播客纯音频
AV_PLATFORMS = PLATFORMS + (PLATFORM_BILIBILI, PLATFORM_XIAOYUZHOU)

# ============================================================
# 跨平台调性迁移表(设计文档 §3.3: 确定性映射, 非 LLM 推理)
# rhythm 节奏 / duration 秒区间 / density 话术密度 / form 形式
# ============================================================

PLATFORM_TONE = {
    "douyin": {"rhythm": "fast", "duration": [15, 45],
               "density": "high", "form": "short_video"},
    "wechat_channels": {"rhythm": "brisk", "duration": [30, 90],
                       "density": "medium", "form": "short_video"},
    "xiaohongshu": {"rhythm": "relaxed", "duration": [45, 120],
                    "density": "medium", "form": "short_video"},
    "weibo": {"rhythm": "brisk", "duration": [15, 60],
              "density": "medium", "form": "short_video"},
    "bilibili": {"rhythm": "deep", "duration": [180, 480],
                 "density": "medium", "form": "mid_video"},
    "xiaoyuzhou": {"rhythm": "immersive", "duration": [600, 1800],
                   "density": "low", "form": "podcast"},
}

# 封禁视听元素种类(BGM/转场; 全平台共享 + 平台增量)
ELEMENT_KINDS = ("bgm", "transition")

# ============================================================
# P6a 视听信号常量(设计文档 §3.1——复用 blogger_signals 表)
# ============================================================

# 正向: 行为率视听信号
AV_SIGNAL_KIND_COMPLETION = "completion_rate"   # 完播率
AV_SIGNAL_KIND_SHARE = "share_rate"             # 分享率
AV_SIGNAL_KIND_FAVORITE = "favorite_rate"      # 收藏率
AV_SIGNAL_KIND_COMMENT_POSITIVE = "comment_positive"  # 评论正面密度
# 负向: 弹幕负面密度
AV_SIGNAL_KIND_DANMAKU_NEGATIVE = "danmaku_negative"

# 弹幕负面词表(确定性——P5a NEGATIVE_SENTIMENT_WORDS + 弹幕场景增量)
DANMAKU_NEGATIVE_WORDS = NEGATIVE_SENTIMENT_WORDS + (
    "无聊", "划走", "尬", "广告狗", "跳过", "关掉了", "不喜欢",
)
# 弹幕负面密度阈值(与 P5a 评论口径一致)
DANMAKU_DENSITY_THRESHOLD = 0.3

# 评论正面词表(确定性——共鸣度第 4 分量)
POSITIVE_SENTIMENT_WORDS = (
    "好看", "有用", "推荐", "有帮助", "学到", "收藏了", "干货",
    "温暖", "专业", "赞", "爱了", "走心",
)

# ============================================================
# 情感对齐复合奖励参数(设计文档 §3.2, 宪法域核心)
# ============================================================

# reward = α×转化 + β×合规 + γ×共鸣 − δ×风险
AV_REWARD_ALPHA = 0.3   # 转化(可经 46号审批调整)
AV_REWARD_BETA = 0.3    # 合规(宪法域——同 P5a REWARD_BETA 口径)
AV_REWARD_GAMMA = 0.3   # 情感共鸣(宪法域: γ≥α 永不小于)
AV_REWARD_DELTA = 0.1   # 风险惩罚(可经 46号审批调整)

# 共鸣度归一基准(设计文档 §3.2)
RESONANCE_COMPLETION_REF = 0.6   # 完播 60% 视为满分
RESONANCE_SHARE_REF = 0.05       # 分享 5% 视为满分
RESONANCE_FAVORITE_REF = 0.08    # 收藏 8% 视为满分
# 共鸣度四分量权重: 完播0.4 分享0.3 收藏0.2 正面密度0.1
RESONANCE_W_COMPLETION = 0.4
RESONANCE_W_SHARE = 0.3
RESONANCE_W_FAVORITE = 0.2
RESONANCE_W_POSITIVE = 0.1

# 冷启动样本线: 完播信号 < N 采样回退 P5a 信值对齐奖励
AV_COLD_START_SAMPLES = 3


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def danmaku_negative_density(danmaku: list[str]) -> float:
    """弹幕负面情绪密度(确定性词表, LLM 禁入)

    命中负面词的弹幕条数占比; 无弹幕返回 0。
    """
    texts = [str(d or "") for d in (danmaku or []) if str(d or "")] \
        if danmaku is not None else []
    if not texts:
        return 0.0
    hits = sum(1 for t in texts
               if any(w in t for w in DANMAKU_NEGATIVE_WORDS))
    return round(hits / len(texts), 4)


def positive_density(comments: list[str]) -> float:
    """评论正面情绪密度(确定性词表——共鸣度第 4 分量)"""
    texts = [str(c or "") for c in (comments or []) if str(c or "")] \
        if comments is not None else []
    if not texts:
        return 0.0
    hits = sum(1 for t in texts
               if any(w in t for w in POSITIVE_SENTIMENT_WORDS))
    return round(hits / len(texts), 4)


def compute_resonance(completion_rate: float, share_rate: float,
                      favorite_rate: float,
                      comment_positive: float) -> float:
    """情感共鸣度(设计文档 §3.2 公式, 纯函数)

    0.4×完播归一 + 0.3×分享归一 + 0.2×收藏归一 + 0.1×正面密度,
    各分量按基准归一, clamp [0,1]。
    """
    comp = min(1.0, max(0.0, float(completion_rate or 0))
               / RESONANCE_COMPLETION_REF)
    share = min(1.0, max(0.0, float(share_rate or 0))
                / RESONANCE_SHARE_REF)
    fav = min(1.0, max(0.0, float(favorite_rate or 0))
              / RESONANCE_FAVORITE_REF)
    pos = min(1.0, max(0.0, float(comment_positive or 0)))
    value = (RESONANCE_W_COMPLETION * comp
             + RESONANCE_W_SHARE * share
             + RESONANCE_W_FAVORITE * fav
             + RESONANCE_W_POSITIVE * pos)
    return round(max(0.0, min(1.0, value)), 4)


def compute_emotion_reward(conversion: float, compliance_score: float,
                           resonance: float, risk: float,
                           alpha: float = None,
                           gamma: float = None,
                           delta: float = None) -> float:
    """情感对齐复合奖励(设计文档 §3.2 公式, 纯函数)

    reward = α×转化 + β×合规 + γ×共鸣 − δ×风险, clamp [-1,1]。
    β 恒取宪法域常量(调用方不可传参——P5a 铁律继承);
    **γ ≥ α 铁律: 传入/覆盖后 γ < α 直接 ValueError 拒绝**。
    """
    a = AV_REWARD_ALPHA if alpha is None else float(alpha)
    g = AV_REWARD_GAMMA if gamma is None else float(gamma)
    d = AV_REWARD_DELTA if delta is None else float(delta)
    if g < a:
        raise ValueError(
            f"γ 情感共鸣权重({g})不得低于 α 转化权重({a})"
            "——宪法域铁律(情感真实性权重永不低于转化效率)")
    conv = max(0.0, min(1.0, float(conversion or 0)))
    comp = max(0.0, min(1.0, float(compliance_score or 0) / 100.0))
    res = max(0.0, min(1.0, float(resonance or 0)))
    r = max(0.0, min(1.0, float(risk or 0)))
    reward = a * conv + REWARD_BETA * comp + g * res - d * r
    return round(max(-1.0, min(1.0, reward)), 4)


class BloggerAVLearnService:
    """40号 P6a·多模态自主学习引擎(视听信号/情感奖励/调性/封禁库)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())

    # ============================================================
    # 1. 视听信号采集(双轨: report 上传轨 / collect 批量滚动轨)
    # ============================================================

    async def report_av_metrics(self, follow_id: int,
                                completion_rate: float = None,
                                share_rate: float = None,
                                favorite_rate: float = None,
                                danmaku: list = None,
                                comments: list = None) -> dict:
        """视听指标上报(上传轨——平台回执落地, 1h/6h/24h 多次调用)

        存 avMetrics 到 follow(滚动合并) + 生成视听信号。
        弹幕负面密度 > 阈值生成 negative 信号; 评论正面密度生成
        comment_positive 信号(共鸣度第 4 分量)。

        Raises:
            KeyError: 跟随内容不存在
            ValueError: 内容非已发布 / 指标越界
        """
        follow = await self.repo.get_follow(follow_id)
        if follow is None:
            raise KeyError(f"跟随内容不存在(followId={follow_id})")
        if follow.get("status") != FOLLOW_STATUS_PUBLISHED:
            raise ValueError(
                f"仅已发布内容可上报视听指标(当前{follow.get('status')})")
        rates = {}
        for name, v in (("completionRate", completion_rate),
                        ("shareRate", share_rate),
                        ("favoriteRate", favorite_rate)):
            if v is None:
                continue
            rate = float(v)
            if not 0.0 <= rate <= 1.0:
                raise ValueError(f"{name} 须在 [0,1](当前{rate})")
            rates[name] = round(rate, 4)
        # 滚动合并到 follow.avMetrics(多采样点)
        av_metrics = dict(follow.get("avMetrics") or {})
        av_metrics.update(rates)
        av_metrics.setdefault("samples",
                              0)
        av_metrics["samples"] = int(av_metrics.get("samples") or 0) + 1
        follow = await self.repo.update_follow(
            follow_id, {"avMetrics": av_metrics})
        created = []
        if completion_rate is not None:
            created.append(await self._put_signal(
                follow, SIGNAL_CHANNEL_POSITIVE,
                AV_SIGNAL_KIND_COMPLETION, float(completion_rate)))
        if share_rate is not None:
            created.append(await self._put_signal(
                follow, SIGNAL_CHANNEL_POSITIVE,
                AV_SIGNAL_KIND_SHARE, float(share_rate)))
        if favorite_rate is not None:
            created.append(await self._put_signal(
                follow, SIGNAL_CHANNEL_POSITIVE,
                AV_SIGNAL_KIND_FAVORITE, float(favorite_rate)))
        pos_density = positive_density(comments)
        if comments is not None and len(
                [c for c in comments if str(c or "")]) > 0:
            created.append(await self._put_signal(
                follow, SIGNAL_CHANNEL_POSITIVE,
                AV_SIGNAL_KIND_COMMENT_POSITIVE, pos_density,
                raw={"commentCount": len(comments)}))
        dan_density = danmaku_negative_density(danmaku)
        if dan_density > DANMAKU_DENSITY_THRESHOLD:
            created.append(await self._put_signal(
                follow, SIGNAL_CHANNEL_NEGATIVE,
                AV_SIGNAL_KIND_DANMAKU_NEGATIVE, dan_density,
                raw={"danmakuCount": len(danmaku or []),
                     "sampled": True}))
        return {"followId": follow_id,
                "avMetrics": av_metrics,
                "danmakuDensity": dan_density,
                "positiveDensity": pos_density,
                "created": len(created),
                "signals": [
                    {"signalId": s["signalId"], "kind": s["kind"],
                     "value": s["value"]} for s in created]}

    async def collect_av_signals(self, follow_id: int = None) -> dict:
        """视听信号批量采集(滚动轨——从 follow.avMetrics 重采样)

        - follow_id 指定: 单条已发布内容采集(须已 report 过指标)
        - follow_id 空: 全量已发布且含 avMetrics 的内容批量采集

        Raises:
            KeyError: 跟随内容不存在
            ValueError: 内容非已发布 / 无视听指标
        """
        if follow_id is not None:
            follow = await self.repo.get_follow(follow_id)
            if follow is None:
                raise KeyError(f"跟随内容不存在(followId={follow_id})")
            if follow.get("status") != FOLLOW_STATUS_PUBLISHED:
                raise ValueError(
                    f"仅已发布内容可采集(当前{follow.get('status')})")
            targets = [follow]
        else:
            targets = await self.repo.list_follows(
                status=FOLLOW_STATUS_PUBLISHED, limit=200)
            targets = [f for f in targets if f.get("avMetrics")]
        created = 0
        skipped = 0
        for follow in targets:
            metrics = follow.get("avMetrics") or {}
            if not metrics:
                skipped += 1
                continue
            emitted = []
            for kind, key in (
                    (AV_SIGNAL_KIND_COMPLETION, "completionRate"),
                    (AV_SIGNAL_KIND_SHARE, "shareRate"),
                    (AV_SIGNAL_KIND_FAVORITE, "favoriteRate")):
                if metrics.get(key) is not None:
                    emitted.append(await self._put_signal(
                        follow, SIGNAL_CHANNEL_POSITIVE, kind,
                        float(metrics.get(key))))
            if metrics.get("commentPositive") is not None:
                emitted.append(await self._put_signal(
                    follow, SIGNAL_CHANNEL_POSITIVE,
                    AV_SIGNAL_KIND_COMMENT_POSITIVE,
                    float(metrics.get("commentPositive"))))
            if not emitted:
                skipped += 1
                continue
            created += len(emitted)
        return {"targets": len(targets), "collected": created,
                "skipped": skipped}

    async def _put_signal(self, follow: dict, channel: str,
                          kind: str, value: float,
                          raw: dict = None) -> dict:
        """信号入库(原始流: 可重复采样, 幂等由消费端保证——P5a 口径)"""
        signal_id = await self.repo.next_id("signal")
        signal = {
            "signalId": signal_id,
            "followId": int(follow.get("followId") or 0),
            "bloggerId": int(follow.get("bloggerId") or 0),
            "platform": follow.get("platform", ""),
            "channel": channel,
            "kind": kind,
            "value": round(float(value or 0), 4),
            "raw": raw or {},
            "consumed": False,
            "createdAt": _now_iso(),
        }
        return await self.repo.save_signal(signal)

    async def av_signals_status(self) -> dict:
        """视听信号统计视图(AV kind 计数 + 未消费数 + 最新采样)"""
        av_kinds = (AV_SIGNAL_KIND_COMPLETION, AV_SIGNAL_KIND_SHARE,
                    AV_SIGNAL_KIND_FAVORITE,
                    AV_SIGNAL_KIND_COMMENT_POSITIVE,
                    AV_SIGNAL_KIND_DANMAKU_NEGATIVE)
        signals = await self.repo.list_signals(limit=5000)
        by_kind = {k: 0 for k in av_kinds}
        unconsumed = 0
        latest = []
        for s in signals:
            if s.get("kind") not in av_kinds:
                continue
            by_kind[s.get("kind", "")] = \
                by_kind.get(s.get("kind", ""), 0) + 1
            if not s.get("consumed"):
                unconsumed += 1
            if len(latest) < 5:
                latest.append(
                    {"signalId": s["signalId"],
                     "followId": s["followId"], "kind": s["kind"],
                     "value": s["value"],
                     "consumed": bool(s.get("consumed"))})
        return {"byKind": by_kind, "unconsumed": unconsumed,
                "latest": latest}

    # ============================================================
    # 2. 情感对齐奖励参数(宪法域: β 拒改 + γ≥α 拒改; α/δ 只读
    #    ——调整须 46号审批, 写端点不提供)
    # ============================================================

    async def get_av_reward_config(self) -> dict:
        """AV 奖励参数只读视图(β/γ 标宪法域)"""
        stored = await self.repo.get_av_reward_config()
        try:
            alpha = float(stored.get("alpha") or AV_REWARD_ALPHA)
            gamma = float(stored.get("gamma") or AV_REWARD_GAMMA)
            delta = float(stored.get("delta") or AV_REWARD_DELTA)
        except (TypeError, ValueError):
            alpha, gamma, delta = (AV_REWARD_ALPHA, AV_REWARD_GAMMA,
                                   AV_REWARD_DELTA)
        return {
            "alpha": alpha, "beta": AV_REWARD_BETA,
            "gamma": gamma, "delta": delta,
            "betaNote": "宪法域: β 合规权重硬编码(继承 P5a), 不可调整",
            "gammaNote": "宪法域: γ 情感共鸣权重硬校验 γ≥α, "
                         "任何 γ<α 调整请求被拒绝",
            "resonanceRefs": {
                "completion": RESONANCE_COMPLETION_REF,
                "share": RESONANCE_SHARE_REF,
                "favorite": RESONANCE_FAVORITE_REF},
            "coldStartSamples": AV_COLD_START_SAMPLES,
            "formula": ("reward = alpha×conversion + beta×compliance"
                        " + gamma×resonance - delta×risk "
                        "(clamp [-1,1])"),
        }

    async def set_av_reward_params(self, alpha: float = None,
                                   gamma: float = None,
                                   delta: float = None,
                                   beta: float = None) -> dict:
        """AV 奖励参数写入(仅服务层内部/测试用)

        红线: beta 传入任何值直接 ValueError; 合并后 γ<α 直接
        ValueError——宪法域双保险(P5a β 拒改 + P6a γ≥α)。
        """
        if beta is not None:
            raise ValueError(
                "β 合规权重为宪法域参数(硬编码), 任何调整请求被拒绝")
        stored = await self.repo.get_av_reward_config()
        if alpha is not None:
            a = float(alpha)
            if not 0.0 <= a <= 1.0:
                raise ValueError("α 须在 [0,1]")
            stored["alpha"] = a
        if gamma is not None:
            g = float(gamma)
            if not 0.0 <= g <= 1.0:
                raise ValueError("γ 须在 [0,1]")
            stored["gamma"] = g
        if delta is not None:
            d = float(delta)
            if not 0.0 <= d <= 1.0:
                raise ValueError("δ 须在 [0,1]")
            stored["delta"] = d
        # γ≥α 合并校验(覆盖存储值与默认值)
        eff_alpha = float(stored.get("alpha") or AV_REWARD_ALPHA)
        eff_gamma = float(stored.get("gamma") or AV_REWARD_GAMMA)
        if eff_gamma < eff_alpha:
            raise ValueError(
                f"γ 情感共鸣权重({eff_gamma})不得低于 α 转化权重"
                f"({eff_alpha})——宪法域铁律")
        await self.repo.save_av_reward_config(stored)
        return await self.get_av_reward_config()

    # ============================================================
    # 3. 跨平台调性 + 封禁元素库
    # ============================================================

    async def get_tone(self, platform: str) -> dict:
        """平台调性参数视图(附该平台适用封禁元素)

        Raises:
            ValueError: 平台无效
        """
        if platform not in AV_PLATFORMS:
            raise ValueError(
                f"平台无效({platform}, "
                f"须为{'/'.join(AV_PLATFORMS)})")
        banned = await self.repo.list_banned_elements(
            platform=platform)
        return {"platform": platform,
                "tone": PLATFORM_TONE[platform],
                "bannedElements": [
                    {"elementId": b["elementId"], "kind": b.get("kind"),
                     "value": b.get("value"),
                     "platform": b.get("platform") or "全平台"}
                    for b in banned]}

    async def add_banned_element(self, kind: str, value: str,
                                 platform: str = None,
                                 source: str = "admin") -> dict:
        """封禁视听元素登记(全平台共享 + 平台增量, 实时同步约束库)

        Raises:
            ValueError: 种类/取值/平台非法 / 重复登记
        """
        if kind not in ELEMENT_KINDS:
            raise ValueError(
                f"元素种类无效({kind}, 须为{'/'.join(ELEMENT_KINDS)})")
        if not (value or "").strip():
            raise ValueError("元素取值不能为空")
        if platform:
            if platform not in AV_PLATFORMS:
                raise ValueError(
                    f"平台无效({platform}, "
                    f"须为{'/'.join(AV_PLATFORMS)})")
        for existing in await self.repo.list_banned_elements(
                platform=platform, kind=kind, limit=1000):
            if (existing.get("kind") == kind
                    and existing.get("value") == value.strip()
                    and (existing.get("platform") or "")
                    == (platform or "")):
                raise ValueError(
                    f"封禁元素已登记(kind={kind}, value={value.strip()},"
                    f" platform={platform or '全平台'}——勿重复)")
        element_id = await self.repo.next_id("element")
        element = {
            "elementId": element_id,
            "kind": kind,
            "value": value.strip(),
            "platform": platform or "",
            "source": source,
            "createdAt": _now_iso(),
        }
        return await self.repo.save_banned_element(element)

    async def check_banned(self, kind: str, value: str,
                           platform: str = None) -> bool:
        """封禁元素校验(P6b 生成前置门: BGM/转场槽位选取)"""
        for e in await self.repo.list_banned_elements(
                platform=platform, kind=kind, limit=1000):
            if e.get("value") == (value or "").strip():
                return True
        return False

    # ============================================================
    # 4. 学习轮(视听信号消费 → 情感对齐奖励 → 44号 Hedge)
    # ============================================================

    async def run_av_learning(self) -> dict:
        """视听信号消费学习轮: 未消费信号按 followId 聚合
        → 情感对齐奖励(冷启动回退 P5a) → 44号 submit_feedback
        (source: blogger_p6a) → 学习轮触发

        Raises:
            ValueError: 无未消费信号 / 自主行为已暂停
        """
        # P5d 铁律: pause 后自主行为一律拒绝(仲裁优先于调度)
        from services.blogger_auto_govern_service import \
            BloggerAutoGovernService
        await BloggerAutoGovernService(
            repo=self.repo, blogger_service=self.svc
        ).require_running_async()
        signals = await self.repo.list_signals(
            consumed=False, limit=1000)
        if not signals:
            raise ValueError(
                "无未消费信号, 先执行视听信号采集(collect/report)")
        groups: dict[int, list[dict]] = {}
        for s in signals:
            groups.setdefault(int(s.get("followId") or 0), []).append(s)
        params = await self.get_av_reward_config()
        submitted, skipped, results = 0, 0, []
        for fid, group in sorted(groups.items()):
            follow = await self.repo.get_follow(fid)
            work = (await self.repo.get_work(
                int((follow or {}).get("workId") or 0)) or {})
            factors = ((work.get("scoreSnapshot") or {})
                       .get("factors")) or []
            if follow is None or not factors:
                for s in group:
                    await self.repo.update_signal(
                        s["signalId"], {"consumed": True})
                skipped += 1
                logger.warning(
                    "p6a_signal_skip followId=%s: follow/因子快照缺失",
                    fid)
                continue
            agg = self._aggregate(group)
            clicks = int(agg.get(SIGNAL_KIND_CLICKS, 0))
            registered = int(agg.get(SIGNAL_KIND_REGISTERED, 0))
            ordered = int(agg.get(SIGNAL_KIND_ORDERED, 0))
            compliance = agg.get(
                SIGNAL_KIND_COMPLIANCE_SCORE,
                float(follow.get("complianceScore") or 0))
            risk = compute_risk_loss(
                int(agg.get(SIGNAL_KIND_REPORTS, 0)),
                clicks,
                int(agg.get(SIGNAL_KIND_RATE_LIMIT, 0)),
                int(agg.get(SIGNAL_KIND_COMPLAINTS, 0)))
            completion_samples = sum(
                1 for s in group
                if s.get("kind") == AV_SIGNAL_KIND_COMPLETION)
            conv = compute_conversion_efficiency(
                registered, ordered, clicks)
            if completion_samples < AV_COLD_START_SAMPLES:
                # 冷启动回退: P5a 信值对齐奖励(平滑过渡)
                mode = "cold_start"
                reward = compute_value_aligned_reward(
                    conv, compliance, risk)
            else:
                mode = "emotion_aligned"
                resonance = compute_resonance(
                    agg.get(AV_SIGNAL_KIND_COMPLETION, 0),
                    agg.get(AV_SIGNAL_KIND_SHARE, 0),
                    agg.get(AV_SIGNAL_KIND_FAVORITE, 0),
                    agg.get(AV_SIGNAL_KIND_COMMENT_POSITIVE, 0))
                reward = compute_emotion_reward(
                    conv, compliance, resonance, risk,
                    alpha=params["alpha"],
                    gamma=params["gamma"],
                    delta=params["delta"])
            from services.ai_learning_service import submit_feedback
            await submit_feedback({
                "scorerId": "blogger_work_gate",
                "factors": factors,
                "scoreAtDecision": work.get("score", 0),
                "actualAction": ("auto_follow" if reward > 0
                                 else "no_traffic"),
                "correct": reward > 0,
                "reward": reward,
                "note": (f"P6a av-fused followId={fid} mode={mode} "
                         f"completionSamples={completion_samples} "
                         f"conv={conv} compliance={compliance} "
                         f"risk={risk:.2f} reward={reward}"),
                "source": "blogger_p6a",
            })
            for s in group:
                await self.repo.update_signal(
                    s["signalId"], {"consumed": True})
            submitted += 1
            results.append({"followId": fid, "mode": mode,
                            "reward": reward})
        # 学习轮触发(反馈不足时 409 语义, fail-soft 返回 deferred)
        learning = {"deferred": "未触发"}
        try:
            from services.ai_learning_service import run_learning_cycle
            learning = await run_learning_cycle("blogger_work_gate")
        except ValueError as exc:
            learning = {"deferred": str(exc)}
        except Exception as exc:  # noqa: BLE001 学习轮异常不阻断回流
            logger.warning("p6a_learn_cycle_failed: %s", exc)
            learning = {"deferred": str(exc)}
        return {"submitted": submitted, "skipped": skipped,
                "results": results, "learning": learning}

    @staticmethod
    def _aggregate(group: list[dict]) -> dict:
        """采样点聚合: 同 kind 取 value 最大(行为率滚动上报口径)"""
        agg: dict[str, float] = {}
        for s in group:
            k = s.get("kind", "")
            v = float(s.get("value") or 0)
            if k not in agg or v > agg[k]:
                agg[k] = v
        return agg
