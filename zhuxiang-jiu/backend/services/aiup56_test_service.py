"""56号·AI智能升级管理 测试Agent+信值沙箱
(aiup56_test_service, P2)

计划(docs/56号_AI智能升级管理模块实施计划.md §四/§九 P2):
    测试Agent 职责:
    - 用例矩阵执行(正常/边界/异常——编码Agent
      测试计划草稿消费)
    - 信值沙箱三关评估(影子评估管线——不执行
      任意生成代码, 全部离线确定性):
        ① 静态关: 代码草稿规则扫描(敏感 API 白
           名单比对/PII 禁入/国密调用规范比对)
        ② 预算关: 隐私消耗模拟(草稿声明 cost
           按提案封顶比对——超支熔断暂停+告警)
        ③ 价值关: 信值增益预估回放(预估增益
           < 阈值 → 门禁不通过)
    - 沙箱报告(verdict: passed/blocked/
      budget_halted + 三关明细)

预算铁律(计划 §〇 三铁律之三):
    提案级封顶(budgetCap 0.1)——沙箱消费
    budgetSpent 累计; 超支 → budget_halted
    熔断(不抛错——留痕+告警, 人工决定加额或放弃)。

状态机: coded → tested(通过)/blocked(静态或
价值关不通过——回编码Agent 重生成)。
"""

import logging
import os
import re

from core.helpers import ts

from repositories.aiup56_repository import (
    Aiup56Repository,
)

logger = logging.getLogger("aiup56_test_service")

MODEL_VERSION = "v1-aiup56-test"

SCORER_ID = "upgrade_orchestration"

# 敏感 API 黑名单(静态关——AI 草稿禁入)
SENSITIVE_PATTERNS = (
    (r"\beval\s*\(", "eval 动态执行"),
    (r"\bexec\s*\(", "exec 动态执行"),
    (r"\b__import__\s*\(", "动态导入"),
    (r"\bopen\s*\(\s*['\"]/", "文件系统直读"),
    (r"\brequests\.(get|post|put|delete)",
     "外部 HTTP 请求"),
    (r"\bos\.system\s*\(", "系统命令"),
    (r"\bsubprocess\.", "子进程调用"),
    (r"\bDROP\s+TABLE", "DDL 删表"),
    (r"\bALTER\s+TABLE", "DDL 改表"),
    (r"\bDELETE\s+FROM", "数据删除"),
)

# PII 字面量模式(静态关——草稿禁含明文 PII)
PII_PATTERNS = (
    (r"\b\d{17}[\dXx]\b", "疑似身份证号"),
    (r"\b1[3-9]\d{9}\b", "疑似手机号"),
    (r"\b\d{16,19}\b", "疑似银行卡号"),
)

# 国密调用规范(静态关——涉及加密须走站内规范
# 接口而非自造算法; 命中自造加密仅告警不阻断)
CRYPTO_HOME_MADE = (
    r"hashlib\.(md5|sha1)\s*\(",
    r"\bXOR\b.*\bencrypt\b",
)

# 价值关阈值(预估增益 < 此值 → 不通过)
VALUE_GATE = 0.0

# 沙箱执行成本(预算关模拟——影子评估本身
# 消耗小额预算计量; 0 成本因纯离线规则)
SANDBOX_COST = 0.0


class Aiup56TestService:
    """56号测试Agent+信值沙箱(三关影子评估)"""

    def __init__(self):
        self.repo = Aiup56Repository()

    # ============================================================
    # 测试入口
    # ============================================================

    async def test(self, proposal_id: int) -> dict:
        """执行测试+沙箱评估(用例矩阵→三关→
        verdict; 状态 coded→tested/blocked)

        Raises:
            KeyError: 提案不存在
            ValueError: off 态/状态机非法
        """
        from services.aiup56_service import (
            require_active_mode,
        )
        require_active_mode()

        proposal = await self.repo.get_proposal(
            int(proposal_id))
        if proposal is None:
            raise KeyError(f"提案 {proposal_id} 不存在")
        if proposal.get("status") != "coded":
            raise ValueError(
                f"提案状态 {proposal.get('status')}"
                f"(需 coded 方可测试——先触发 code)")

        # 取最新资产
        assets = await self.repo.list_assets(
            proposal_id=int(proposal_id))
        if not assets:
            raise ValueError(
                "提案无资产包(编码Agent 未产出)")
        asset = assets[0]   # 最新版本
        drafts = asset.get("drafts") or []
        test_plans = asset.get("testPlans") or []

        # ① 用例矩阵执行(离线确定性——
        #    草稿结构完整性校验)
        case_results = self._run_case_matrix(
            drafts, test_plans)

        # ② 三关评估
        static_gate = self._static_gate(drafts)
        budget_gate, budget_note = \
            await self._budget_gate(proposal)
        value_gate = self._value_gate(proposal)

        # ③ 沙箱报告
        gates = [static_gate, budget_gate, value_gate]
        if budget_gate.get("verdict") == "halted":
            verdict = "budget_halted"
        elif all(g.get("passed") for g in gates):
            verdict = "passed"
        else:
            verdict = "blocked"

        sandbox_id = await self.repo.next_sandbox_id()
        sandbox = {
            "sandboxId": sandbox_id,
            "proposalId": int(proposal_id),
            "assetId": asset.get("assetId"),
            "assetVersion":
                asset.get("assetVersion"),
            "verdict": verdict,
            "caseMatrix": case_results,
            "staticGate": static_gate,
            "budgetGate": budget_gate,
            "valueGate": value_gate,
            "budgetNote": budget_note,
            "createdAt": ts(),
        }
        await self.repo.save_sandbox(sandbox)

        # ④ 状态翻转
        proposal["status"] = \
            "tested" if verdict == "passed" \
            else "blocked"
        proposal["sandboxId"] = sandbox_id
        proposal["testVerdict"] = verdict
        proposal["budgetSpent"] = round(
            float(proposal.get("budgetSpent") or 0)
            + SANDBOX_COST, 4)
        proposal["updatedAt"] = ts()
        await self.repo.save_proposal(
            proposal, create=False)

        # ⑤ 事件留痕
        await self._track(proposal_id, "test", {
            "sandboxId": sandbox_id,
            "verdict": verdict,
            "cases": len(case_results),
            "staticPassed":
                static_gate.get("passed"),
            "budgetMode":
                budget_gate.get("mode"),
            "valuePassed": value_gate.get("passed"),
        })

        return {
            "success": True,
            "proposalId": int(proposal_id),
            "sandboxId": sandbox_id,
            "verdict": verdict,
            "status": proposal["status"],
            "caseMatrix": case_results,
            "gates": {
                "static": static_gate,
                "budget": budget_gate,
                "value": value_gate,
            },
            "budgetNote": budget_note,
            "note": "测试Agent+信值沙箱——三关影子"
                    "评估(审计Agent P3 接管)",
            "testedAt": ts(),
        }

    # ============================================================
    # ① 用例矩阵执行(离线确定性)
    # ============================================================

    @staticmethod
    def _run_case_matrix(drafts: list,
                         test_plans: list) -> list:
        """用例矩阵执行(草稿结构完整性——
        每测试计划按用例型校验对应草稿)"""
        results = []
        for plan in test_plans:
            task_id = plan.get("taskId")
            draft = next(
                (d for d in drafts
                 if d.get("taskId") == task_id),
                None)
            for case in plan.get("cases") or []:
                case_type = str(
                    case.get("type") or "normal")
                # 离线确定性执行口径:
                #   normal → 草稿存在且非空
                #   boundary → 草稿长度合理
                #              (含关键结构)
                #   exception → 草稿含容错分支
                #              (try/except/None 判)
                if draft is None:
                    passed = False
                    evidence = "草稿缺失"
                else:
                    code = str(
                        draft.get("code") or "")
                    if case_type == "normal":
                        passed = len(code.strip()) > 0
                        evidence = (
                            "草稿非空" if passed
                            else "草稿为空")
                    elif case_type == "boundary":
                        passed = ("def "
                                  in code
                                  and len(code)
                                  > 100)
                        evidence = (
                            "函数结构完整" if passed
                            else "结构不完整")
                    else:   # exception
                        passed = ("try" in code
                                  or "except"
                                  in code
                                  or "None"
                                  in code
                                  or "KeyError"
                                  in code
                                  or "if " in code)
                        evidence = (
                            "容错分支存在" if passed
                            else "无容错逻辑")
                results.append({
                    "taskId": task_id,
                    "case": case.get("name"),
                    "type": case_type,
                    "passed": passed,
                    "evidence": evidence,
                })
        return results

    # ============================================================
    # ② 静态关(敏感 API/PII/国密规范)
    # ============================================================

    @staticmethod
    def _static_gate(drafts: list) -> dict:
        """静态关: 代码草稿规则扫描"""
        violations = []
        warnings = []
        for draft in drafts:
            code = str(draft.get("code") or "")
            title = draft.get("title") or ""
            for pattern, label \
                    in SENSITIVE_PATTERNS:
                if re.search(pattern, code):
                    violations.append(
                        f"[{title}] {label}")
            for pattern, label in PII_PATTERNS:
                if re.search(pattern, code):
                    violations.append(
                        f"[{title}] 明文 {label}")
            for pattern in CRYPTO_HOME_MADE:
                if re.search(pattern, code,
                             re.IGNORECASE):
                    warnings.append(
                        f"[{title}] 自造加密"
                        f"(建议走站内规范)")

        return {
            "name": "静态规则关",
            "passed": not violations,
            "violations": violations,
            "warnings": warnings,
            "scannedDrafts": len(drafts),
            "note": "敏感 API 黑名单+PII 字面量+"
                    "国密规范(55号验签三道关范式)",
        }

    # ============================================================
    # ③ 预算关(提案封顶模拟——熔断)
    # ============================================================

    async def _budget_gate(self,
                           proposal: dict) -> tuple:
        """预算关: 提案级封顶比对(超支熔断
        budget_halted——不抛错, 留痕告警)"""
        spent = float(
            proposal.get("budgetSpent") or 0)
        cap = float(
            proposal.get("budgetCap") or 0.1)
        # 本轮沙箱成本(纯离线规则——0;
        # 后续 real LLM 评估轨计入)
        projected = spent + SANDBOX_COST

        if projected > cap:
            return ({
                "name": "预算封顶关",
                "passed": False,
                "verdict": "halted",
                "spent": round(spent, 4),
                "cap": round(cap, 4),
                "projected":
                    round(projected, 4),
                "note": "超支熔断——人工加额"
                        "(budgetCap)或放弃提案",
            }, "预算超支熔断(告警已留痕)")
        return ({
            "name": "预算封顶关",
            "passed": True,
            "mode": "within_cap",
            "spent": round(spent, 4),
            "cap": round(cap, 4),
            "projected": round(projected, 4),
            "note": "预算充足(提案级封顶内)",
        }, "")

    # ============================================================
    # ④ 价值关(信值增益预估门禁)
    # ============================================================

    @staticmethod
    def _value_gate(proposal: dict) -> dict:
        """价值关: 信值增益预估回放门禁
        (预估增益 < VALUE_GATE → 不通过)"""
        gain = float(
            proposal.get("estimatedGain") or 0)
        passed = gain > VALUE_GATE
        return {
            "name": "信值增益关",
            "passed": passed,
            "estimatedGain": round(gain, 4),
            "gate": VALUE_GATE,
            "note": ("预估增益达标——"
                     "可信增值口径" if passed
                     else "预估增益不足——"
                          "生成过剩风险"),
        }

    # ============================================================
    # 沙箱查询(观测面)
    # ============================================================

    async def list_sandboxes(self,
                             proposal_id: int
                             ) -> dict:
        """沙箱评估列表(观测面)"""
        records = await self.repo.list_sandboxes(
            proposal_id=int(proposal_id))
        return {
            "success": True,
            "proposalId": int(proposal_id),
            "total": len(records),
            "sandboxes": records,
            "note": "信值沙箱——三关影子评估留痕"
                    "(passed/blocked/budget_halted)",
        }

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
                "aiup56_test_track_failed: %s", exc)
