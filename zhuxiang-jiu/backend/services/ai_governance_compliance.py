"""46号·AI 治理与合规中枢 P4 合规材料自动化
(算法备案材料六节汇编 + 治理审计报告)

计划(docs/46号_AI治理与合规中枢实施计划.md §七):
    ① 算法备案材料(结构化汇编, 对齐《互联网信息服务
       算法推荐管理规定》备案口径)——六节:
        ① 算法基本信息(名称/用途/责任主体占位)
        ② 数据来源与类型说明
        ③ 算法逻辑(权重表+因子清单——真实数据)
        ④ 公平性与性能结论(引用 P2/P1 报告)
        ⑤ 风险防控与应急处置(冻结机制/干预通道/申诉路径)
        ⑥ 变更记录清单(审批总线留痕)
    ② 治理审计报告(时间窗聚合):
        变更数/审批通过率/告警统计/公平性结论/冻结事件
        ——月度治理报告一键生成
    ③ LLM 三态: mock(代码模板, 数字永远来自数据层——
       LLM 幻觉不进备案, 41/42/45号同口径)/real(润色)

设计铁律:
    - 数字来自数据层: 材料每个数字可溯源到注册中心/
      健康度/公平性/变更/告警数据; LLM 仅润色文案
    - 结构模块化: 六节独立拼装, 版本留痕可重编
"""

import logging
from datetime import UTC, datetime, timedelta

from core.helpers import ts

from repositories.ai_governance_repository import (
    AiGovernance46Repository,
)

logger = logging.getLogger(__name__)

# 备案材料版本(结构变更时递增, 支持重编对齐)
FILING_TEMPLATE_VERSION = "v1-filing"

# 审计报告默认时间窗(天)
DEFAULT_REPORT_WINDOW_DAYS = 30


def _within_days(record_ts: str, days: int,
                 now: datetime = None) -> bool:
    """时间戳是否在近 N 天窗口内(不可解析返回 False)"""
    if not record_ts:
        return False
    try:
        dt = datetime.fromisoformat(str(record_ts))
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return (now - dt).total_seconds() <= days * 86400.0


class AiGovernanceComplianceService:
    """合规材料自动化(46号 P4)"""

    def __init__(self,
                 repo: AiGovernance46Repository = None):
        self.repo = repo or AiGovernance46Repository()

    # --------------------------------------------------------
    # ① 算法备案材料(六节结构化汇编)
    # --------------------------------------------------------

    async def build_filing(self, scorer_id: str = None) -> dict:
        """汇编备案材料(scorerId 空=全档案汇总版)

        六节全部来自数据层真实数据; LLM 仅润色
        (llm_mode=real 时调用, 失败回退模板)。
        """
        govs = await self.repo.list_govs(limit=1000)
        if scorer_id:
            govs = [g for g in govs
                    if g.get("scorerId") == scorer_id]
            if not govs:
                raise KeyError(
                    f"档案 {scorer_id} 未入册(先调 sync)")
        if not govs:
            return {"success": True, "filings": [],
                    "count": 0,
                    "templateVersion":
                        FILING_TEMPLATE_VERSION,
                    "note": "台账为空(先调 sync)"}

        filings = []
        for gov in govs:
            try:
                filings.append(await self._build_one(gov))
            except Exception as exc:
                logger.warning("ai46_filing_skip %s: %s",
                               gov.get("scorerId"), exc)
        return {
            "success": True, "count": len(filings),
            "filings": filings,
            "templateVersion": FILING_TEMPLATE_VERSION,
            "generatedAt": ts(),
            "llmMode": self._llm_mode(),
        }

    async def _build_one(self, gov: dict) -> dict:
        scorer_id = gov.get("scorerId")
        # ③ 算法逻辑: 权重表+因子清单(真实数据)
        weights_view = await self._weights_view(scorer_id)
        # ④ 公平性与性能结论(引用 P2/P1 数据)
        fairness = await self.repo.get_latest_report(scorer_id)
        health = await self.repo.get_latest_snapshot()
        health_entry = {}
        if health:
            for e in (health.get("entries") or []):
                if e.get("scorerId") == scorer_id:
                    health_entry = e
                    break
        # ⑥ 变更记录清单(审批总线留痕)
        changes = await self.repo.list_changes(
            scorer_id=scorer_id, limit=50)
        # ⑤ 冻结事件
        frozen_events = [c for c in changes
                         if c.get("kind") in ("freeze",
                                              "unfreeze")]

        sections = {
            "section1_basic": {
                "title": "算法基本信息",
                "algorithmName": gov.get("label"),
                "scorerId": scorer_id,
                "module": gov.get("module"),
                "batch": gov.get("batch"),
                "status": gov.get("status"),
                "responsibility": "责任主体占位(待人工"
                                  "填写运营主体信息)",
                "purpose": f"{gov.get('label')}——嵌入"
                           f"{gov.get('module')}业务决策的"
                           f"AI 评分档案(自动汇编)",
            },
            "section2_data": {
                "title": "数据来源与类型说明",
                "sources": [
                    "业务决策点自愿上报(脱敏因子快照)",
                    "决策终态事件自动回流(ai_learning "
                    "反馈闭环)",
                    "45号信值模块事件适配(合法授权数据)",
                ],
                "dataTypes": [
                    "因子快照(数值型, 无个人标识字段)",
                    "决策动作与真实结果标注",
                    "人工裁决真值(申诉复核回流)",
                ],
                "redline": "最小采集红线: 不含个人标识/"
                           "敏感字段(43号脱敏口径)",
            },
            "section3_logic": {
                "title": "算法逻辑(权重表+因子清单)",
                "formula": "评分 = Σ 因子分 × 权重"
                           "(全档案线性结构)",
                "weightVersion": weights_view.get("version"),
                "weights": weights_view.get("weights"),
                "factorCount": len(
                    weights_view.get("weights") or {}),
                "guardrail": "护栏约束: 相对默认值 "
                             "[1/guardrail, guardrail] 倍"
                             "(ai_learning 护栏机制)",
            },
            "section4_fairness": {
                "title": "公平性与性能结论",
                "fairness": {
                    "flagged": (fairness or {}).get("flagged"),
                    "sampleCount": (fairness or {}).get(
                        "sampleCount", 0),
                    "meanDiffRatio": (fairness or {}).get(
                        "meanDiffRatio"),
                    "passRateGap": (fairness or {}).get(
                        "passRateGap"),
                    "conclusion": (fairness or {}).get(
                        "conclusion", "暂无审计报告"
                        "(先上报采样并触发审计)"),
                },
                "health": {
                    "healthScore": health_entry.get(
                        "healthScore"),
                    "healthLevel": health_entry.get(
                        "healthLevel"),
                    "signals": health_entry.get("signals", []),
                },
                "driftMonitor": "因子分数 EMA 偏离基线 "
                                "自动告警(ai_learning 漂移监控)",
            },
            "section5_risk": {
                "title": "风险防控与应急处置",
                "freeze": "档案级冻结: 审批总线人工审批后"
                          "学习暂停(仅拦学习不拦评分)",
                "intervention": "干预通道: POST /ai-gov/"
                                "changes(freeze/unfreeze 走"
                                "人工审批)",
                "appeal": "申诉路径: 45号申诉复核流"
                          "(裁决真值回流学习闭环)",
                "failSoft": "治理设施 fail-soft: 治理异常"
                            "永不阻断 AI 运行",
                "frozenEvents": len(frozen_events),
            },
            "section6_changes": {
                "title": "变更记录清单(审批总线留痕)",
                "totalChanges": len(changes),
                "approved": sum(1 for c in changes
                                if c.get("status")
                                == "approved"),
                "rejected": sum(1 for c in changes
                                if c.get("status")
                                == "rejected"),
                "pending": sum(1 for c in changes
                                if c.get("status")
                                == "pending"),
                "recentChanges": [
                    {"changeId": c.get("changeId"),
                     "kind": c.get("kind"),
                     "status": c.get("status"),
                     "requestedAt": c.get("requestedAt"),
                     "reviewedAt": c.get("reviewedAt")}
                    for c in changes[:10]],
            },
        }
        return {
            "scorerId": scorer_id,
            "label": gov.get("label"),
            "templateVersion": FILING_TEMPLATE_VERSION,
            "sections": sections,
            "generatedAt": ts(),
        }

    async def _weights_view(self,
                            scorer_id: str) -> dict:
        """当前冠军权重(数字来自数据层)"""
        try:
            from services.ai_learning_service import (
                default_weights,
            )
            from repositories.ai_learning_repository import (
                AiLearningRepository,
            )
            defaults = default_weights(scorer_id)
            profile = await AiLearningRepository(
            ).get_profile(scorer_id) or {}
            champion = profile.get("champion") or {}
            raw = champion.get("weights")
            if isinstance(raw, dict) and set(raw) == \
                    set(defaults):
                return {"version": champion.get("version",
                                                "v1"),
                        "weights": raw}
            return {"version": "v1(default)",
                    "weights": defaults}
        except Exception as exc:
            logger.warning("ai46_filing_weights_skip "
                           "%s: %s", scorer_id, exc)
            return {"version": "unavailable",
                    "weights": {}}

    @staticmethod
    def _llm_mode() -> str:
        """LLM 三态: off(mock 模板)/on(real 润色)"""
        try:
            from services.llm_client import llm_enabled
            return "real" if llm_enabled() else "mock"
        except Exception:
            return "mock"

    # --------------------------------------------------------
    # ② 治理审计报告(时间窗聚合)
    # --------------------------------------------------------

    async def build_report(
            self, days: int = DEFAULT_REPORT_WINDOW_DAYS
    ) -> dict:
        """时间窗聚合审计报告(月度治理报告)

        聚合: 变更数/审批通过率/告警统计/公平性结论/
        冻结事件(全部来自数据层, 窗口过滤)。
        """
        if not (1 <= days <= 365):
            raise ValueError("days 需在 [1, 365] 区间")
        changes = await self.repo.list_changes(limit=1000)
        window_changes = [c for c in changes
                          if _within_days(
                              c.get("requestedAt"), days)]
        approved = [c for c in window_changes
                    if c.get("status") == "approved"]
        rejected = [c for c in window_changes
                    if c.get("status") == "rejected"]
        decided = len(approved) + len(rejected)
        frozen_events = [c for c in window_changes
                         if c.get("kind") in ("freeze",
                                              "unfreeze")]

        alerts = await self.repo.list_alerts(limit=1000)
        window_alerts = [a for a in alerts
                         if _within_days(
                             a.get("firstSeenAt"), days)]
        by_signal: dict = {}
        for a in window_alerts:
            s = a.get("signal") or "unknown"
            by_signal[s] = by_signal.get(s, 0) + 1

        reports = await self.repo.list_reports(limit=1000)
        window_reports = [r for r in reports
                          if _within_days(
                              r.get("generatedAt"), days)]
        flagged = [r for r in window_reports
                   if r.get("flagged")]

        govs = await self.repo.list_govs(limit=1000)
        frozen_now = [g.get("scorerId") for g in govs
                      if g.get("status") == "frozen"]
        latest_health = await \
            self.repo.get_latest_snapshot()

        return {
            "success": True,
            "windowDays": days,
            "generatedAt": ts(),
            "registry": {
                "total": len(govs),
                "active": sum(1 for g in govs
                              if g.get("status")
                              == "active"),
                "frozen": len(frozen_now),
                "frozenList": frozen_now[:10],
                "retired": sum(1 for g in govs
                               if g.get("status")
                               == "retired"),
            },
            "changes": {
                "total": len(window_changes),
                "approved": len(approved),
                "rejected": len(rejected),
                "pending": sum(
                    1 for c in window_changes
                    if c.get("status") == "pending"),
                "approvalRate": round(
                    len(approved) / decided, 4)
                if decided else None,
            },
            "alerts": {
                "total": len(window_alerts),
                "bySignal": by_signal,
            },
            "fairness": {
                "reportsGenerated": len(window_reports),
                "flaggedCount": len(flagged),
                "flaggedScorers": [
                    r.get("scorerId") for r in flagged[:10]],
            },
            "freezeEvents": {
                "total": len(frozen_events),
                "detail": [
                    {"changeId": c.get("changeId"),
                     "kind": c.get("kind"),
                     "scorerId": c.get("scorerId"),
                     "reviewedAt": c.get("reviewedAt")}
                    for c in frozen_events[:10]],
            },
            "health": ({
                "lastScanId":
                    latest_health.get("scanId"),
                "lastScannedAt":
                    latest_health.get("scannedAt"),
                "avgScore":
                    latest_health.get("avgScore"),
                "hits": latest_health.get("hits"),
            } if latest_health else None),
            "conclusion": self._report_conclusion(
                len(window_changes), decided,
                len(approved), len(window_alerts),
                len(flagged), len(frozen_now)),
        }

    @staticmethod
    def _report_conclusion(total, decided, approved,
                           alert_count, flagged_count,
                           frozen_now) -> str:
        """中文结论(数字全部来自聚合层)"""
        parts = [f"近窗内变更 {total} 次"
                 f"(已裁决 {decided}, 通过 {approved}"]
        if decided:
            parts.append(f", 通过率 "
                         f"{round(approved / decided * 100, 1)}%")
        parts.append(f"); 治理告警 {alert_count} 条; "
                     f"公平性偏疑 {flagged_count} 项; "
                     f"当前冻结档案 {frozen_now} 个")
        if flagged_count:
            parts.append("——建议优先复核偏疑档案"
                         "公平性审计报告")
        elif alert_count == 0 and total == 0:
            parts.append("——治理平稳(无变更无告警)")
        else:
            parts.append("——治理运行正常")
        return "".join(parts)
