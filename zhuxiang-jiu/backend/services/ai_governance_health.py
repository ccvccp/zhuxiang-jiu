"""46号·AI 治理与合规中枢 P1 档案健康度监控
(三检测器 + 群体健康分 + 巡检快照 + 治理告警)

计划(docs/46号_AI治理与合规中枢实施计划.md §四):
    ① 三大检测器(确定性规则, 非评分器——治理健康是
       确定性聚合, 不入 Hedge, 不新增第29档案):
        - 学习停滞: 距最近一次版本事件 ≥30 天且近 30 日
          有反馈(有数据却无版本演进 = 学不动了);
          无任何版本事件时以最早反馈时间为观察起点
        - 反馈枯竭: 近 30 日新增反馈 < min_feedback
          (学不动); 从未有过反馈的档案不判枯竭
          (未启动 ≠ 枯竭, 防全新部署全员误报)
        - 漂移高: ai_learning drift.driftLevel == "high"
          (既有漂移统计口径, 43号阈值)
    ② 群体健康分(聚合视图指标):
        健康分 = 100 − 停滞40 − 枯竭30 − 漂移30(命中扣满)
        分层: ≥90 healthy / ≥60 watch / <60 risk
    ③ 治理告警(43号 P6-2 三信号范式):
        采集 → 去重(同档案同信号当日一条) → 队列呈现;
        fail-soft 隔离(单档案采集失败不阻断其余,
        单信号数据源失败仅记 error 不阻断其余信号)

设计铁律:
    - 治理不阻断: 健康巡检任何异常不抛出——降级为
      entry.errors 留痕(43号告警 fail-soft 同款)
    - 数字来自数据层: 检测输入全部来自 ai_learning
      真实存储(profile/history/config/drift/feedback)
    - 只读检测: 不初始化未加载的档案(不调用
      _load_profile——健康巡检零副作用)
"""

import logging
from datetime import UTC, datetime, timedelta

from core.helpers import ts

from repositories.ai_governance_repository import (
    AiGovernance46Repository,
)
from repositories.ai_learning_repository import (
    AiLearningRepository,
)

logger = logging.getLogger(__name__)

# 检测阈值(计划 §四)
STAGNATION_DAYS = 30     # 停滞: 距最近版本事件天数
DEPLETION_WINDOW_DAYS = 30   # 枯竭: 反馈观察窗(天)
PENALTY = {"stagnation": 40, "depletion": 30,
           "drift_high": 30}   # 健康分扣减(命中扣满)

# 健康分层(≥90 绿 / ≥60 黄 / <60 红)
HEALTH_LEVELS = ((90, "healthy"), (60, "watch"), (0, "risk"))

# 信号展示名(告警消息/面板渲染)
SIGNAL_NAMES = {"stagnation": "学习停滞",
                "depletion": "反馈枯竭",
                "drift_high": "因子漂移高"}


def _parse_dt(value):
    """ISO8601 → datetime(失败返回 None)"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _age_days(created, now: datetime):
    """时间距今天数(不可解析返回 None)"""
    dt = _parse_dt(created)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (now - dt).total_seconds() / 86400.0


class AiGovernanceHealthService:
    """档案健康度监控(46号 P1: 三检测器+健康分+巡检+告警)"""

    def __init__(self,
                 repo: AiGovernance46Repository = None,
                 learn_repo: AiLearningRepository = None):
        self.repo = repo or AiGovernance46Repository()
        self.learn = learn_repo or AiLearningRepository()

    # --------------------------------------------------------
    # 数据采集(逐项 fail-soft——单信号源失败不阻断其余)
    # --------------------------------------------------------

    async def _collect(self, scorer_id: str) -> dict:
        """采集单档案检测输入(每项独立容错)

        Returns:
            {profile, history, config, drift,
             feedbackTimes, versionEventTimes, errors}
        """
        data = {"profile": None, "history": [],
                "config": {}, "drift": None,
                "feedbackTimes": [],
                "versionEventTimes": [],
                "errors": []}

        async def _safe(name, fn, key):
            try:
                data[key] = await fn()
            except Exception as exc:
                data["errors"].append(name)
                logger.warning("ai46_health_collect_skip "
                               "%s %s: %s", scorer_id, name, exc)

        await _safe("profile",
                    lambda: self.learn.get_profile(scorer_id),
                    "profile")
        await _safe("history",
                    lambda: self.learn.list_history(
                        scorer_id, limit=100),
                    "history")
        await _safe("config",
                    lambda: self._read_config(scorer_id),
                    "config")
        await _safe("drift",
                    lambda: self.learn.get_drift(scorer_id),
                    "drift")
        await _safe("feedback",
                    lambda: self.learn.list_feedback(
                        scorer_id, limit=0),
                    "feedback")
        feedback = data.pop("feedback", [])
        if isinstance(feedback, list):
            data["feedbackTimes"] = [
                f.get("createdAt") for f in feedback
                if isinstance(f, dict) and f.get("createdAt")]
        else:
            data["feedbackTimes"] = []

        # 版本事件时间 = 冠军/挑战者/历史全部版本记录的
        # createdAt(学习产出挑战者亦为版本演进——学习在
        # 转即不停滞)
        events = []
        profile = data.get("profile") or {}
        for role in ("champion", "challenger"):
            rec = profile.get(role) or {}
            if rec.get("createdAt"):
                events.append(rec.get("createdAt"))
        for rec in (data.get("history") or []):
            if isinstance(rec, dict) and rec.get("createdAt"):
                events.append(rec.get("createdAt"))
        data["versionEventTimes"] = events
        return data

    async def _read_config(self, scorer_id: str) -> dict:
        from services.ai_learning_service import (
            DEFAULT_LEARNING_CONFIG,
        )
        cfg = await self.learn.get_config(scorer_id)
        return {**DEFAULT_LEARNING_CONFIG, **(cfg or {})}

    # --------------------------------------------------------
    # 三检测器(纯函数——确定性规则)
    # --------------------------------------------------------

    def detect_stagnation(self, data: dict,
                          now: datetime) -> tuple:
        """学习停滞: 距最近版本事件 ≥30 天且期间有反馈

        无任何版本事件时以最早反馈为观察起点(数据积累
        超 30 天未产出版本亦为停滞)。
        """
        window = timedelta(days=DEPLETION_WINDOW_DAYS)
        feedback_30d = sum(
            1 for t in data["feedbackTimes"]
            if (_parse_dt(t) or now) >= now - window)
        events = [_parse_dt(t)
                  for t in data["versionEventTimes"]
                  if _parse_dt(t) is not None]
        parsed_feedback = [
            dt for dt in (_parse_dt(t)
                          for t in data["feedbackTimes"])
            if dt is not None]
        last_event_str = max(
            (t for t in data["versionEventTimes"
                             ] if _parse_dt(t) is not None),
            default=None)
        if events:
            days = (now - max(events)).total_seconds() / 86400.0
        elif parsed_feedback:
            days = ((now - min(parsed_feedback))
                    .total_seconds() / 86400.0)
        else:
            days = None
        hit = (feedback_30d > 0 and days is not None
               and days >= STAGNATION_DAYS)
        return hit, {
            "daysSinceVersionEvent": (round(days, 1)
                                      if days is not None
                                      else None),
            "feedback30d": feedback_30d,
            "lastVersionEventAt": last_event_str,
        }

    def detect_depletion(self, data: dict,
                         now: datetime) -> tuple:
        """反馈枯竭: 近 30 日新增反馈 < min_feedback(学不动)

        从未有过反馈不判枯竭(未启动 ≠ 枯竭)。
        """
        window = timedelta(days=DEPLETION_WINDOW_DAYS)
        feedback_30d = sum(
            1 for t in data["feedbackTimes"]
            if (_parse_dt(t) or now) >= now - window)
        total = len(data["feedbackTimes"])
        min_fb = int((data.get("config") or {}).get(
            "min_feedback") or 10)
        hit = total > 0 and feedback_30d < min_fb
        return hit, {
            "feedback30d": feedback_30d,
            "totalFeedback": total,
            "minFeedback": min_fb,
        }

    def detect_drift_high(self, data: dict) -> tuple:
        """漂移高: 既有漂移统计 driftLevel == high"""
        drift = data.get("drift") or {}
        hit = drift.get("driftLevel") == "high"
        return hit, {
            "driftLevel": drift.get("driftLevel") or "none",
            "driftScore": drift.get("driftScore") or 0.0,
        }

    # --------------------------------------------------------
    # 健康分(群体聚合视图指标, 非第29档案)
    # --------------------------------------------------------

    @staticmethod
    def health_score(hits: dict) -> int:
        """健康分 = 100 − 停滞40 − 枯竭30 − 漂移30(命中扣满)"""
        score = 100
        for signal, penalty in PENALTY.items():
            if hits.get(signal):
                score -= penalty
        return max(0, score)

    @staticmethod
    def health_level(score: int) -> str:
        for threshold, name in HEALTH_LEVELS:
            if score >= threshold:
                return name
        return "risk"

    # --------------------------------------------------------
    # 单档案评估
    # --------------------------------------------------------

    async def _assess(self, gov: dict,
                      now: datetime) -> dict:
        scorer_id = gov.get("scorerId")
        data = await self._collect(scorer_id)
        stagnation, stag_detail = self.detect_stagnation(
            data, now)
        depletion, dep_detail = self.detect_depletion(
            data, now)
        drift_high, drift_detail = self.detect_drift_high(data)
        hits = {"stagnation": stagnation,
                "depletion": depletion,
                "drift_high": drift_high}
        score = self.health_score(hits)
        return {
            "scorerId": scorer_id,
            "label": gov.get("label"),
            "module": gov.get("module"),
            "batch": int(gov.get("batch") or 0),
            "govStatus": gov.get("status"),
            "healthScore": score,
            "healthLevel": self.health_level(score),
            **hits,
            "signals": [s for s, hit in hits.items() if hit],
            "details": {**stag_detail, **dep_detail,
                        **drift_detail},
            "errors": data.get("errors") or [],
        }

    async def _assess_all(self) -> tuple:
        """全档案评估(fail-soft: 单档案异常跳过留痕)"""
        now = datetime.now(UTC)
        govs = await self.repo.list_govs(limit=1000)
        entries, skipped = [], []
        for gov in govs:
            if gov.get("status") == "retired":
                continue   # 退役档案不巡检
            try:
                entries.append(await self._assess(gov, now))
            except Exception as exc:
                skipped.append(gov.get("scorerId"))
                logger.warning("ai46_health_assess_skip %s: %s",
                               gov.get("scorerId"), exc)
        # 健康分升序(最需关注在前)
        entries.sort(key=lambda e: (e["healthScore"],
                                    str(e["scorerId"])))
        by_level: dict = {}
        hits: dict = {"stagnation": 0, "depletion": 0,
                     "drift_high": 0}
        for e in entries:
            by_level[e["healthLevel"]] = (
                by_level.get(e["healthLevel"], 0) + 1)
            for s in e["signals"]:
                hits[s] = hits.get(s, 0) + 1
        avg = round(sum(e["healthScore"] for e in entries)
                    / len(entries), 1) if entries else 100.0
        return entries, {
            "scorerCount": len(entries),
            "skipped": skipped, "byLevel": by_level,
            "hits": hits, "avgScore": avg,
        }

    # --------------------------------------------------------
    # 巡检落快照 + 治理告警(POST /ai-gov/health/scan)
    # --------------------------------------------------------

    async def scan(self) -> dict:
        """触发一轮巡检: 评估 → 落快照 → 生成告警(当日去重)"""
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        # 台账先行(幂等 upsert——确保巡检覆盖全部在册档案)
        await AiGovernanceService(
            repo=self.repo).sync_registry()

        entries, stats = await self._assess_all()
        scan_id = await self.repo.next_scan_id()
        now = ts()
        day = now[:10]
        alerts_new = alerts_updated = 0
        for e in entries:
            for signal in e["signals"]:
                created = await self._emit_alert(
                    e, signal, day, scan_id)
                if created:
                    alerts_new += 1
                else:
                    alerts_updated += 1

        record = {
            "scanId": scan_id, "scannedAt": now,
            "scorerCount": stats["scorerCount"],
            "skipped": stats["skipped"],
            "avgScore": stats["avgScore"],
            "byLevel": stats["byLevel"],
            "hits": stats["hits"],
            "alertsNew": alerts_new,
            "alertsUpdated": alerts_updated,
            "entries": entries,
        }
        await self.repo.save_snapshot(record)
        logger.info("ai46_health_scan scanId=%s scorers=%s "
                    "hits=%s alertsNew=%s deduped=%s",
                    scan_id, stats["scorerCount"],
                    stats["hits"], alerts_new, alerts_updated)
        return {"success": True,
                "scanId": scan_id,
                "scannedAt": now, **record}

    async def _emit_alert(self, entry: dict, signal: str,
                          day: str, scan_id: int) -> bool:
        """生成/去重一条告警(同档案同信号当日一条)

        Returns:
            True=新建 / False=当日已有(occurrences 累加)
        """
        scorer_id = entry["scorerId"]
        try:
            existing = await self.repo.find_alert_of_day(
                scorer_id, signal, day)
        except Exception as exc:
            logger.warning("ai46_alert_dedupe_skip %s %s: %s",
                           scorer_id, signal, exc)
            existing = None
        message = self._alert_message(entry, signal)
        if existing:
            existing["occurrences"] = int(
                existing.get("occurrences") or 1) + 1
            existing["lastSeenAt"] = ts()
            existing["message"] = message
            await self.repo.save_alert(existing, new=False)
            return False
        alert_id = await self.repo.next_alert_id()
        await self.repo.save_alert({
            "alertId": alert_id, "scorerId": scorer_id,
            "label": entry.get("label") or scorer_id,
            "signal": signal, "level": "warn",
            "message": message, "day": day,
            "occurrences": 1,
            "firstSeenAt": ts(), "lastSeenAt": ts(),
            "firstScanId": scan_id, "status": "open",
        })
        return True

    def _alert_message(self, entry: dict, signal: str) -> str:
        d = entry.get("details") or {}
        name = SIGNAL_NAMES.get(signal, signal)
        label = entry.get("label") or entry.get("scorerId")
        if signal == "stagnation":
            return (f"[{label}] {name}: 距最近版本事件 "
                    f"{d.get('daysSinceVersionEvent')} 天"
                    f"(阈值 {STAGNATION_DAYS} 天), 近 "
                    f"{DEPLETION_WINDOW_DAYS} 日有 "
                    f"{d.get('feedback30d')} 条反馈未推动"
                    f"版本演进——建议检查学习配置(min_feedback"
                    f"={d.get('minFeedback')})或人工晋升挑战者")
        if signal == "depletion":
            return (f"[{label}] {name}: 近 "
                    f"{DEPLETION_WINDOW_DAYS} 日新增反馈 "
                    f"{d.get('feedback30d')} 条 < 学习阈值 "
                    f"min_feedback={d.get('minFeedback')}"
                    f"(学不动)——建议补充反馈数据源")
        return (f"[{label}] {name}: driftScore="
                f"{d.get('driftScore')}, 因子分布显著偏离"
                f"基线——建议复核近期业务分布变化")

    # --------------------------------------------------------
    # 视图(GET /ai-gov/health + GET /ai-gov/alerts)
    # --------------------------------------------------------

    async def live_health(self) -> dict:
        """全档案健康度排行+分层统计(实时计算不落库)"""
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        govs = await self.repo.list_govs(limit=1000)
        if not govs:
            # 台账未初始化: 幂等同步后重读(只读优先,
            # sync 为 upsert 幂等零破坏)
            await AiGovernanceService(
                repo=self.repo).sync_registry()
        entries, stats = await self._assess_all()
        last = await self.repo.get_latest_snapshot()
        return {
            "success": True, "live": True,
            "scorerCount": stats["scorerCount"],
            "avgScore": stats["avgScore"],
            "byLevel": stats["byLevel"],
            "hits": stats["hits"],
            "skipped": stats["skipped"],
            "entries": entries,
            "lastScan": ({
                "scanId": last.get("scanId"),
                "scannedAt": last.get("scannedAt"),
                "avgScore": last.get("avgScore"),
                "hits": last.get("hits"),
            } if last else None),
            "generatedAt": ts(),
        }

    async def list_alerts(self, signal: str = None,
                          scorer_id: str = None,
                          limit: int = 100) -> dict:
        """告警队列(最新在前; 信号/档案过滤)"""
        alerts = await self.repo.list_alerts(
            signal=signal, scorer_id=scorer_id,
            limit=limit)
        by_signal: dict = {}
        for a in await self.repo.list_alerts(limit=1000):
            s = a.get("signal") or "unknown"
            by_signal[s] = by_signal.get(s, 0) + 1
        return {
            "success": True, "total": len(alerts),
            "alerts": alerts, "bySignal": by_signal,
            "signals": SIGNAL_NAMES,
            "fetchedAt": ts(),
        }
