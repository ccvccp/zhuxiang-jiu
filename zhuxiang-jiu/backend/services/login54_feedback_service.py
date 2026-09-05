"""54号·小竹AI智能登录引擎大模型 决策回流管道
(login54_feedback_service, P1)

计划(docs/54号_小竹AI智能登录引擎大模型实施计划.md §三):
    真值标注七类信号源(53号既有数据——零侵入纯扫描):

    | 信号源                     | reward | 语义            |
    |---------------------------|--------|-----------------|
    | 登录成功+驻留领取/导览      | +1.0   | 正当(allow 正确) |
    | 登录成功+5min 无行为+退出   | +0.3   | 弱正当           |
    | 凭证失败 3 次+切换通道成功  | -1.0   | 恶意倾向(挑战正确)|
    | 风控拦截后申诉成立(43号)    | -1.0   | 误拦负反馈修正    |
    | 风控拦截后无申诉            | +1.0   | 拦截正确         |
    | 令牌重放/跨会员盗用(红队)   | -1.0   | 对抗样本         |
    | 43号 riskFlagged 高危登录  | -0.5   | 人工标注兜底     |

回流时机(计划 §三):
    - 同步语义: collect 可在事件落库后随时调用
      (幂等扫描——已标注事件跳过, 47号 scan 幂等教训)
    - T+1 批次补标: 驻留/申诉信号延时可得 →
      pending_dwell/pending_appeal 延迟态 + 重扫转正
      (调度器 login54_scheduler——46号 P6 范式)

44号反馈池双写(计划 §二):
    每条终态标注 → submit_feedback(login_orchestration)
    走 44号既有 record 接口——学习闭环数据源。
    决策上下文从 53号 events 历史重构(八因子快照),
    真值(reward/expected)与因子配对入池。

设计红线:
    - 53号/43号/44号 零改动(纯读取式接入)
    - 幂等铁律: eventId 1:1——labeled 终态永不重标,
      pending 可重评转正(不重复入池)
    - fail-soft: 单事件标注异常跳过留痕, 不阻断批量
"""

import logging
from datetime import datetime

from core.helpers import ts

from repositories.login54_repository import (
    Login54Repository,
)
from services.login54_scorer import Login54Scorer

logger = logging.getLogger("login54_feedback_service")

MODEL_VERSION = "v1-login54-feedback"

SCORER_ID = "login_orchestration"

# 七类信号源 → reward(计划 §三真值标注口径)
SIGNAL_REWARDS = {
    "retention_dwell": 1.0,    # 登录成功+驻留(正当)
    "weak_dwell": 0.3,          # 登录成功+弱驻留(弱正当)
    "fail_switch": -1.0,        # 凭证失败3次+切换成功(恶意倾向)
    "appeal_upheld": -1.0,      # 风控拦截+申诉成立(误拦修正)
    "block_correct": 1.0,       # 风控拦截+无申诉(拦截正确)
    "replay_theft": -1.0,       # 令牌重放/跨会员盗用(对抗样本)
    "risk_flagged": -0.5,       # 43号高危会员登录(人工兜底)
}

SIGNAL_NAMES = {
    "retention_dwell": "驻留正当",
    "weak_dwell": "弱驻留",
    "fail_switch": "失败切换(恶意倾向)",
    "appeal_upheld": "申诉成立(误拦)",
    "block_correct": "拦截正确",
    "replay_theft": "对抗样本(重放/盗用)",
    "risk_flagged": "高危会员登录",
}

# 阻断/强制核验类决策(43号拦截在 53号的落点)
BLOCKED_DECISIONS = ("enhanced", "security_challenge")

# 红队对抗标记(detail/explainRef 命中即对抗样本)
ADVERSARIAL_MARKERS = (
    "replay", "盗用", "跨会员", "token_reuse", "stolen",
)

# 弱驻留判定窗口(登录成功后 5 分钟内无行为)
DWELL_WINDOW_SECONDS = 300

# 失败切换阈值(凭证失败 N 次+切换通道成功)
FAIL_SWITCH_THRESHOLD = 3

# 四级响应档位(53号 RISK_TIERS 对齐)
TIERS = ("silent", "one_tap", "step_up", "enhanced")

# 中间态决策(不产出终态标注——完成态事件承载标签)
INTERMEDIATE_DECISIONS = (
    "credential_fail", "budget_block", "step_up",
    "one_tap_pending", "dual_factor_pending",
)


def _truthy(value) -> bool:
    """健壮布尔解析(Redis 态 success 为 '1'/'0' 字符串)"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes")


def _parse_ts(value: str) -> datetime | None:
    """ISO8601 时间戳解析(失败返回 None——fail-soft)"""
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _ts_utc(value: str) -> datetime | None:
    """时间戳统一 UTC(混合时区不可字典序比较——
    解析后比较; 无时区按 UTC)"""
    from datetime import timezone
    dt = _parse_ts(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Login54FeedbackService:
    """54号决策回流管道(七类信号源→reward→44号池双写)"""

    def __init__(self):
        self.repo = Login54Repository()

    # ============================================================
    # 回流标注入口(collect——幂等批量扫描)
    # ============================================================

    async def collect_feedback(
            self, member_id: int = None,
            limit: int = 500) -> dict:
        """触发一轮回流标注(53号 events 扫描→七类信号→
        login54_feedback 落库 + 44号池双写)

        幂等铁律: eventId 1:1——labeled 终态跳过,
        pending(pending_dwell/pending_appeal)重评转正。
        """
        from repositories.login53_repository import (
            Login53Repository,
        )
        login53_repo = Login53Repository()
        events = await login53_repo.list_events(
            member_id=member_id, limit=limit)
        # 时序升序(失败切换链/上下文重构依赖因果序)
        events.sort(key=lambda e: int(
            e.get("eventId") or 0))
        # 会员分组(失败切换链检测——按会员事件序列)
        by_member: dict[int, list] = {}
        for event in events:
            by_member.setdefault(int(
                event.get("memberId") or 0), []).append(event)

        summary = {
            "scanned": len(events), "labeled": 0,
            "deferred": 0, "skipped": 0, "poolSubmitted": 0,
            "poolFailed": 0, "signals": {},
            "errors": [], "collectedAt": ts(),
        }

        for event in events:
            try:
                outcome = await self._process_event(
                    event,
                    by_member.get(int(
                        event.get("memberId") or 0), []))
                kind = outcome.get("kind")
                if kind == "labeled":
                    summary["labeled"] += 1
                    source = outcome["source"]
                    summary["signals"][source] = \
                        summary["signals"].get(source, 0) + 1
                    if outcome.get("poolSubmitted"):
                        summary["poolSubmitted"] += 1
                    elif outcome.get("poolFailed"):
                        summary["poolFailed"] += 1
                elif kind == "deferred":
                    summary["deferred"] += 1
                else:
                    summary["skipped"] += 1
            except Exception as exc:  # noqa: BLE001
                # fail-soft: 单事件异常不阻断批量(留痕)
                summary["errors"].append(
                    f"event={event.get('eventId')}:"
                    f"{str(exc)[:60]}")
                logger.warning(
                    "login54_label_failed event=%s: %s",
                    event.get("eventId"), exc)

        # 模型事件留痕(P0 基建复用——版本溯源)
        try:
            from services.login54_service import (
                Login54Service,
            )
            await Login54Service().record_model_event(
                "feedback_collect", {
                    "scanned": summary["scanned"],
                    "labeled": summary["labeled"],
                    "deferred": summary["deferred"],
                    "skipped": summary["skipped"],
                    "poolSubmitted":
                        summary["poolSubmitted"],
                    "signals": summary["signals"],
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "login54_collect_event_failed: %s", exc)

        summary["success"] = True
        summary["note"] = ("决策回流幂等扫描——七类信号源"
                           "真值标注+44号池双写")
        return summary

    # ============================================================
    # 单事件处理(信号判定→落库→双写)
    # ============================================================

    async def _process_event(self, event: dict,
                             member_events: list) -> dict:
        """单事件标注(labeled/deferred/skip 三态)"""
        event_id = int(event.get("eventId") or 0)

        # 幂等: 终态已标注跳过(pending 可重评)
        existing = await self.repo.get_feedback_by_event(
            event_id)
        if existing is not None \
                and existing.get("status") == "labeled":
            return {"kind": "skip", "reason": "already_labeled"}

        # 信号判定(七类——优先级: 对抗>拦截分支>成功分支)
        signal = await self._label_signal(
            event, member_events)
        if signal.get("defer"):
            # 延迟态: upsert pending(不双写池)
            await self._upsert_pending(
                existing, event, signal["defer"])
            return {"kind": "deferred",
                    "reason": signal["defer"]}
        if signal.get("skip"):
            # 中间态事件(无终态标签)——已有 pending 不动
            return {"kind": "skip",
                    "reason": signal["skip"]}

        source = signal["source"]
        reward = SIGNAL_REWARDS[source]

        # 决策上下文重构+八因子快照(44号池配对数据源)
        ctx = self._build_ctx(event, member_events)
        scorer_result = await Login54Scorer().score(ctx)
        trust = float(scorer_result.get("trustScore") or 0)

        # 落 login54_feedback(upsert pending→labeled)
        record = await self._save_label(
            existing, event, source, reward, ctx, trust,
            observed=signal["observed"],
            expected=signal["expected"],
            evidence=signal.get("evidence") or [])

        # 44号池双写(学习闭环数据源)
        pool_feedback_id, pool_error = \
            await self._write_pool(
                event, source, reward, signal["expected"],
                signal["observed"], scorer_result)
        record["poolFeedbackId"] = pool_feedback_id or 0
        record["poolError"] = pool_error or ""
        await self.repo.save_feedback(
            record, create=existing is None)

        return {
            "kind": "labeled", "source": source,
            "reward": reward,
            "poolSubmitted": pool_feedback_id is not None,
            "poolFailed": pool_feedback_id is None,
        }

    # ============================================================
    # 七类信号判定(计划 §三真值标注口径)
    # ============================================================

    async def _label_signal(self, event: dict,
                            member_events: list) -> dict:
        """信号判定(优先级: S6 对抗→拦截分支(S4/S7)→
        成功分支(S3/S5/S1/S2)→中间态 skip)"""
        success = _truthy(event.get("success"))
        decision = str(event.get("decision") or "")
        detail = str(event.get("detail") or "")
        explain_ref = str(event.get("explainRef") or "")

        # S6 对抗样本(红队向量——detail/explainRef 标记)
        text = f"{detail} {explain_ref}".lower()
        if any(m.lower() in text
               for m in ADVERSARIAL_MARKERS):
            return {
                "source": "replay_theft",
                "observed": self._observed_tier(event),
                "expected": "enhanced",
                "evidence": [f"对抗标记命中: {detail[:40]}"],
            }

        # 拦截分支(S4 误拦修正 / S7 拦截正确)
        if decision in BLOCKED_DECISIONS and not success:
            appeal = await self._member_appeal_state(event)
            if appeal == "approved":
                return {
                    "source": "appeal_upheld",
                    "observed": "enhanced",
                    "expected": "silent",
                    "evidence": [
                        "43号申诉成立——误拦负反馈修正"],
                }
            if appeal == "pending":
                return {"defer": "pending_appeal"}
            return {
                "source": "block_correct",
                "observed": "enhanced",
                "expected": "enhanced",
                "evidence": [
                    f"拦截({decision})无申诉——拦截正确"],
            }

        # 成功分支(S3 失败切换 / S5 高危 / S1 驻留 / S2 弱驻留)
        if success:
            member_events = member_events or []
            # S3 凭证失败 N 次+切换通道成功(恶意倾向)
            streak = self._fail_switch_streak(
                event, member_events)
            if streak >= FAIL_SWITCH_THRESHOLD:
                return {
                    "source": "fail_switch",
                    "observed":
                        self._observed_tier(event),
                    "expected": "step_up",
                    "evidence": [
                        f"前序连续凭证失败 {streak} 次"
                        "(异通道)后切换成功——挑战正确"],
                }
            # S5 43号 riskFlagged 高危会员登录(人工兜底)
            if await self._is_risk_flagged(event):
                return {
                    "source": "risk_flagged",
                    "observed":
                        self._observed_tier(event),
                    "expected": "enhanced",
                    "evidence": [
                        "53号档案 riskFlagged 高危标记"],
                }
            # S1 驻留正当(retention 领取/导览)
            if await self._retention_claimed(event):
                return {
                    "source": "retention_dwell",
                    "observed":
                        self._observed_tier(event),
                    "expected":
                        self._observed_tier(event),
                    "evidence": [
                        "同日驻留已领取——正当(allow 正确)"],
                }
            # S2 弱驻留(5min 无行为) / T+1 延迟态
            age = self._age_seconds(event)
            if age is not None \
                    and age > DWELL_WINDOW_SECONDS:
                return {
                    "source": "weak_dwell",
                    "observed":
                        self._observed_tier(event),
                    "expected":
                        self._observed_tier(event),
                    "evidence": [
                        f"登录成功 {int(age)}s 无驻留"
                        "——弱正当"],
                }
            return {"defer": "pending_dwell"}

        # 中间态(credential_fail/step_up/one_tap 等)——skip
        return {"skip": decision or "unknown"}

    # --------------------------------------------------------
    # 信号源辅助判定(53号/43号 纯读取)
    # --------------------------------------------------------

    async def _member_appeal_state(self,
                                   event: dict) -> str:
        """43号申诉状态(会员级+时序过滤:
        createdAt >= 事件时间的申诉才相关)"""
        try:
            from repositories.security_repository import (
                Security43Repository,
            )
            member_id = int(event.get("memberId") or 0)
            appeals = await Security43Repository(
            ).list_appeals(member_id=member_id,
                           limit=100)
            created = _ts_utc(
                str(event.get("createdAt") or ""))
            relevant = []
            for a in appeals:
                appeal_ts = _ts_utc(
                    str(a.get("createdAt") or ""))
                if created is None or appeal_ts is None:
                    relevant.append(a)   # 解析失败兜底
                elif appeal_ts >= created:
                    relevant.append(a)
            statuses = {str(a.get("status") or "")
                        for a in relevant}
            if "approved" in statuses:
                return "approved"
            if "pending" in statuses:
                return "pending"
            return "none"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "login54_appeal_lookup_failed: %s", exc)
            return "none"

    async def _is_risk_flagged(self, event: dict) -> bool:
        """53号档案 riskFlagged 高危标记(43号侧门)"""
        try:
            from repositories.login53_repository import (
                Login53Repository,
            )
            member_id = int(event.get("memberId") or 0)
            profile = await Login53Repository(
            ).get_profile(member_id)
            flag = (profile or {}).get("riskFlagged")
            return bool(flag or flag == 1)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "login54_riskflag_lookup_failed: %s", exc)
            return False

    async def _retention_claimed(self, event: dict) -> bool:
        """53号驻留台账同日领取(驻留正当信号)"""
        try:
            from repositories.login53_repository import (
                Login53Repository,
            )
            member_id = int(event.get("memberId") or 0)
            # dayKey 归一 UTC(53号 ts()[:10] 同口径)
            created = _ts_utc(
                str(event.get("createdAt") or ""))
            day_key = created.strftime(
                "%Y-%m-%d") if created else \
                str(event.get("createdAt") or "")[:10]
            if not day_key:
                return False
            ret = await Login53Repository().get_retention(
                member_id, day_key)
            return ret is not None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "login54_retention_lookup_failed: %s", exc)
            return False

    @staticmethod
    def _fail_switch_streak(event: dict,
                            member_events: list) -> int:
        """前序连续凭证失败次数(全部异通道——
        '失败 N 次+切换通道成功'因果链)"""
        event_id = int(event.get("eventId") or 0)
        method = str(event.get("method") or "")
        streak = 0
        for prev in reversed(member_events):
            if int(prev.get("eventId") or 0) >= event_id:
                continue
            if str(prev.get("decision") or "") \
                    != "credential_fail":
                break
            if str(prev.get("method") or "") == method:
                break   # 同通道失败非切换
            streak += 1
        return streak

    @staticmethod
    def _age_seconds(event: dict) -> float | None:
        """事件年龄(秒——驻留窗口判定; UTC 归一)"""
        from datetime import timezone
        created = _ts_utc(event.get("createdAt"))
        if created is None:
            return None
        now = datetime.now(timezone.utc)
        return max(0.0,
                   (now - created).total_seconds())

    @staticmethod
    def _observed_tier(event: dict) -> str:
        """观测档位(完成态事件 decision 即档位;
        异常值归 silent——保守记录)"""
        decision = str(event.get("decision") or "")
        return decision if decision in TIERS else "silent"

    # ============================================================
    # 决策上下文重构(53号 events 历史→八因子 ctx)
    # ============================================================

    @staticmethod
    def _build_ctx(event: dict,
                   member_events: list) -> dict:
        """从会员事件历史重构决策上下文(标注时点近似——
        53号事件不带因子快照, 聚合历史还原八因子输入)"""
        event_id = int(event.get("eventId") or 0)
        method = str(event.get("method") or "")
        prior = [e for e in (member_events or [])
                 if int(e.get("eventId") or 0) < event_id]

        # 通道历史成功率(Laplace 平滑防新通道归零)
        on_channel = [e for e in prior
                      if str(e.get("method") or "")
                      == method]
        succ = sum(1 for e in on_channel
                   if _truthy(e.get("success")))
        channel_success = (succ + 1) / (len(on_channel) + 2)

        # 同通道失败计数(credential_fail)
        fail_count = sum(
            1 for e in on_channel
            if str(e.get("decision") or "")
            == "credential_fail")

        # 账龄(首事件至当天数; 单事件=1 天; UTC 归一)
        timestamps = [
            _ts_utc(e.get("createdAt"))
            for e in (prior + [event])]
        timestamps = [t for t in timestamps if t]
        age_days = 1
        if len(timestamps) >= 2:
            span = (max(timestamps)
                    - min(timestamps)).total_seconds()
            age_days = max(1, int(span / 86400) + 1)

        return {
            "channelSuccess": round(channel_success, 3),
            "channel": method,
            "baselineMatch": 0.5,    # 设备态未知→中性
            "budgetRemaining": 0.5,  # 预算未探→中性
            "accountAgeDays": age_days,
            "loginFrequency": len(prior) + 1,
            "channelFailCount": fail_count,
            "portalState": "active",  # 基线(高危已由信号覆盖)
        }

    # ============================================================
    # 落库与双写
    # ============================================================

    async def _save_label(self, existing: dict | None,
                          event: dict, source: str,
                          reward: float, ctx: dict,
                          trust: float, observed: str,
                          expected: str,
                          evidence: list) -> dict:
        """login54_feedback 落库(pending 转正复用
        feedbackId——索引不重复入列)"""
        if existing is not None:
            record = dict(existing)
            record.update({
                "source": source, "reward": reward,
                "trustScore": trust,
                "observedTier": observed,
                "expectedTier": expected,
                "status": "labeled",
                "evidence": evidence,
                "context": ctx,
                "labeledAt": ts(),
            })
            await self.repo.save_feedback(
                record, create=False)
            return record
        feedback_id = await self.repo.next_feedback_id()
        record = {
            "feedbackId": feedback_id,
            "eventId": int(event.get("eventId") or 0),
            "memberId": int(event.get("memberId") or 0),
            "source": source,
            "sourceName": SIGNAL_NAMES.get(source, source),
            "reward": reward,
            "trustScore": trust,
            "observedTier": observed,
            "expectedTier": expected,
            "status": "labeled",
            "evidence": evidence,
            "context": ctx,
            "poolFeedbackId": 0,
            "poolError": "",
            "createdAt": ts(),
            "labeledAt": ts(),
        }
        await self.repo.save_feedback(record)
        return record

    async def _upsert_pending(self, existing: dict | None,
                              event: dict,
                              defer: str) -> None:
        """延迟态 upsert(T+1 重扫——不双写池)"""
        if existing is not None:
            existing["status"] = defer
            await self.repo.save_feedback(
                existing, create=False)
            return
        feedback_id = await self.repo.next_feedback_id()
        await self.repo.save_feedback({
            "feedbackId": feedback_id,
            "eventId": int(event.get("eventId") or 0),
            "memberId": int(event.get("memberId") or 0),
            "source": defer,
            "sourceName": defer,
            "reward": 0.0,
            "trustScore": 0.0,
            "status": defer,
            "evidence": [],
            "context": {},
            "poolFeedbackId": 0,
            "poolError": "",
            "createdAt": ts(),
        })

    async def _write_pool(self, event: dict, source: str,
                          reward: float, expected: str,
                          observed: str,
                          scorer_result: dict) -> tuple:
        """44号反馈池双写(submit_feedback——学习闭环
        数据源; 返回 (poolFeedbackId|None, error))"""
        try:
            from services.ai_learning_service import (
                submit_feedback,
            )
            result = await submit_feedback({
                "scorerId": SCORER_ID,
                "factors": scorer_result.get("factors") or [],
                "scoreAtDecision": float(
                    scorer_result.get("trustScore") or 0),
                "actualAction": observed,
                "expectedAction": expected,
                "correct": observed == expected,
                "reward": reward,
                "note": f"login54:{source}:eventId="
                        f"{event.get('eventId')}",
                "source": "login54_pipeline",
            })
            return result.get("feedbackId"), ""
        except Exception as exc:  # noqa: BLE001
            # fail-soft: 池双写失败不阻断标注本体
            logger.warning(
                "login54_pool_write_failed event=%s: %s",
                event.get("eventId"), exc)
            return None, str(exc)[:80]

    # ============================================================
    # 回流统计(观测面)
    # ============================================================

    async def feedback_stats(self) -> dict:
        """回流统计(标注分布/样本量/池双写/延迟态)"""
        records = await self.repo.list_feedback(
            limit=1000)
        by_source: dict = {}
        by_status: dict = {}
        positive = negative = pooled = 0
        for r in records:
            source = str(r.get("source") or "unknown")
            by_source[source] = \
                by_source.get(source, 0) + 1
            status = str(r.get("status") or "unknown")
            by_status[status] = \
                by_status.get(status, 0) + 1
            reward = float(r.get("reward") or 0)
            if reward > 0:
                positive += 1
            elif reward < 0:
                negative += 1
            if int(r.get("poolFeedbackId") or 0) > 0:
                pooled += 1
        return {
            "success": True,
            "total": len(records),
            "labeled": by_status.get("labeled", 0),
            "pending": {
                "dwell": by_status.get(
                    "pending_dwell", 0),
                "appeal": by_status.get(
                    "pending_appeal", 0),
            },
            "bySource": by_source,
            "byStatus": by_status,
            "rewardSplit": {
                "positive": positive,
                "negative": negative,
            },
            "poolSubmitted": pooled,
            "signalRewards": SIGNAL_REWARDS,
            "note": "七类信号源真值标注——44号池双写"
                    "(学习闭环数据源)",
            "generatedAt": ts(),
        }
