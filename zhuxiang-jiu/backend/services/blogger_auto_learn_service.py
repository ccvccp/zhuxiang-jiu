"""40号 P5a·自主学习引擎服务(设计文档《40号 P5 升级方案》§3)

三通道信号流(正向/负向/合规)实时采集 → 信值对齐复合奖励
→ 44号 Hedge 学习闭环(第21档案 blogger_work_gate) → 微调研(低置信人机共学)

架构口径:
    - 信号与反馈分离: blogger_signals 是原始流(可重复采样, 不去重),
      44号 feedback 是学习消费端(learn_run 按 consumed 标记幂等)
    - 复合奖励双轨: 归因样本 ≥ COLD_START_CLICKS 走信值对齐公式;
      样本不足回退现有 compute_reward 流量分量(平滑过渡不破坏既有闭环)
    - 采样点聚合: 多次 collect = 多采样点, 聚合按 kind 取 max
      (clicks/registered/ordered 等计数指标单调不减)

红线(宪法域, 永不可校准):
    - β 合规权重硬编码 [0.1, 1.0], 任何修改请求直接 ValueError 拒绝
    - LLM 禁入判定链: 负面情绪 = 确定性词表密度, 奖励 = 确定性公式
    - 微调研仅低置信(<0.8)场景可发起, 高置信发起直接拒绝
"""

import logging
from datetime import datetime, UTC

from repositories.blogger_repository import (
    BloggerRepository, FOLLOW_STATUS_PUBLISHED,
)
from services.blogger_service import (
    BloggerService, compute_reward, CLICK_P90_REF,
)

logger = logging.getLogger(__name__)


# ============================================================
# 三通道信号常量(设计文档 §3.1)
# ============================================================

SIGNAL_CHANNEL_POSITIVE = "positive"      # 正向: 点击/注册/下单
SIGNAL_CHANNEL_NEGATIVE = "negative"      # 负向: 举报/投诉/负面情绪/限流
SIGNAL_CHANNEL_COMPLIANCE = "compliance"  # 合规: 三审合规分
SIGNAL_CHANNELS = (SIGNAL_CHANNEL_POSITIVE,
                   SIGNAL_CHANNEL_NEGATIVE,
                   SIGNAL_CHANNEL_COMPLIANCE)

# 正向信号种类
SIGNAL_KIND_CLICKS = "clicks"
SIGNAL_KIND_REGISTERED = "registered"
SIGNAL_KIND_ORDERED = "ordered"
# 负向信号种类
SIGNAL_KIND_REPORTS = "reports"
SIGNAL_KIND_COMPLAINTS = "complaints"
SIGNAL_KIND_NEGATIVE_SENTIMENT = "negative_sentiment"
SIGNAL_KIND_RATE_LIMIT = "rate_limit"
# 合规信号种类
SIGNAL_KIND_COMPLIANCE_SCORE = "compliance_score"
# 人机共学信号(微调研投票生成)
SIGNAL_KIND_HUMAN_FEEDBACK = "human_feedback"

# 负面情绪词表(确定性——口径同源 38号 NEGATIVE_WORDS, 评论场景裁剪)
NEGATIVE_SENTIMENT_WORDS = (
    "骗子", "垃圾", "坑人", "被骗", "投诉", "退款",
    "差评", "假货", "套路", "举报", "离谱", "敷衍",
)
# 负面情绪密度阈值(命中评论占比超过才生成信号)
NEGATIVE_DENSITY_THRESHOLD = 0.3

# ============================================================
# 信值对齐复合奖励参数(设计文档 §3.2)
# ============================================================

# reward = α×信值转化效率 + β×合规得分 − γ×风险损失
REWARD_ALPHA = 0.5   # 信值转化主目标(可经 46号审批调整, P5a 只读)
REWARD_BETA = 0.3    # 合规权重(宪法域, 永不可调整)
REWARD_GAMMA = 0.2    # 风险惩罚(可经 46号审批调整, P5a 只读)
# β 宪法域区间(任何写入请求直接拒绝)
BETA_DOMAIN_MIN = 0.1
BETA_DOMAIN_MAX = 1.0

# 信值转化效率基准: 点击→深度转化 5% 视为满分
CONVERSION_REF = 0.05
# 举报/投诉率基准: 2% 视为风险分量满分
RISK_RATE_REF = 0.02
# 限流次数归一基准
RATE_LIMIT_REF = 3
# 冷启动样本线: 点击 < 3 回退流量分量奖励(compute_reward)
COLD_START_CLICKS = 3
# 微调研低置信线: 置信度 ≥ 0.8 无需调研
POLL_CONFIDENCE_MAX = 0.8


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def negative_density(comments: list[str]) -> float:
    """评论负面情绪密度(确定性词表, LLM 禁入)

    命中负面词的评论条数占比; 无评论返回 0。
    """
    texts = [str(c or "") for c in (comments or []) if str(c or "")]
    if not texts:
        return 0.0
    hits = sum(1 for t in texts
               if any(w in t for w in NEGATIVE_SENTIMENT_WORDS))
    return round(hits / len(texts), 4)


def compute_conversion_efficiency(registered: int, ordered: int,
                                  clicks: int) -> float:
    """信值转化效率(设计文档 §3.2)

    (注册×0.4 + 深度激活×0.6) / 点击数, 按 5% 基准归一, clamp [0,1]。
    """
    c = max(1, int(clicks or 0))
    base = (int(registered or 0) * 0.4
            + int(ordered or 0) * 0.6) / c
    return round(max(0.0, min(1.0, base / CONVERSION_REF)), 4)


def compute_risk_loss(reports: int, clicks: int,
                      rate_limits: int, complaints: int) -> float:
    """风险损失(设计文档 §3.2)

    举报率×0.5 + 限流×0.3 + 投诉×0.2, 各分量按基准归一, clamp [0,1]。
    """
    c = max(1, int(clicks or 0))
    report_rate = min(1.0, (int(reports or 0) / c) / RISK_RATE_REF)
    rl = min(1.0, int(rate_limits or 0) / RATE_LIMIT_REF)
    comp_rate = min(1.0, (int(complaints or 0) / c) / RISK_RATE_REF)
    return round(max(0.0, min(
        1.0, 0.5 * report_rate + 0.3 * rl + 0.2 * comp_rate)), 4)


def compute_value_aligned_reward(conversion: float,
                                 compliance_score: float,
                                 risk: float,
                                 alpha: float = None,
                                 gamma: float = None) -> float:
    """信值对齐复合奖励(设计文档 §3.2 公式, 纯函数)

    reward = α×转化效率 + β×合规得分 − γ×风险损失, clamp [-1,1]
    β 恒取宪法域常量 REWARD_BETA(调用方不可传参——铁律)。
    """
    a = REWARD_ALPHA if alpha is None else float(alpha)
    g = REWARD_GAMMA if gamma is None else float(gamma)
    conv = max(0.0, min(1.0, float(conversion or 0)))
    comp = max(0.0, min(1.0, float(compliance_score or 0) / 100.0))
    r = max(0.0, min(1.0, float(risk or 0)))
    reward = a * conv + REWARD_BETA * comp - g * r
    return round(max(-1.0, min(1.0, reward)), 4)


class BloggerAutoLearnService:
    """40号 P5a·自主学习引擎(信号采集/复合奖励/微调研/学习轮)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())

    # ============================================================
    # 1. 三通道信号采集
    # ============================================================

    async def collect_signals(self, follow_id: int = None) -> dict:
        """信号采集(正向+合规自动; 负向经 report_negative 上报)

        - follow_id 指定: 单条已发布内容采集
        - follow_id 空: 全量已发布内容批量采集(滚动采样, 可重复调用)

        Raises:
            KeyError: 跟随内容不存在
            ValueError: 内容非已发布状态
        """
        if follow_id is not None:
            follow = await self.repo.get_follow(follow_id)
            if follow is None:
                raise KeyError(f"跟随内容不存在(followId={follow_id})")
            if follow.get("status") != FOLLOW_STATUS_PUBLISHED:
                raise ValueError(
                    f"仅已发布内容可采集信号(当前"
                    f"{follow.get('status')})")
            targets = [follow]
        else:
            targets = await self.repo.list_follows(
                status=FOLLOW_STATUS_PUBLISHED, limit=200)
        created = []
        for follow in targets:
            # 正向: attract 归因聚合(best-effort, 复用 _link_metrics)
            metrics = await self.svc._link_metrics(
                [follow.get("shortCode", "")])
            for kind in (SIGNAL_KIND_CLICKS, SIGNAL_KIND_REGISTERED,
                         SIGNAL_KIND_ORDERED):
                created.append(await self._put_signal(
                    follow, SIGNAL_CHANNEL_POSITIVE, kind,
                    float(metrics.get(kind, 0))))
            # 合规: 三审闸门分数(follow 记录现成字段)
            created.append(await self._put_signal(
                follow, SIGNAL_CHANNEL_COMPLIANCE,
                SIGNAL_KIND_COMPLIANCE_SCORE,
                float(follow.get("complianceScore") or 0)))
        return {
            "targets": len(targets),
            "collected": len(created),
            "signals": [
                {"signalId": s["signalId"], "followId": s["followId"],
                 "channel": s["channel"], "kind": s["kind"],
                 "value": s["value"]}
                for s in created
            ],
        }

    async def report_negative(self, follow_id: int,
                              reports: int = 0,
                              complaints: int = 0,
                              comments: list = None,
                              rate_limits: int = 0) -> dict:
        """负向信号上报(代理轨/运营手工, 确定性词表判定)

        Returns:
            {"created": N, "sentimentDensity": float, "signals": [...]}
        """
        follow = await self.repo.get_follow(follow_id)
        if follow is None:
            raise KeyError(f"跟随内容不存在(followId={follow_id})")
        created = []
        if int(reports or 0) > 0:
            created.append(await self._put_signal(
                follow, SIGNAL_CHANNEL_NEGATIVE, SIGNAL_KIND_REPORTS,
                float(reports)))
        if int(complaints or 0) > 0:
            created.append(await self._put_signal(
                follow, SIGNAL_CHANNEL_NEGATIVE, SIGNAL_KIND_COMPLAINTS,
                float(complaints)))
        if int(rate_limits or 0) > 0:
            created.append(await self._put_signal(
                follow, SIGNAL_CHANNEL_NEGATIVE,
                SIGNAL_KIND_RATE_LIMIT, float(rate_limits)))
        density = negative_density(comments)
        if density > NEGATIVE_DENSITY_THRESHOLD:
            created.append(await self._put_signal(
                follow, SIGNAL_CHANNEL_NEGATIVE,
                SIGNAL_KIND_NEGATIVE_SENTIMENT, density,
                raw={"commentCount": len(comments or []),
                     "sampled": True}))
        return {"created": len(created),
                "sentimentDensity": density,
                "signals": [
                    {"signalId": s["signalId"], "kind": s["kind"],
                     "value": s["value"]} for s in created]}

    async def _put_signal(self, follow: dict, channel: str,
                          kind: str, value: float,
                          raw: dict = None) -> dict:
        """信号入库(原始流: 可重复采样不去重, 幂等由消费端保证)"""
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

    async def signals_status(self) -> dict:
        """信号统计视图(按通道/种类计数 + 未消费数 + 最新采样)"""
        signals = await self.repo.list_signals(limit=5000)
        by_channel = {ch: 0 for ch in SIGNAL_CHANNELS}
        by_kind: dict[str, int] = {}
        unconsumed = 0
        for s in signals:
            by_channel[s.get("channel", "")] = \
                by_channel.get(s.get("channel", ""), 0) + 1
            by_kind[s.get("kind", "")] = \
                by_kind.get(s.get("kind", ""), 0) + 1
            if not s.get("consumed"):
                unconsumed += 1
        latest = [
            {"signalId": s["signalId"], "followId": s["followId"],
             "channel": s["channel"], "kind": s["kind"],
             "value": s["value"], "consumed": bool(s.get("consumed"))}
            for s in signals[:5]
        ]
        return {"total": len(signals),
                "byChannel": by_channel, "byKind": by_kind,
                "unconsumed": unconsumed, "latest": latest}

    # ============================================================
    # 2. 复合奖励参数(宪法域: β 拒改; α/γ 只读——调整须 46号审批,
    #    写端点 P5a 不提供, set 仅服务层内部校验用途)
    # ============================================================

    async def get_reward_config(self) -> dict:
        """奖励参数只读视图(β 标注宪法域)"""
        stored = await self.repo.get_reward_config()
        try:
            alpha = float(stored.get("alpha") or REWARD_ALPHA)
            gamma = float(stored.get("gamma") or REWARD_GAMMA)
        except (TypeError, ValueError):
            alpha, gamma = REWARD_ALPHA, REWARD_GAMMA
        return {
            "alpha": alpha,
            "beta": REWARD_BETA,
            "gamma": gamma,
            "betaDomain": [BETA_DOMAIN_MIN, BETA_DOMAIN_MAX],
            "betaNote": "宪法域: 合规权重硬编码, 任何调整请求被拒绝",
            "conversionRef": CONVERSION_REF,
            "riskRateRef": RISK_RATE_REF,
            "coldStartClicks": COLD_START_CLICKS,
            "formula": ("reward = alpha×conversion + beta×compliance"
                        " - gamma×risk (clamp [-1,1])"),
        }

    async def set_reward_params(self, alpha: float = None,
                                gamma: float = None,
                                beta: float = None) -> dict:
        """奖励参数写入(仅服务层内部/测试用)

        红线: beta 传入任何值直接 ValueError——宪法域不可调整。
        α/γ 调整生产须走 46号审批总线(P5d 接入), P5a 不开放端点。
        """
        if beta is not None:
            raise ValueError(
                "β 合规权重为宪法域参数(硬编码 [0.1,1.0]), "
                "任何调整请求被拒绝")
        stored = await self.repo.get_reward_config()
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
        await self.repo.save_reward_config(stored)
        return await self.get_reward_config()

    # ============================================================
    # 3. 学习轮(信号消费 → 复合奖励 → 44号 Hedge)
    # ============================================================

    @staticmethod
    def _aggregate(group: list[dict]) -> dict:
        """采样点聚合: 同 kind 取 value 最大(计数指标单调不减)"""
        agg: dict[str, float] = {}
        for s in group:
            k = s.get("kind", "")
            v = float(s.get("value") or 0)
            if k not in agg or v > agg[k]:
                agg[k] = v
        return agg

    async def run_learning(self) -> dict:
        """信号消费学习轮: 未消费信号按 followId 聚合 → 复合奖励
        → 44号 submit_feedback(source: blogger_p5a) → 学习轮触发

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
            raise ValueError("无未消费信号, 先执行信号采集(collect)")
        groups: dict[int, list[dict]] = {}
        for s in signals:
            groups.setdefault(int(s.get("followId") or 0), []).append(s)
        submitted, skipped, results = 0, 0, []
        for fid, group in sorted(groups.items()):
            follow = await self.repo.get_follow(fid)
            work = (await self.repo.get_work(
                int((follow or {}).get("workId") or 0)) or {})
            factors = ((work.get("scoreSnapshot") or {})
                       .get("factors")) or []
            if follow is None or not factors:
                # 消费掉不可学习的信号(留痕防重复扫描)
                for s in group:
                    await self.repo.update_signal(
                        s["signalId"], {"consumed": True})
                skipped += 1
                logger.warning(
                    "p5a_signal_skip followId=%s: follow/因子快照缺失",
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
            if clicks < COLD_START_CLICKS:
                # 冷启动回退: 流量分量奖励(既有 compute_reward)
                mode = "cold_start"
                reward = compute_reward(
                    clicks, 0.0, 1.0, CLICK_P90_REF)
            else:
                mode = "value_aligned"
                conv = compute_conversion_efficiency(
                    registered, ordered, clicks)
                reward = compute_value_aligned_reward(
                    conv, compliance, risk)
            from services.ai_learning_service import submit_feedback
            await submit_feedback({
                "scorerId": "blogger_work_gate",
                "factors": factors,
                "scoreAtDecision": work.get("score", 0),
                "actualAction": ("auto_follow" if reward > 0
                                 else "no_traffic"),
                "correct": reward > 0,
                "reward": reward,
                "note": (f"P5a signal-fused followId={fid} "
                         f"mode={mode} clicks={clicks} reg={registered}"
                         f" ord={ordered} compliance={compliance} "
                         f"risk={risk:.2f} reward={reward}"),
                "source": "blogger_p5a",
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
            logger.warning("p5a_learn_cycle_failed: %s", exc)
            learning = {"deferred": str(exc)}
        return {"submitted": submitted, "skipped": skipped,
                "results": results, "learning": learning}

    # ============================================================
    # 4. 微调研(低置信人机共学)
    # ============================================================

    async def create_poll(self, topic: str, question: str,
                          options: list, confidence: float,
                          scenario: str = "creation") -> dict:
        """发起微调研(仅低置信 <0.8 场景; 3 选项投票)

        Raises:
            ValueError: 置信度≥0.8 无需调研 / 选项数非法 / 参数缺失
        """
        conf = float(confidence or 0)
        if conf >= POLL_CONFIDENCE_MAX:
            raise ValueError(
                f"置信度{conf}≥{POLL_CONFIDENCE_MAX}, 无需微调研"
                "(高置信场景禁止发起——调研成本留给真正不确定的决策)")
        opts = [str(o).strip() for o in (options or [])
                if str(o).strip()]
        if not 2 <= len(opts) <= 5:
            raise ValueError("选项须 2-5 个")
        if not (topic or "").strip() or not (question or "").strip():
            raise ValueError("调研主题与问题不能为空")
        poll_id = await self.repo.next_id("poll")
        poll = {
            "pollId": poll_id,
            "topic": topic.strip(),
            "scenario": scenario,
            "question": question.strip(),
            "options": opts,
            "votes": {i: 0 for i in range(len(opts))},
            "answer": -1,
            "confidence": conf,
            "status": "open",
            "createdAt": _now_iso(),
            "closedAt": "",
        }
        return await self.repo.save_poll(poll)

    async def vote_poll(self, poll_id: int, option: int) -> dict:
        """运营投票(即时生效: 单票即定——3 选项投票语义)

        投票关闭后生成 human_feedback 信号(source: human 人机共学)。

        Raises:
            KeyError: 调研不存在
            ValueError: 调研已关闭 / 选项越界
        """
        poll = await self.repo.get_poll(poll_id)
        if poll is None:
            raise KeyError(f"微调研不存在(pollId={poll_id})")
        if poll.get("status") != "open":
            raise ValueError(f"调研已关闭(当前{poll.get('status')})")
        idx = int(option)
        if not 0 <= idx < len(poll.get("options") or []):
            raise ValueError(f"选项越界(0-{len(poll['options']) - 1})")
        votes = poll.get("votes") or {}
        votes[idx] = int(votes.get(idx, 0)) + 1
        poll.update({"votes": votes, "answer": idx,
                     "status": "closed", "closedAt": _now_iso()})
        poll = await self.repo.update_poll(poll_id, poll)
        # 投票记录作为高质量反馈注入学习轮(source: human)
        await self._put_human_feedback_signal(poll, idx)
        return poll

    async def _put_human_feedback_signal(self, poll: dict,
                                         option: int) -> None:
        """微调研投票 → human_feedback 信号(留痕人机共学)"""
        signal_id = await self.repo.next_id("signal")
        await self.repo.save_signal({
            "signalId": signal_id,
            "followId": 0,
            "bloggerId": 0,
            "platform": "",
            "channel": SIGNAL_CHANNEL_POSITIVE,
            "kind": SIGNAL_KIND_HUMAN_FEEDBACK,
            "value": 1.0,
            "raw": {"pollId": poll["pollId"],
                    "topic": poll.get("topic", ""),
                    "answer": poll.get("options", [""])[option]},
            "consumed": True,   # 人机共学信号仅留痕, 不进学习轮聚合
            "createdAt": _now_iso(),
        })

    async def list_pending_polls(self) -> list[dict]:
        """待调研列表(open 状态)"""
        return await self.repo.list_polls(status="open")
