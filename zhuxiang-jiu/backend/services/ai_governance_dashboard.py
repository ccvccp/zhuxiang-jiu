"""46号·AI 治理与合规中枢 P5 治理看板聚合
(六区块一次拉取 + 干预闭环入口)

计划(docs/46号_AI治理与合规中枢实施计划.md §八):
    ① 档案总览: 28 档案状态分布(active/frozen/retired)
       + batch 分组
    ② 审批队列: pending 变更列表(一键审批入口)
    ③ 健康排行: 健康分 Top/Bottom + 三检测器命中
    ④ 公平性视图: 各档案群体均值对比 + flagged 标记
    ⑤ 回放轨迹: 最近决策日志 + 漂移标记
    ⑥ 合规入口: 备案材料/审计报告链接 + 红线提示

干预闭环(§8.2): 冻结/解冻走审批总线(P0)——看板提交
申请 → 审批 → 注册中心生效 → run_learning 守卫生效
(全链 E2E, 前端面板提供提交入口, 后端端点 P0 已备)。

设计铁律:
    - 单端点聚合(43/44/45号范式): 一次 GET 六区块全量,
      前端零拼装
    - fail-soft 分区: 单区块数据源异常不阻断看板
      (区块级 error 留痕, 其余照常)
    - 数字来自数据层: 各区块直接引用 P0-P4 服务真实
      计算/存储, 不做二次加工
"""

import logging

from core.helpers import ts

from repositories.ai_governance_repository import (
    AiGovernance46Repository,
)

logger = logging.getLogger(__name__)

# 治理红线(§九: 展示层常驻提示)
REDLINES = (
    "治理不阻断: 治理设施异常 fail-soft 永不阻断 AI 运行"
    "(仅人工审批的冻结干预学习, 不拦评分)",
    "审批即真值: 变更审批留痕只追加不可篡改; 重复审批拒绝",
    "数字来自数据层: 备案/报告数字可溯源, LLM 仅润色",
    "最小采集: 回放/采样脱敏——不含个人标识字段",
    "监督权分离: 治理审批 X-Role: admin 独立于业务",
)


class AiGovernanceDashboardService:
    """治理看板聚合(46号 P5 压轴)"""

    def __init__(self,
                 repo: AiGovernance46Repository = None):
        self.repo = repo or AiGovernance46Repository()

    async def build(self) -> dict:
        """六区块聚合(单次 GET, fail-soft 分区)"""
        zones = {}
        errors = []

        async def _zone(name, fn):
            try:
                zones[name] = await fn()
            except Exception as exc:
                errors.append(name)
                zones[name] = {"error": str(exc)[:120]}
                logger.warning("ai46_dashboard_zone_skip "
                               "%s: %s", name, exc)

        await _zone("registry", self._zone_registry)
        await _zone("approvals", self._zone_approvals)
        await _zone("health", self._zone_health)
        await _zone("fairness", self._zone_fairness)
        await _zone("replay", self._zone_replay)
        await _zone("compliance", self._zone_compliance)

        return {
            "success": True,
            "zones": zones,
            "zoneErrors": errors,
            "redlines": REDLINES,
            "intervention": {
                "note": "冻结/解冻走审批总线: 看板提交申请"
                        "→人工审批→注册中心生效→学习守卫生效",
                "submitEndpoint":
                    "POST /api/ai-gov/changes",
                "reviewEndpoint":
                    "POST /api/ai-gov/changes/"
                    "{changeId}/review",
            },
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # ① 档案总览(状态分布 + batch 分组)
    # --------------------------------------------------------

    async def _zone_registry(self) -> dict:
        govs = await self.repo.list_govs(limit=1000)
        by_status = {"active": 0, "frozen": 0, "retired": 0}
        by_batch: dict = {}
        frozen_list = []
        for g in govs:
            s = g.get("status") or "active"
            by_status[s] = by_status.get(s, 0) + 1
            b = int(g.get("batch") or 0)
            by_batch[b] = by_batch.get(b, 0) + 1
            if s == "frozen":
                frozen_list.append(g.get("scorerId"))
        return {
            "total": len(govs),
            "byStatus": by_status,
            "byBatch": dict(sorted(by_batch.items())),
            "frozenScorers": frozen_list[:10],
            "recentSyncedAt": max(
                (g.get("lastSyncedAt") or ""
                 for g in govs), default=""),
        }

    # --------------------------------------------------------
    # ② 审批队列(pending 变更 + 统计)
    # --------------------------------------------------------

    async def _zone_approvals(self) -> dict:
        changes = await self.repo.list_changes(limit=500)
        pending = [c for c in changes
                   if c.get("status") == "pending"]
        by_status: dict = {}
        for c in changes:
            s = c.get("status") or "pending"
            by_status[s] = by_status.get(s, 0) + 1
        return {
            "pendingCount": len(pending),
            "byStatus": by_status,
            "pendingChanges": [
                {"changeId": c.get("changeId"),
                 "scorerId": c.get("scorerId"),
                 "kind": c.get("kind"),
                 "reason": str(c.get("reason"))[:60],
                 "requestedBy": c.get("requestedBy"),
                 "requestedAt": c.get("requestedAt")}
                for c in pending[:10]],
        }

    # --------------------------------------------------------
    # ③ 健康排行(Top/Bottom + 三检测器命中)
    # --------------------------------------------------------

    async def _zone_health(self) -> dict:
        snapshot = await self.repo.get_latest_snapshot()
        if snapshot is None:
            return {
                "note": "暂无巡检快照(先触发 "
                        "POST /api/ai-gov/health/scan)",
                "avgScore": None,
                "top": [], "bottom": [],
                "hits": {}, "byLevel": {},
                "lastScan": None,
            }
        entries = snapshot.get("entries") or []
        ranked = sorted(
            entries, key=lambda e: e.get("healthScore") or 0)
        return {
            "lastScan": {
                "scanId": snapshot.get("scanId"),
                "scannedAt": snapshot.get("scannedAt"),
            },
            "avgScore": snapshot.get("avgScore"),
            "byLevel": snapshot.get("byLevel") or {},
            "hits": snapshot.get("hits") or {},
            "top": [self._health_row(e)
                    for e in ranked[-5:][::-1]],
            "bottom": [self._health_row(e)
                       for e in ranked[:5]],
        }

    @staticmethod
    def _health_row(e: dict) -> dict:
        return {
            "scorerId": e.get("scorerId"),
            "label": e.get("label"),
            "healthScore": e.get("healthScore"),
            "healthLevel": e.get("healthLevel"),
            "signals": e.get("signals") or [],
        }

    # --------------------------------------------------------
    # ④ 公平性视图(各档案最近报告 + flagged 标记)
    # --------------------------------------------------------

    async def _zone_fairness(self) -> dict:
        reports = await self.repo.list_reports(limit=100)
        flagged_rows = []
        normal_count = 0
        for r in reports:
            if r.get("flagged"):
                flagged_rows.append({
                    "scorerId": r.get("scorerId"),
                    "sampleCount": r.get("sampleCount"),
                    "meanDiffRatio":
                        r.get("meanDiffRatio"),
                    "passRateGap": r.get("passRateGap"),
                    "conclusion": str(
                        r.get("conclusion"))[:80],
                })
            else:
                normal_count += 1
        return {
            "reportsTotal": len(reports),
            "flaggedCount": len(flagged_rows),
            "normalCount": normal_count,
            "flagged": flagged_rows[:10],
            "note": ("" if reports else
                     "暂无审计报告(先上报采样并触发审计)"),
        }

    # --------------------------------------------------------
    # ⑤ 回放轨迹(最近决策日志 + 漂移标记)
    # --------------------------------------------------------

    async def _zone_replay(self) -> dict:
        # 复用服务层 list_logs(逐条重算漂移标注——
        # 仓储层不标, 计算在服务)
        from services.ai_governance_replay import (
            AiGovernanceReplayService,
        )
        r = await AiGovernanceReplayService(
            repo=self.repo).list_logs(limit=200)
        logs = r.get("logs") or []
        drifted = [l for l in logs if l.get("drifted")]
        return {
            "logsTotal": len(logs),
            "driftedCount": len(drifted),
            "driftThreshold":
                r.get("driftThreshold"),
            "recentLogs": [
                {"replayId": l.get("replayId"),
                 "scorerId": l.get("scorerId"),
                 "subjectRef": l.get("subjectRef"),
                 "score": l.get("score"),
                 "rescored": l.get("rescored"),
                 "weightVersion":
                     l.get("weightVersion"),
                 "drifted": l.get("drifted"),
                 "ts": l.get("ts")}
                for l in logs[:10]],
        }

    # --------------------------------------------------------
    # ⑥ 合规入口(材料/报告链接 + 摘要)
    # --------------------------------------------------------

    async def _zone_compliance(self) -> dict:
        reports = await self.repo.list_reports(limit=1)
        filing_count = len(
            await self.repo.list_govs(limit=1000))
        last_filing = None
        try:
            from services.ai_governance_compliance import (
                AiGovernanceComplianceService,
            )
            audit = await (
                AiGovernanceComplianceService(
                    repo=self.repo).build_report(days=30))
            last_filing = {
                "windowDays": audit.get("windowDays"),
                "changes": (audit.get("changes") or {})
                .get("total"),
                "alerts": (audit.get("alerts") or {})
                .get("total"),
                "flagged": (audit.get("fairness") or {})
                .get("flaggedCount"),
                "conclusion": str(
                    audit.get("conclusion"))[:100],
            }
        except Exception as exc:
            logger.warning("ai46_dashboard_compliance_skip: "
                           "%s", exc)
        return {
            "endpoints": {
                "filing":
                    "GET /api/ai-gov/compliance/filing",
                "report":
                    "GET /api/ai-gov/compliance/"
                    "report?days=30",
                "fairnessReport":
                    "GET /api/ai-gov/fairness/report",
            },
            "registryCount": filing_count,
            "fairnessReports": len(reports),
            "lastAudit": last_filing,
            "note": "备案材料六节汇编 + 审计报告时间窗"
                    "聚合——数字全部来自数据层",
        }
