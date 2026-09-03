"""43号·P4-1 SOC 安全运营日报服务

计划 §二(docs/43号P4_运营成熟化实施计划.md):
    - 按日聚合 security_events(security_events 按 createdAt 前缀
      切分) → daily_report 日报(事件分布/裁决/误报率/D5 专项)
    - D5 专项观测: 命中数/误报裁决率/联动建议(硬标准:
      观察≥14天 且 误报率<5% 且 样本≥20 → 建议开启强制联动)
    - 态势时间线: posture.history 按日归并

口径:
    - 时区: createdAt 为 UTC ISO(与 ts() 一致), 按日切分取
      date 前 10 位, 无需时区转换
    - D5 命中口径: identity_risk 降分事件中 factors 含 D5
      前缀因子的 behavior_alert + D5_stuffing 撞库预警
"""

import logging
from datetime import datetime, UTC, timedelta

from core.helpers import ts
from repositories.security_repository import (
    Security43Repository,
)

logger = logging.getLogger(__name__)

# D5 联动决策硬标准(计划 §二 ④)
D5_OBSERVE_MIN_DAYS = 14
D5_MAX_FALSE_POSITIVE_RATE = 0.05
D5_MIN_SAMPLES = 20


def _date_str(iso_or_date) -> str:
    """任意时间表示 → YYYY-MM-DD(UTC)"""
    s = str(iso_or_date or "")
    if len(s) >= 10:
        return s[:10]
    return ""


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).strftime(
        "%Y-%m-%d")


class SocReportService:
    """SOC 安全运营日报服务(43号 P4-1)"""

    def __init__(self, repo: Security43Repository
                 = Security43Repository()):
        self.repo = repo

    # ========================================================
    # 单日报告
    # ========================================================

    async def daily_report(self, date: str = None) -> dict:
        """生成指定日期(UTC)的安全运营日报

        Args:
            date: YYYY-MM-DD(缺省今天)

        Returns:
            {date, eventsByAction, verdicts, falsePositiveRate,
             appeals, blocks, d5, postureTimeline}
        """
        date = date or _today()
        events = await self.repo.list_events(limit=2000)
        day_events = [e for e in events
                      if _date_str(e.get("createdAt")) == date]

        by_action = {}
        verdicts = {"confirmed": 0, "falsePositive": 0,
                    "pending": 0}
        for e in day_events:
            a = e.get("action") or "unknown"
            by_action[a] = by_action.get(a, 0) + 1
            v = e.get("verdict")
            if v == "confirmed":
                verdicts["confirmed"] += 1
            elif v == "false_positive":
                verdicts["falsePositive"] += 1
            elif v == "pending":
                verdicts["pending"] += 1

        decided = verdicts["confirmed"] + verdicts["falsePositive"]
        fpr = (round(verdicts["falsePositive"] / decided, 4)
               if decided else 0.0)

        # 申诉按日(createdAt 同日提交)
        appeals = await self.repo.list_appeals(limit=2000)
        day_appeals = [a for a in appeals
                       if _date_str(a.get("createdAt")) == date]

        # D5 专项
        d5 = self._d5_daily(day_events)

        # 态势时间线(全局 history 中当日条目)
        posture_timeline = await self._posture_daily(date)

        return {
            "success": True,
            "date": date,
            "eventsByAction": by_action,
            "eventsTotal": len(day_events),
            "verdicts": verdicts,
            "falsePositiveRate": fpr,
            "appeals": {
                "created": len(day_appeals),
                "pending": sum(1 for a in day_appeals
                               if a.get("status") == "pending"),
            },
            "d5": d5,
            "postureTimeline": posture_timeline,
        }

    # ========================================================
    # 近 N 天序列(联动决策数据源)
    # ========================================================

    async def daily_series(self, days: int = 14) -> dict:
        """近 N 天日报序列(含汇总与 D5 联动建议)"""
        days = max(1, min(days, 90))
        reports = []
        for i in range(days - 1, -1, -1):
            reports.append(await self.daily_report(_days_ago(i)))
        return {
            "success": True,
            "days": days,
            "reports": reports,
            "summary": self._series_summary(reports),
        }

    def _series_summary(self, reports: list[dict]) -> dict:
        total = sum(r.get("eventsTotal", 0) for r in reports)
        confirmed = sum(r["verdicts"]["confirmed"]
                        for r in reports)
        false_pos = sum(r["verdicts"]["falsePositive"]
                        for r in reports)
        decided = confirmed + false_pos
        # 有事件的天数(观察期有效天数)
        active_days = sum(1 for r in reports
                          if r.get("eventsTotal", 0) > 0)
        return {
            "eventsTotal": total,
            "confirmed": confirmed,
            "falsePositive": false_pos,
            "falsePositiveRate": (
                round(false_pos / decided, 4) if decided else 0.0),
            "activeDays": active_days,
            "d5Samples": sum(r["d5"]["hits"]
                             + r["d5"]["stuffingAlerts"]
                             for r in reports),
        }

    # ========================================================
    # D5 专项观测(联动决策硬标准)
    # ========================================================

    def _d5_daily(self, day_events: list[dict]) -> dict:
        """单日 D5 统计"""
        jumps = 0         # behavior_alert 中 D5_jump 类因子命中
        stuffing = 0     # D5_stuffing 撞库预警
        decided = 0
        false_pos = 0
        for e in day_events:
            if e.get("action") != "behavior_alert":
                continue
            factors = e.get("factors") or []
            names = [str(f.get("name") or "") for f in factors]
            if any(n == "D5_stuffing" for n in names):
                stuffing += 1
            elif any(n.startswith("D1_") or n.startswith("D2_")
                     or n.startswith("D3_") or n.startswith("D4_")
                     or n.startswith("D5_") for n in names):
                jumps += 1
            # D5 相关事件的裁决口径
            verdict = e.get("verdict")
            if verdict in ("confirmed", "false_positive"):
                decided += 1
                if verdict == "false_positive":
                    false_pos += 1
        return {
            "hits": jumps,
            "stuffingAlerts": stuffing,
            "samples": jumps + stuffing,
            "decided": decided,
            "falsePositive": false_pos,
            "falsePositiveRate": (
                round(false_pos / decided, 4) if decided else None),
        }

    async def d5_observation(self) -> dict:
        """D5 联动决策观测(硬标准评估)

        Returns:
            {samples, falsePositiveRate, observeDays,
             recommendation, criteria}
        """
        reports = [(await self.daily_report(_days_ago(i)))
                   for i in range(D5_OBSERVE_MIN_DAYS - 1, -1, -1)]
        samples = sum(r["d5"]["samples"] for r in reports)
        decided = sum(r["d5"]["decided"] for r in reports)
        false_pos = sum(r["d5"]["falsePositive"] for r in reports)
        active_days = sum(1 for r in reports
                          if r["d5"]["samples"] > 0)
        fpr = (round(false_pos / decided, 4)
               if decided else None)

        criteria = {
            "observeDays": {
                "required": D5_OBSERVE_MIN_DAYS,
                "actual": active_days,
                "met": active_days >= D5_OBSERVE_MIN_DAYS},
            "falsePositiveRate": {
                "required": f"<{D5_MAX_FALSE_POSITIVE_RATE}",
                "actual": fpr,
                "met": (fpr is not None
                       and fpr < D5_MAX_FALSE_POSITIVE_RATE)},
            "samples": {
                "required": f">={D5_MIN_SAMPLES}",
                "actual": samples,
                "met": samples >= D5_MIN_SAMPLES},
        }
        all_met = all(c["met"] for c in criteria.values())
        if not active_days:
            recommendation = "insufficient_data"
        elif all_met:
            recommendation = "enable_strict_linkage"
        else:
            recommendation = "keep_observe"

        # P5-1: 联动执行开关实况(面板⑦区 D5 状态卡数据源)
        from services.sequence_service import (
            d5_enforce_on, d5_enforce_band,
        )
        band_lo, band_hi = d5_enforce_band()
        return {
            "success": True,
            "samples": samples,
            "decided": decided,
            "falsePositiveRate": fpr,
            "observeDays": active_days,
            "criteria": criteria,
            "recommendation": recommendation,
            "d5Enforce": {
                "active": d5_enforce_on(),
                "band": f"{band_lo:g}-{band_hi:g}",
                "note": "SECURITY_D5_ENFORCE=on 开启"
                        "(建议: 达标后人工开启, 7 天内复核误报率)",
            },
            "recommendationName": {
                "enable_strict_linkage": "建议开启 D5 强制联动",
                "keep_observe": "维持 observe 继续观察",
                "insufficient_data": "样本不足, 继续观察",
            }[recommendation],
        }

    # ========================================================
    # 态势时间线
    # ========================================================

    async def _posture_daily(self, date: str) -> list[dict]:
        """当日态势转换时间线(全局 posture.history 过滤)"""
        try:
            record = await self.repo.get_posture()
            history = record.get("history") or []
            return [h for h in history
                    if _date_str(h.get("at")) == date]
        except Exception:
            return []
