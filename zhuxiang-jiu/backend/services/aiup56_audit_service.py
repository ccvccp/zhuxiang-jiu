"""56号·AI智能升级管理 审计Agent
(aiup56_audit_service, P3)

计划(docs/56号_AI智能升级管理模块实施计划.md §三/§九 P3):
    审计Agent 职责:
    - 合规三重校验:
        ① 代码层: 敏感 API/数据流向/加密强度
           (沙箱静态关结论复核+资产完整性)
        ② 逻辑层: 业务规则符合性(预算封顶/
           状态机/回滚预案完备性)
        ③ 文档层: 变更说明完整/用户告知充分
           (摘要+VALUE_REASON+测试计划齐备)
    - 一票否决权: 任一层 critical 违规 →
      verdict rejected(状态回 planned 重规划)
    - LLM 归因报告(mock/real 三态——
      数字来自数据层; 高亮人工重点关注项)

QC(计划 §九 P3): 审计一票否决; 无审批不可
交付(架构断言——交付端点必经 review 状态)。
"""

import logging
import os

from core.helpers import ts

from repositories.aiup56_repository import (
    Aiup56Repository,
)

logger = logging.getLogger("aiup56_audit_service")

MODEL_VERSION = "v1-aiup56-audit"

SCORER_ID = "upgrade_orchestration"

# 审计 LLM 调用成本(real 轨)
AUDIT_LLM_COST = 0.01

# 代码层校验项(敏感 API 复核——沙箱静态关
# 结论+资产级完整性)
CODE_LAYER_CHECKS = ("staticViolations", "assetIntegrity")

# 逻辑层校验项(业务规则)
LOGIC_LAYER_CHECKS = ("budgetCap", "stateMachine",
                      "rollbackPlan")

# 文档层校验项
DOC_LAYER_CHECKS = ("summary", "valueReasons",
                    "testPlans")


class Aiup56AuditService:
    """56号审计Agent(合规三重校验+一票否决+
    LLM 归因报告)"""

    def __init__(self):
        self.repo = Aiup56Repository()

    # ============================================================
    # 审计入口
    # ============================================================

    async def audit(self, proposal_id: int) -> dict:
        """执行审计(三重校验→verdict→归因报告;
        状态 tested→audited/rejected)

        Raises:
            KeyError: 提案不存在
            ValueError: off 态/状态机非法(非 tested)
        """
        from services.aiup56_service import (
            require_active_mode,
        )
        require_active_mode()

        proposal = await self.repo.get_proposal(
            int(proposal_id))
        if proposal is None:
            raise KeyError(f"提案 {proposal_id} 不存在")
        if proposal.get("status") != "tested":
            raise ValueError(
                f"提案状态 {proposal.get('status')}"
                f"(需 tested 方可审计——先触发 test)")

        # 沙箱结论(最新)
        sandboxes = await self.repo.list_sandboxes(
            proposal_id=int(proposal_id))
        sandbox = sandboxes[0] if sandboxes else None
        assets = await self.repo.list_assets(
            proposal_id=int(proposal_id))
        asset = assets[0] if assets else None

        # ① 代码层(敏感 API 复核+资产完整性)
        code_layer = self._code_layer(sandbox, asset)

        # ② 逻辑层(业务规则)
        logic_layer = self._logic_layer(proposal,
                                         sandbox)

        # ③ 文档层(变更说明/告知充分)
        doc_layer = self._doc_layer(proposal, asset)

        # 一票否决: 任一层 critical
        layers = (code_layer, logic_layer, doc_layer)
        critical = any(
            item.get("severity") == "critical"
            for layer in layers
            for item in layer.get("findings") or [])
        verdict = "rejected" if critical else "passed"

        # LLM 归因报告(mock/real 三态)
        report, report_mode, llm_cost = \
            await self._audit_report(
                proposal, sandbox, asset,
                layers, verdict)

        # 事件留痕
        await self._track(proposal_id, "audit", {
            "verdict": verdict,
            "codePassed": code_layer.get("passed"),
            "logicPassed": logic_layer.get("passed"),
            "docPassed": doc_layer.get("passed"),
            "reportMode": report_mode,
        })

        # 状态翻转(passed→audited / rejected→
        # planned 重规划)
        proposal["status"] = \
            "audited" if verdict == "passed" \
            else "planned"
        proposal["auditVerdict"] = verdict
        proposal["auditReport"] = report
        proposal["auditMode"] = report_mode
        proposal["auditLayers"] = {
            "code": code_layer,
            "logic": logic_layer,
            "doc": doc_layer,
        }
        if llm_cost:
            proposal["budgetSpent"] = round(
                float(proposal.get("budgetSpent")
                      or 0) + llm_cost, 4)
        proposal["updatedAt"] = ts()
        await self.repo.save_proposal(
            proposal, create=False)

        return {
            "success": True,
            "proposalId": int(proposal_id),
            "verdict": verdict,
            "status": proposal["status"],
            "layers": {
                "code": code_layer,
                "logic": logic_layer,
                "doc": doc_layer,
            },
            "report": report,
            "reportMode": report_mode,
            "highlightItems": self._highlights(
                layers, verdict),
            "note": "审计Agent——三重校验+一票否决"
                    "(人类审批面板 P3 接管)",
            "auditedAt": ts(),
        }

    # ============================================================
    # ① 代码层(敏感 API+资产完整性)
    # ============================================================

    @staticmethod
    def _code_layer(sandbox: dict | None,
                    asset: dict | None) -> dict:
        findings = []
        # 敏感 API 复核(沙箱静态关结论)
        static = (sandbox or {}).get("staticGate") or {}
        for violation in static.get("violations") \
                or []:
            findings.append({
                "check": "staticViolations",
                "severity": "critical",
                "detail": f"敏感 API/PII: {violation}",
            })
        # 资产完整性
        if asset is None:
            findings.append({
                "check": "assetIntegrity",
                "severity": "critical",
                "detail": "资产包缺失(编码Agent "
                          "产出被移除)",
            })
        else:
            drafts = asset.get("drafts") or []
            test_plans = asset.get("testPlans") or []
            if not drafts:
                findings.append({
                    "check": "assetIntegrity",
                    "severity": "critical",
                    "detail": "草稿为空",
                })
            if len(drafts) != len(test_plans):
                findings.append({
                    "check": "assetIntegrity",
                    "severity": "warning",
                    "detail": "草稿与测试计划数量"
                              "不齐",
                })
            # 加密强度(静态关弱加密告警复核)
            warnings = static.get("warnings") or []
            if warnings:
                findings.append({
                    "check": "assetIntegrity",
                    "severity": "warning",
                    "detail": f"弱加密告警: "
                              f"{warnings[0]}",
                })
        return {
            "layer": "代码层",
            "checks": list(CODE_LAYER_CHECKS),
            "passed": not any(
                f.get("severity") == "critical"
                for f in findings),
            "findings": findings,
        }

    # ============================================================
    # ② 逻辑层(业务规则)
    # ============================================================

    @staticmethod
    def _logic_layer(proposal: dict,
                     sandbox: dict | None) -> dict:
        findings = []
        # 预算封顶
        spent = float(
            proposal.get("budgetSpent") or 0)
        cap = float(
            proposal.get("budgetCap") or 0.1)
        if spent > cap:
            findings.append({
                "check": "budgetCap",
                "severity": "critical",
                "detail": f"预算超支 {spent}>{cap}",
            })
        # 沙箱 verdict
        sb_verdict = (sandbox or {}).get("verdict")
        if sb_verdict != "passed":
            findings.append({
                "check": "stateMachine",
                "severity": "critical",
                "detail": f"沙箱未通过"
                          f"({sb_verdict})——"
                          f"不应进入审计",
            })
        # 回滚预案完备性
        tasks = proposal.get("tasks") or []
        incomplete = [
            t.get("title") for t in tasks
            if not ((t.get("rollbackPlan") or {})
                    .get("strategy"))]
        if incomplete:
            findings.append({
                "check": "rollbackPlan",
                "severity": "warning",
                "detail": f"回滚预案缺失: "
                          f"{incomplete[:3]}",
            })
        return {
            "layer": "逻辑层",
            "checks": list(LOGIC_LAYER_CHECKS),
            "passed": not any(
                f.get("severity") == "critical"
                for f in findings),
            "findings": findings,
        }

    # ============================================================
    # ③ 文档层(变更说明/告知充分)
    # ============================================================

    @staticmethod
    def _doc_layer(proposal: dict,
                   asset: dict | None) -> dict:
        findings = []
        summary = proposal.get("summary") or {}
        if not summary.get("headline") \
                or not summary.get("topSignals"):
            findings.append({
                "check": "summary",
                "severity": "critical",
                "detail": "提案摘要不完整"
                          "(缺 headline/topSignals)",
            })
        value_reasons = (asset or {}).get(
            "VALUE_REASONs") or []
        if not value_reasons:
            findings.append({
                "check": "valueReasons",
                "severity": "critical",
                "detail": "VALUE_REASON 缺失——"
                          "变更对信值体系的贡献"
                          "无证据",
            })
        test_plans = (asset or {}).get(
            "testPlans") or []
        if not test_plans:
            findings.append({
                "check": "testPlans",
                "severity": "warning",
                "detail": "测试计划缺失",
            })
        return {
            "layer": "文档层",
            "checks": list(DOC_LAYER_CHECKS),
            "passed": not any(
                f.get("severity") == "critical"
                for f in findings),
            "findings": findings,
        }

    # ============================================================
    # LLM 归因报告(mock/real 三态——数字来自
    # 数据层, 55号 attribution 范式)
    # ============================================================

    async def _audit_report(self, proposal: dict,
                            sandbox: dict | None,
                            asset: dict | None,
                            layers: tuple,
                            verdict: str) -> tuple:
        """《升级合规审计报告》(确定性模板+LLM 润色
        三态; 返回 (report, mode, llm_cost))"""
        # 数据层事实(数字唯一来源)
        findings_total = sum(
            len(layer.get("findings") or [])
            for layer in layers)
        critical_total = sum(
            1 for layer in layers
            for f in layer.get("findings") or []
            if f.get("severity") == "critical")

        # mock 确定性模板
        mode = "mock"
        answer = self._mock_report(
            proposal, sandbox, layers, verdict,
            findings_total, critical_total)

        # real 润色(fail-soft——assist 态)
        llm_cost = 0.0
        if self._llm_available():
            try:
                from services.llm_client import (
                    provider_client,
                )
                reply = provider_client().chat(
                    system="你是升级合规审计助手。"
                           "用不超过 4 句中文总结"
                           "审计结论。只使用用户"
                           "提供的数据, 不编造任何"
                           "数字。",
                    user=f"审计事实(以此为准):\n"
                         f"{answer}")
                if reply and reply.strip():
                    answer = reply.strip()
                    mode = "real"
                    llm_cost = AUDIT_LLM_COST
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "aiup56_audit_llm_skip: %s", exc)

        report = {
            "verdict": verdict,
            "headline": answer,
            "facts": {
                "findingsTotal": findings_total,
                "criticalTotal": critical_total,
                "sandboxVerdict":
                    (sandbox or {}).get("verdict"),
                "budgetSpent":
                    proposal.get("budgetSpent"),
                "budgetCap":
                    proposal.get("budgetCap"),
                "estimatedGain":
                    proposal.get("estimatedGain"),
            },
            "generatedAt": ts(),
        }
        return report, mode, llm_cost

    @staticmethod
    def _mock_report(proposal: dict,
                     sandbox: dict | None,
                     layers: tuple,
                     verdict: str,
                     findings_total: int,
                     critical_total: int) -> str:
        pid = proposal.get("proposalId")
        sb = (sandbox or {}).get("verdict") or "-"
        parts = [
            f"提案 {pid} 三重校验完成: 代码层/"
            f"逻辑层/文档层, 结论 {verdict}"
            f"(发现 {findings_total} 项, 其中 "
            f"critical {critical_total} 项); "
            f"沙箱 verdict {sb}"]
        for layer in layers:
            status = "通过" if layer.get("passed") \
                else "存在 critical 违规"
            parts.append(
                f"{layer['layer']}: {status}")
        if verdict == "rejected":
            parts.append(
                "一票否决生效——提案已回退 planned "
                "重新规划; 需修复 critical 违规后"
                "重走编码/测试/审计链")
        else:
            parts.append(
                "审计通过——待人类审批面板终审"
                "(关键项强制确认; 交付无审批"
                "不可达)")
        return "; ".join(parts)

    # --------------------------------------------------------
    # 高亮人工重点关注项
    # --------------------------------------------------------

    @staticmethod
    def _highlights(layers: tuple,
                     verdict: str) -> list:
        """需人工重点关注项(高亮——一键追问入口)"""
        items = []
        for layer in layers:
            for f in layer.get("findings") or []:
                if f.get("severity") == "critical":
                    items.append(
                        f"[{layer['layer']}] "
                        f"{f.get('detail')}")
        return items[:5]

    # --------------------------------------------------------
    # LLM 可用性(assist 态)
    # --------------------------------------------------------

    @staticmethod
    def _llm_available() -> bool:
        if os.environ.get("AIUP56_MODE") != "assist":
            return False
        try:
            from services.llm_client import llm_enabled
            return llm_enabled()
        except Exception:  # noqa: BLE001
            return False

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, proposal_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "proposalId": int(proposal_id),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_audit_track_failed: %s", exc)
