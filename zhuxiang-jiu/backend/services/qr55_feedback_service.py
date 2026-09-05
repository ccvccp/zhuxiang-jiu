"""55号·二维码AI智能管理 决策回流管道
(qr55_feedback_service, P2)

计划(docs/55号_二维码AI智能管理模块实施计划.md §六):
    七类真值信号源(qr55_events 事件链判定——零侵入纯扫描):

    | 信号源                     | reward | 语义            |
    |---------------------------|--------|-----------------|
    | 生码后扫码+服务完成          | +1.0   | 意图满足(生成正确) |
    | 生码后扫码未完成(放弃)       | +0.3   | 弱满足            |
    | 澄清后命中(二次即中)        | +0.8   | 澄清有效          |
    | 澄清≥3轮才命中/放弃         | -0.6   | 澄清低效          |
    | 生码后过期未扫              | -0.4   | 生成过剩          |
    | 验签失败(篡改)             | -1.0   | 对抗样本          |
    | 拨测失败重试成功            | -0.5   | 路由劣化(P4预置)  |

回流时机(54号 P1 范式全继承):
    - collect 可在事件落库后随时调用(幂等扫描——已标注
      事件跳过, 47号 scan 幂等教训)
    - T+1 批次补标: 扫码未完成/澄清未跟进延时可得 →
      pending_completion/pending_clarify 延迟态 +
      重扫转正(qr55_scheduler 调度)

44号反馈池双写(计划 §四):
    每条终态标注 → submit_feedback(qr_orchestration)
    走 44号既有 record 接口——第30档案学习闭环数据源。
    决策上下文从码实例+会员画像重构(八因子快照——标注
    时点近似, 54号 _build_ctx 同范式)。

信值结算联动 45号(计划 §六 P2 全链: 生成→扫码→完成→
信值结算):
    scan_completed 终态按会员聚合 → TrustRadarService
    .submit_deposit(L3 longtail_good 长尾正向——微小
    但高频的服务完成), 走 45号验真三道关管线(双源交叉
    +因果净贡献; 50号 T+1 结算桥接同范式)。

设计红线:
    - 44/45/49号零改动(纯调用式接入)
    - 幂等铁律: eventId 1:1——labeled 终态永不重标,
      pending 可重评转正(不重复入池/结算)
    - fail-soft: 单事件标注异常跳过留痕, 不阻断批量
"""

import logging
import time
from datetime import datetime, timezone

from core.helpers import ts

from repositories.qr55_repository import (
    Qr55Repository,
)

logger = logging.getLogger("qr55_feedback_service")

MODEL_VERSION = "v1-qr55-feedback"

SCORER_ID = "qr_orchestration"

# 七类信号源 → reward(计划 §六真值标注口径)
SIGNAL_REWARDS = {
    "scan_completed": 1.0,        # 扫码+服务完成(意图满足)
    "scan_abandoned": 0.3,        # 扫码未完成(弱满足)
    "clarify_hit": 0.8,            # 澄清后命中(二次即中)
    "clarify_inefficient": -0.6,   # 澄清≥3轮/放弃(低效)
    "expired_unscanned": -0.4,    # 过期未扫(生成过剩)
    "tamper_detected": -1.0,      # 验签失败(对抗样本)
    "probe_retry": -0.5,          # 拨测失败重试成功(P4预置)
}

SIGNAL_NAMES = {
    "scan_completed": "扫码完成(意图满足)",
    "scan_abandoned": "扫码未完成(弱满足)",
    "clarify_hit": "澄清命中(二次即中)",
    "clarify_inefficient": "澄清低效(≥3轮/放弃)",
    "expired_unscanned": "过期未扫(生成过剩)",
    "tamper_detected": "篡改(对抗样本)",
    "probe_retry": "拨测重试(路由劣化)",
}

# 负修正信号的期望策略(学习口径——生成过剩该澄清、
# 篡改环境该保守; 正向信号期望=观测)
EXPECTED_OVERRIDE = {
    "clarify_inefficient": "confirm",
    "expired_unscanned": "clarify",
    "tamper_detected": "clarify",
    "probe_retry": "confirm",
}

# 完成判定窗口(扫码后 24h 未完成 → 弱满足转正——T+1)
COMPLETION_WINDOW_SECONDS = 86400
# 澄清跟进窗口(24h 无后续编排 → 放弃——T+1)
CLARIFY_WINDOW_SECONDS = 86400

# 编排尝试事件(澄清链判定域)
ORCHESTRATE_EVENTS = ("generate", "confirm", "clarify")


def _truthy(value) -> bool:
    """健壮布尔解析(Redis 态 '1'/'0' 字符串)"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in (
        "1", "true", "yes")


def _ts_utc(value) -> datetime | None:
    """时间戳统一 UTC(混合时区不可字典序比较——
    解析后比较; 无时区按 UTC)"""
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_seconds(event: dict) -> float | None:
    """事件年龄(秒——窗口判定; UTC 归一)"""
    created = _ts_utc(event.get("createdAt"))
    if created is None:
        return None
    now = datetime.now(timezone.utc)
    return max(0.0, (now - created).total_seconds())


class Qr55FeedbackService:
    """55号决策回流管道(七类信号→reward→44号池双写
    +45号信值结算)"""

    def __init__(self):
        self.repo = Qr55Repository()

    # ============================================================
    # 回流标注入口(collect——幂等批量扫描)
    # ============================================================

    async def collect_feedback(self,
                               member_id: int = None,
                               limit: int = 500) -> dict:
        """触发一轮回流标注(qr55_events 扫描→七类信号→
        qr55_feedback 落库 + 44号池双写 + 45号信值结算)

        幂等铁律: eventId 1:1——labeled 终态跳过,
        pending(pending_completion/pending_clarify)
        重评转正(不重复入池/结算)。

        注: 回流采集不依赖 QR55_MODE(管理面——off 态
        亦可补标, 54号 collect 同范式)。
        """
        events = await self.repo.list_events(limit=limit)
        # 时序升序(澄清链/因果序判定依赖)
        events.sort(key=lambda e: int(
            e.get("eventId") or 0))
        if member_id is not None:
            events = [e for e in events
                      if int(e.get("memberId") or 0)
                      == int(member_id)]

        # 索引: 按码(生成链)/按会员(澄清链)
        by_code: dict[int, list] = {}
        by_member: dict[int, list] = {}
        for event in events:
            by_code.setdefault(int(
                event.get("codeId") or 0),
                []).append(event)
            by_member.setdefault(int(
                event.get("memberId") or 0),
                []).append(event)

        summary = {
            "scanned": len(events), "labeled": 0,
            "deferred": 0, "skipped": 0,
            "poolSubmitted": 0, "poolFailed": 0,
            "signals": {}, "errors": [],
            "settled": 0, "collectedAt": ts(),
        }
        completed_labels: dict[int, list] = {}

        for event in events:
            try:
                outcome = await self._process_event(
                    event,
                    by_code.get(int(
                        event.get("codeId") or 0), []),
                    by_member.get(int(
                        event.get("memberId") or 0),
                        []))
                kind = outcome.get("kind")
                if kind == "labeled":
                    summary["labeled"] += 1
                    source = outcome["source"]
                    summary["signals"][source] = \
                        summary["signals"].get(
                            source, 0) + 1
                    if outcome.get("poolSubmitted"):
                        summary["poolSubmitted"] += 1
                    elif outcome.get("poolFailed"):
                        summary["poolFailed"] += 1
                    # 信值结算聚合(scan_completed 按会员)
                    if source == "scan_completed":
                        completed_labels.setdefault(
                            int(event.get("memberId")
                                or 0),
                            []).append(event)
                elif kind == "deferred":
                    summary["deferred"] += 1
                else:
                    summary["skipped"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(
                    f"event={event.get('eventId')}:"
                    f"{str(exc)[:60]}")
                logger.warning(
                    "qr55_label_failed event=%s: %s",
                    event.get("eventId"), exc)

        # 信值结算联动 45号(scan_completed 聚合→
        # L3 长尾正向 deposit 验真)
        summary["settled"] = await self._settle_trust(
            completed_labels)

        # 模型事件留痕(版本溯源)
        try:
            from services.qr55_service import (
                Qr55Service,
            )
            await Qr55Service().record_model_event(
                "feedback_collect", {
                    "scanned": summary["scanned"],
                    "labeled": summary["labeled"],
                    "deferred": summary["deferred"],
                    "skipped": summary["skipped"],
                    "poolSubmitted":
                        summary["poolSubmitted"],
                    "settled": summary["settled"],
                    "signals": summary["signals"],
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_collect_event_failed: %s", exc)

        summary["success"] = True
        summary["note"] = ("决策回流幂等扫描——七类信号"
                           "真值标注+44号池双写+45号信值结算")
        return summary

    # ============================================================
    # 单事件处理(信号判定→落库→双写)
    # ============================================================

    async def _process_event(self, event: dict,
                             code_events: list,
                             member_events: list
                             ) -> dict:
        """单事件标注(labeled/deferred/skip 三态)"""
        event_id = int(event.get("eventId") or 0)
        event_type = str(event.get("eventType") or "")

        # 幂等: 终态已标注跳过(pending 可重评)
        existing = await \
            self.repo.get_feedback_by_event(event_id)
        if existing is not None \
                and existing.get("status") == "labeled":
            return {"kind": "skip",
                    "reason": "already_labeled"}

        # 信号判定(事件类型分派)
        if event_type == "generate":
            signal = await self._label_generate_chain(
                event, code_events)
        elif event_type == "clarify":
            signal = self._label_clarify_chain(
                event, member_events)
        elif event_type == "tamper":
            signal = {
                "source": "tamper_detected",
                "observed": "direct",
                "evidence": ["验签失败——对抗样本"],
            }
        elif event_type == "probe":
            detail = event.get("detail") or {}
            if not _truthy(detail.get("retrySucceeded")):
                return {"kind": "skip",
                        "reason": "probe_no_retry"}
            signal = {
                "source": "probe_retry",
                "observed": "direct",
                "evidence": ["拨测失败重试成功——路由劣化"],
            }
        else:
            # scan/expire/replay/complete/confirm/settle——
            # 证据事件(由 generate/clarify 锚点消费)
            return {"kind": "skip",
                    "reason": f"evidence_event:"
                              f"{event_type or 'unknown'}"}

        if signal.get("defer"):
            await self._upsert_pending(
                existing, event, signal["defer"])
            return {"kind": "deferred",
                    "reason": signal["defer"]}
        if signal.get("skip"):
            return {"kind": "skip",
                    "reason": signal["skip"]}

        source = signal["source"]
        reward = SIGNAL_REWARDS[source]
        observed = signal["observed"]
        expected = EXPECTED_OVERRIDE.get(source) \
            or observed

        # 决策上下文重构+八因子快照(44号池配对数据源)
        ctx = await self._build_ctx(event, source)
        from services.qr55_scorer import Qr55Scorer
        scorer_result = await Qr55Scorer().score(ctx)
        trust = float(scorer_result.get("trustScore")
                      or 0)

        record = await self._save_label(
            existing, event, source, reward, ctx, trust,
            observed=observed, expected=expected,
            evidence=signal.get("evidence") or [])

        # 44号池双写(学习闭环数据源)
        pool_feedback_id, pool_error = \
            await self._write_pool(
                event, source, reward, observed, expected,
                scorer_result)
        record["poolFeedbackId"] = \
            pool_feedback_id or 0
        record["poolError"] = pool_error or ""
        # 二次落库仅更新池字段(create=False——
        # _save_label 已入列, 重复 lpush 会使
        # Redis 态列表出现重复项)
        await self.repo.save_feedback(
            record, create=False)

        return {
            "kind": "labeled", "source": source,
            "reward": reward,
            "poolSubmitted": pool_feedback_id is not None,
            "poolFailed": pool_feedback_id is None,
        }

    # ============================================================
    # 生成链信号(意图满足/弱满足/生成过剩)
    # ============================================================

    async def _label_generate_chain(self, event: dict,
                                    code_events: list
                                    ) -> dict:
        """generate 锚点: 码实例生命周期判定

        扫码+完成→意图满足; 扫码未完成(T+1 窗口)→
        弱满足; 过期未扫(expire 事件/状态/时点)→
        生成过剩; 仍活跃→skip 待下轮。
        """
        code_id = int(event.get("codeId") or 0)

        # 事件链证据(同码时序)
        chain = [e for e in code_events
                 if int(e.get("eventId") or 0)
                 >= int(event.get("eventId") or 0)]
        scanned = next((e for e in chain
                        if e.get("eventType")
                        == "scan"), None)
        completed = next((e for e in chain
                          if e.get("eventType")
                          == "complete"), None)
        expired = next((e for e in chain
                        if e.get("eventType")
                        == "expire"), None)

        detail = event.get("detail") or {}
        observed = "confirm" \
            if str(detail.get("strategy") or "") \
            == "confirm_done" else "direct"

        if completed is not None:
            return {
                "source": "scan_completed",
                "observed": observed,
                "evidence": [
                    f"生成→扫码→完成全链闭环"
                    f"(codeId={code_id})"],
            }
        if scanned is not None:
            age = _age_seconds(scanned)
            if age is not None and age \
                    > COMPLETION_WINDOW_SECONDS:
                return {
                    "source": "scan_abandoned",
                    "observed": observed,
                    "evidence": [
                        f"扫码后 {int(age)}s 未完成服务"
                        f"——弱满足(codeId={code_id})"],
                }
            return {"defer": "pending_completion"}

        # 未扫码: 过期判定(事件/状态/exp 时点三源)
        code_rec = await self.repo.get_code(code_id) \
            if code_id else None
        past_exp = False
        if code_rec is not None:
            try:
                past_exp = int(code_rec.get("expiresAt")
                               or 0) < time.time()
            except (TypeError, ValueError):
                past_exp = False
        if expired is not None \
                or (code_rec is not None
                    and code_rec.get("status")
                    == "expired") \
                or past_exp:
            return {
                "source": "expired_unscanned",
                "observed": observed,
                "evidence": [
                    f"码过期未扫——生成过剩"
                    f"(codeId={code_id})"],
            }
        return {"skip": "still_active"}

    # ============================================================
    # 澄清链信号(澄清命中/低效)
    # ============================================================

    def _label_clarify_chain(self, event: dict,
                             member_events: list) -> dict:
        """clarify 锚点(链首): 后续编排命中判定

        链首=前一编排尝试非 clarify; 链=链首起连续
        clarify 数; 终止=generate/confirm(命中)。
        链长 1(二次即中)→命中 +0.8; ≥2(≥3轮)→
        低效 -0.6; 无终止超窗→放弃(低效)。
        """
        event_id = int(event.get("eventId") or 0)

        # 链首判定: 之前的最近编排尝试是否 clarify
        prior = [
            e for e in member_events
            if int(e.get("eventId") or 0) < event_id
            and e.get("eventType")
            in ORCHESTRATE_EVENTS]
        if prior and prior[-1].get("eventType") \
                == "clarify":
            return {"skip": "chain_continuation"}

        # 后续编排尝试(时序升序)
        subsequent = [
            e for e in member_events
            if int(e.get("eventId") or 0) > event_id
            and e.get("eventType")
            in ORCHESTRATE_EVENTS]

        # 链长: 链首起连续 clarify 数(含链首)
        run_len = 1
        idx = 0
        while idx < len(subsequent) \
                and subsequent[idx].get("eventType") \
                == "clarify":
            run_len += 1
            idx += 1

        if idx < len(subsequent):
            # generate/confirm 终止——命中
            if run_len == 1:
                return {
                    "source": "clarify_hit",
                    "observed": "clarify",
                    "evidence": [
                        "澄清后二次即中——澄清有效"],
                }
            return {
                "source": "clarify_inefficient",
                "observed": "clarify",
                "evidence": [
                    f"澄清 {run_len} 轮才命中"
                    f"(≥3轮——低效)"],
            }

        # 无终止编排: T+1 窗口判放弃
        age = _age_seconds(event)
        if age is not None \
                and age > CLARIFY_WINDOW_SECONDS:
            return {
                "source": "clarify_inefficient",
                "observed": "clarify",
                "evidence": [
                    f"澄清后 {int(age)}s 无跟进"
                    f"——放弃(低效)"],
            }
        return {"defer": "pending_clarify"}

    # ============================================================
    # 上下文重构(标注时点近似——54号 _build_ctx 范式)
    # ============================================================

    async def _build_ctx(self, event: dict,
                         source: str) -> dict:
        """从码实例+会员画像重构决策上下文(八因子)"""
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        member_id = int(event.get("memberId") or 0)

        grade = await Qr55GenerateService._member_grade(
            member_id)
        budget = await \
            Qr55GenerateService._budget_remaining(
                member_id)
        level = Qr55GenerateService._grade_to_level(
            grade)

        if source in ("scan_completed",
                      "scan_abandoned",
                      "expired_unscanned"):
            # 生成决策上下文(已 resolved——近似)
            code_id = int(event.get("codeId") or 0)
            accessibility = False
            params = {}
            if code_id:
                code_rec = await self.repo.get_code(
                    code_id)
                if code_rec:
                    accessibility = bool(
                        code_rec.get("accessibility"))
                    params = code_rec.get("params") or {}
            return {
                "intentConfidence": 0.8,
                "serviceMatch": "resolved",
                "paramComplete":
                    1.0 if params else 0.5,
                "budgetRemaining": budget,
                "memberTrustLevel": level,
                "accessibility": accessibility,
                "riskFlagged": False,
            }
        # clarify/tamper/probe 决策上下文
        return {
            "intentConfidence": 0.2,
            "serviceMatch": "clarify",
            "paramComplete": 0.5,
            "budgetRemaining": budget,
            "memberTrustLevel": level,
            "accessibility": None,
            "riskFlagged": source == "tamper_detected",
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
        """qr55_feedback 落库(pending 转正复用
        feedbackId——索引不重复入列)"""
        if existing is not None:
            record = dict(existing)
            record.update({
                "source": source,
                "sourceName":
                    SIGNAL_NAMES.get(source, source),
                "reward": reward,
                "trustScore": trust,
                "observedStrategy": observed,
                "expectedStrategy": expected,
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
            "sourceName":
                SIGNAL_NAMES.get(source, source),
            "reward": reward,
            "trustScore": trust,
            "observedStrategy": observed,
            "expectedStrategy": expected,
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

    async def _upsert_pending(self,
                              existing: dict | None,
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
                          reward: float, observed: str,
                          expected: str,
                          scorer_result: dict) -> tuple:
        """44号反馈池双写(submit_feedback——第30档案
        学习闭环数据源; 返回 (poolFeedbackId|None,
        error))"""
        try:
            from services.ai_learning_service import (
                submit_feedback,
            )
            result = await submit_feedback({
                "scorerId": SCORER_ID,
                "factors":
                    scorer_result.get("factors") or [],
                "scoreAtDecision": float(
                    scorer_result.get("trustScore") or 0),
                "actualAction": observed,
                "expectedAction": expected,
                "correct": observed == expected,
                "reward": reward,
                "note": f"qr55:{source}:eventId="
                        f"{event.get('eventId')}",
                "source": "qr55_pipeline",
            })
            return result.get("feedbackId"), ""
        except Exception as exc:  # noqa: BLE001
            # fail-soft: 池双写失败不阻断标注本体
            logger.warning(
                "qr55_pool_write_failed event=%s: %s",
                event.get("eventId"), exc)
            return None, str(exc)[:80]

    # ============================================================
    # 信值结算联动 45号(scan_completed 聚合→
    # L3 长尾正向 deposit 验真——50号 T+1 桥接范式)
    # ============================================================

    async def _settle_trust(self,
                            completed_labels: dict
                            ) -> int:
        """按会员聚合 scan_completed → 45号 deposit 申报

        - 会员维度聚合一次申报(长尾正向——微小但高频)
        - 幂等: 标注 eventId 1:1(本轮新转正终态才结算,
          已标注跳过——不重复申报)
        - fail-soft: 档案缺失/验真拒收 → settle 事件
          留痕跳过(不阻断批量)
        """
        settled = 0
        for member_id, labels in \
                sorted(completed_labels.items()):
            if not member_id:
                continue
            count = len(labels)
            code_ids = [str(e.get("codeId") or 0)
                        for e in labels[:5]]
            evidence = (
                f"qr55 T+1 扫码完成结算(会员 {member_id}, "
                f"{count} 笔服务完成, codeIds: "
                f"{';'.join(code_ids)})")
            summary = (f"二维码服务完成聚合结算"
                      f"({count} 笔, 55号回流管道)")
            deposit = None
            reason = ""
            try:
                from services.trust_radar_service import (
                    TrustRadarService,
                )
                deposit = await TrustRadarService(
                ).submit_deposit(
                    int(member_id), "L3",
                    "longtail_good",
                    observed=float(count),
                    peer_baseline=0.0,
                    evidence=evidence,
                    summary=summary,
                    sources=["qr55_pipeline",
                             "event_audit"],
                    voluntary=False,
                    verify_mode="v1")
            except Exception as exc:  # noqa: BLE001
                reason = f"deposit_failed:{str(exc)[:60]}"
                logger.warning(
                    "qr55_settle_deposit_failed "
                    "member=%s: %s", member_id, exc)

            if deposit is not None \
                    and deposit.get("verified"):
                settled += 1
            elif not reason:
                reason = "deposit_unverified"

            # settle 事件留痕(信值结算链可观测)
            try:
                event_id = await self.repo.next_event_id()
                await self.repo.add_event({
                    "eventId": event_id,
                    "codeId": 0,
                    "memberId": int(member_id),
                    "eventType": "settle",
                    "detail": {
                        "source": "scan_completed",
                        "completedCount": count,
                        "depositId":
                            (deposit or {}).get(
                                "depositId") or 0,
                        "depositVerified": bool(
                            (deposit or {}).get(
                                "verified")),
                        "depositDelta": float(
                            (deposit or {}).get(
                                "delta") or 0),
                        "reason": reason,
                    },
                    "createdAt": ts(),
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "qr55_settle_event_failed: %s", exc)
        return settled

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
                "completion": by_status.get(
                    "pending_completion", 0),
                "clarify": by_status.get(
                    "pending_clarify", 0),
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
                    "+45号信值结算",
            "generatedAt": ts(),
        }
