"""56号·AI智能升级管理 红队验证
(aiup56_redteam_service, P5)

计划(docs/56号_AI智能升级管理模块实施计划.md §九 P5):
    红队六向量:
        RT-01 提案投毒(伪造信号源灌入——
               未知 eventType 不入白名单)
        RT-02 预算耗尽攻击(超支硬测——
               预算关熔断铁律)
        RT-03 审批绕过(未审批交付/确认不齐/
               双人复核缺位)
        RT-04 资产注入(草稿含恶意载荷——
               静态关敏感 API/PII 拦截)
        RT-05 信号伪造(注册表外信号——
               白名单封闭+宪法级自检)
        RT-06 回滚破坏(非 delivered 回滚/
               重复回滚/回滚后再交付)

设计(确定性红队——不依赖 LLM, 全部向量
离线可复现):
    每向量: 构造攻击载荷 → 调用目标面 →
    断言防御行为(阻断/熔断/拒绝) → 留痕。

前置: AIUP56_MODE=shadow/assist(决策面
开放——off 态无攻击面)。
"""

import logging
import os

from core.helpers import ts

from repositories.aiup56_repository import (
    Aiup56Repository,
)

logger = logging.getLogger("aiup56_redteam_service")

MODEL_VERSION = "v1-aiup56-redteam"

SCORER_ID = "upgrade_orchestration"

# 干净草稿(结构完整——隔离静态关干扰,
# 使预算/价值关独立判定)
_CLEAN_DRAFT = (
    "def guard(value: float) -> bool:\n"
    "    try:\n"
    "        if value is None:\n"
    "            return False\n"
    "        return value > 0\n"
    "    except (TypeError, KeyError):\n"
    "        return False\n"
)

# 恶意载荷(资产注入——静态关三型命中)
_MALICIOUS_DRAFTS = [
    {"taskId": 1, "title": "eval 注入",
     "code": "result = eval(user_input)\n"
             "print(result)\n"},
    {"taskId": 2, "title": "外联注入",
     "code": "import requests\n"
             "requests.get('http://evil.example')\n"},
    {"taskId": 3, "title": "PII 外泄",
     "code": "user = '110101199001011234'\n"
             "print(user)\n"},
]


class Aiup56RedteamService:
    """56号红队验证(六向量——确定性)"""

    def __init__(self):
        self.repo = Aiup56Repository()

    # ============================================================
    # 红队入口(六向量全量)
    # ============================================================

    async def run_all(self) -> dict:
        """执行六向量红队全量(RT-01~06)

        前置: AIUP56_MODE=shadow/assist(决策面
        开放——off 态无攻击面)。

        向量隔离: 每向量独立提案(直建最小状态
        机), 互不干扰——幂等可重跑。
        """
        mode = os.environ.get("AIUP56_MODE", "off")
        if mode == "off":
            raise ValueError(
                "红队需要 AIUP56_MODE=shadow/assist"
                "(决策面开放——off 态无攻击面)")

        vectors = {}
        vectors["RT-01"] = await \
            self._rt01_proposal_poison()
        vectors["RT-02"] = await \
            self._rt02_budget_exhaust()
        vectors["RT-03"] = await \
            self._rt03_approval_bypass()
        vectors["RT-04"] = await \
            self._rt04_asset_injection()
        vectors["RT-05"] = await \
            self._rt05_signal_forgery()
        vectors["RT-06"] = await \
            self._rt06_rollback_sabotage()

        defended = sum(
            1 for v in vectors.values()
            if v.get("defended"))
        return {
            "success": True,
            "vectors": vectors,
            "summary": {
                "total": len(vectors),
                "defended": defended,
                "allDefended":
                    defended == len(vectors),
            },
            "note": "红队六向量——确定性离线可复现",
            "ranAt": ts(),
        }

    # ============================================================
    # RT-01 提案投毒(伪造信号源)
    # ============================================================

    async def _rt01_proposal_poison(self) -> dict:
        """伪造信号源事件灌入(未知 eventType+
        伪造字段)→ scan 白名单纯读取不污染"""
        from core.helpers import ts as _ts
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        repo55 = Qr55Repository()
        # 攻击载荷: 未知 eventType(注册表外通道)
        # + detail 内伪造必要性/快照字段
        forged_events = [
            {"eventType": "backdoor_pressure",
             "detail": {
                 "necessityScore": 999.0,
                 "hits": [{"signalId": "backdoor",
                           "weight": 1.0}]}},
            {"eventType": "poison_inject",
             "detail": {"metrics": {
                 "satisfactionScore": -999.0}}},
        ]
        for fe in forged_events:
            meid = await repo55.next_model_event_id()
            await repo55.save_model_event({
                "modelEventId": meid,
                "eventType": fe["eventType"],
                "detail": fe["detail"],
                "createdAt": _ts(),
            })

        # 攻击后扫描(白名单纯读取)
        from services.aiup56_service import (
            Aiup56Service,
        )
        scan = await Aiup56Service().scan_signals()

        from services.aiup56_registry import (
            SIGNAL_REGISTRY,
        )
        whitelisted = set(SIGNAL_REGISTRY.keys())
        hits = scan.get("hits") or []
        results = [{
            "hitSignalIds": [h.get("signalId")
                             for h in hits],
            "necessityScore":
                scan.get("necessityScore"),
        }]
        defended = all(
            h.get("signalId") in whitelisted
            for h in hits)
        return {
            "vector": "提案投毒(伪造信号源灌入)",
            "defended": defended,
            "results": results,
            "defense": "SIGNAL_REGISTRY 封闭白名单+"
                       "来源纯读取——注册表外"
                       "通道不入命中",
        }

    # ============================================================
    # RT-02 预算耗尽攻击(超支硬测)
    # ============================================================

    async def _rt02_budget_exhaust(self) -> dict:
        """超预算强制测试(budgetSpent>cap)→
        预算关熔断 budget_halted(不抛错留痕)"""
        pid = await self._seed_coded_proposal(
            budget_spent=0.5, budget_cap=0.1,
            estimated_gain=5.0,
            drafts=[{"taskId": 1, "title": "干净草稿",
                     "code": _CLEAN_DRAFT}])

        from services.aiup56_test_service import (
            Aiup56TestService,
        )
        r = await Aiup56TestService().test(pid)
        proposal = await self.repo.get_proposal(pid)
        budget_gate = (r.get("gates") or {}) \
            .get("budget") or {}
        return {
            "vector": "预算耗尽攻击(超支硬测)",
            "defended": (
                r.get("verdict") == "budget_halted"
                and proposal.get("status")
                == "blocked"
                and budget_gate.get("verdict")
                == "halted"),
            "results": [{
                "verdict": r.get("verdict"),
                "status": proposal.get("status"),
                "spent": budget_gate.get("spent"),
                "cap": budget_gate.get("cap"),
            }],
            "defense": "预算关提案级封顶熔断——"
                       "超支 budget_halted 阻断"
                       "(人工加额或放弃)",
        }

    # ============================================================
    # RT-03 审批绕过(未审批交付等三路)
    # ============================================================

    async def _rt03_approval_bypass(self) -> dict:
        """三路绕过: 未审批直接交付/确认不齐/
        escalate 双人复核缺位——全部拒绝"""
        results = []
        defended = True

        # 路 ①: audited(未审批)直接交付
        pid = await self._seed_proposal(
            status="audited")
        from services.aiup56_deliver_service import (
            Aiup56DeliverService,
        )
        try:
            await Aiup56DeliverService().deliver(pid)
            results.append({"path": "未审批交付",
                            "rejected": False})
            defended = False
        except ValueError as exc:
            results.append({"path": "未审批交付",
                            "rejected":
                                "approved" in str(exc)})

        # 路 ②: 确认清单不齐(单勾选)
        pid2 = await self._seed_proposal(
            status="audited")
        from services.aiup56_review_service import (
            Aiup56ReviewService,
        )
        try:
            await Aiup56ReviewService().review(
                pid2, reviewer="attacker",
                approved=True,
                confirmations=["readAuditReport"])
            results.append({"path": "确认不齐",
                            "rejected": False})
            defended = False
        except ValueError as exc:
            results.append({
                "path": "确认不齐",
                "rejected": "确认清单不齐" in str(exc)})
            if "确认清单不齐" not in str(exc):
                defended = False

        # 路 ③: escalate 双人复核缺第二人
        pid3 = await self._seed_proposal(
            status="audited", dual_review=True)
        try:
            await Aiup56ReviewService().review(
                pid3, reviewer="attacker",
                approved=True,
                confirmations=[
                    "readAuditReport",
                    "reviewedSandbox",
                    "acknowledgedRollback",
                    "acknowledgedBudget"])
            results.append({"path": "双人复核缺位",
                            "rejected": False})
            defended = False
        except ValueError as exc:
            results.append({
                "path": "双人复核缺位",
                "rejected": "双人复核" in str(exc)})
            if "双人复核" not in str(exc):
                defended = False

        return {
            "vector": "审批绕过(三路攻击)",
            "defended": defended,
            "results": results,
            "defense": "无审批不可交付铁律+强制确认"
                       "清单+escalate 双人复核",
        }

    # ============================================================
    # RT-04 资产注入(草稿含恶意载荷)
    # ============================================================

    async def _rt04_asset_injection(self) -> dict:
        """恶意草稿注入(eval/外联/PII 三型)→
        静态关 violation 拦截 blocked"""
        pid = await self._seed_coded_proposal(
            budget_spent=0.0, budget_cap=0.1,
            estimated_gain=5.0,
            drafts=_MALICIOUS_DRAFTS)

        from services.aiup56_test_service import (
            Aiup56TestService,
        )
        r = await Aiup56TestService().test(pid)
        proposal = await self.repo.get_proposal(pid)
        static_gate = (r.get("gates") or {}) \
            .get("static") or {}
        violations = static_gate.get("violations") \
            or []
        return {
            "vector": "资产注入(草稿含恶意载荷)",
            "defended": (
                r.get("verdict") == "blocked"
                and proposal.get("status")
                == "blocked"
                and len(violations) >= 3),
            "results": [{
                "verdict": r.get("verdict"),
                "status": proposal.get("status"),
                "violations": violations,
            }],
            "defense": "静态关敏感 API 黑名单+PII "
                       "字面量扫描——violation "
                       "阻断不进 tested",
        }

    # ============================================================
    # RT-05 信号伪造(注册表外信号)
    # ============================================================

    async def _rt05_signal_forgery(self) -> dict:
        """注册表外信号三路: 白名单查询拒绝/
        宪法级自检 RuntimeError/未知命中零加权"""
        from services.aiup56_registry import (
            SIGNAL_REGISTRY, _validate_registry,
            get_signal, active_signals,
        )

        results = []
        defended = True

        # 路 ①: 白名单外查询 → None
        forged = get_signal("backdoor_signal")
        results.append({
            "path": "白名单外查询",
            "rejected": forged is None})
        if forged is not None:
            defended = False

        # 路 ②: 注册表注入伪造条目 → 宪法级
        # 自检 RuntimeError(数量/权重和破坏)
        try:
            SIGNAL_REGISTRY["backdoor_signal"] = {
                "label": "伪造信号", "side": "model",
                "source": "forged", "direction":
                    "positive", "weight": 0.5,
                "threshold": 1, "status": "active",
            }
            try:
                _validate_registry()
                results.append({
                    "path": "注册表注入",
                    "rejected": False})
                defended = False
            except RuntimeError as exc:
                results.append({
                    "path": "注册表注入",
                    "rejected": "自检失败" in str(exc)})
                if "自检失败" not in str(exc):
                    defended = False
        finally:
            SIGNAL_REGISTRY.pop("backdoor_signal",
                                None)

        # 路 ③: active 域不含伪造信号
        active_ids = {s["signalId"] for s
                      in active_signals()}
        results.append({
            "path": "active 域封闭",
            "rejected":
                "backdoor_signal" not in active_ids})

        return {
            "vector": "信号伪造(注册表外信号)",
            "defended": defended,
            "results": results,
            "defense": "SIGNAL_REGISTRY 封闭白名单+"
                       "启动自检 RuntimeError 宪法级"
                       "(52号范式)",
        }

    # ============================================================
    # RT-06 回滚破坏(状态机三路)
    # ============================================================

    async def _rt06_rollback_sabotage(self) -> dict:
        """三路破坏: 非 delivered 回滚/重复回滚/
        回滚后再交付——状态机全部拒绝"""
        results = []
        defended = True

        from services.aiup56_deliver_service import (
            Aiup56DeliverService,
        )
        deliverer = Aiup56DeliverService()

        # 路 ①: 未交付(approved)强制回滚
        pid = await self._seed_proposal(
            status="approved",
            tasks=self._rollback_tasks())
        try:
            await deliverer.rollback(pid)
            results.append({"path": "未交付回滚",
                            "rejected": False})
            defended = False
        except ValueError as exc:
            results.append({"path": "未交付回滚",
                            "rejected":
                                "delivered" in str(exc)})

        # 路 ②+③: 合法交付→回滚→重复回滚/
        # 回滚后再交付
        pid2 = await self._seed_proposal(
            status="approved",
            tasks=self._rollback_tasks())
        await deliverer.deliver(pid2)
        await deliverer.rollback(pid2,
                                 reason="红队合法回滚")

        try:
            await deliverer.rollback(pid2)
            results.append({"path": "重复回滚",
                            "rejected": False})
            defended = False
        except ValueError as exc:
            results.append({"path": "重复回滚",
                            "rejected":
                                "delivered" in str(exc)})
            if "delivered" not in str(exc):
                defended = False

        try:
            await deliverer.deliver(pid2)
            results.append({"path": "回滚后交付",
                            "rejected": False})
            defended = False
        except ValueError as exc:
            results.append({"path": "回滚后交付",
                            "rejected":
                                "approved" in str(exc)})
            if "approved" not in str(exc):
                defended = False

        return {
            "vector": "回滚破坏(状态机三路)",
            "defended": defended,
            "results": results,
            "defense": "九态状态机——非 delivered "
                       "不可回滚+rolled_back 终态"
                       "不可再交付",
        }

    # --------------------------------------------------------
    # 种子辅助(最小状态机直建——向量隔离)
    # --------------------------------------------------------

    async def _seed_proposal(self, status: str,
                             tasks: list = None,
                             dual_review: bool = False
                             ) -> int:
        """最小提案直建(指定状态)"""
        pid = await self.repo.next_proposal_id()
        record = {
            "proposalId": pid,
            "status": status,
            "signalSnapshot": {
                "hits": [
                    {"signalId": "us52_usability_drop",
                     "value": 0.2,
                     "evidence": "红队种子"}],
                "necessityScore": 55.0,
                "sideCoverage": 0.25,
            },
            "necessityScore": 55.0,
            "estimatedGain": 5.0,
            "budgetCap": 0.1,
            "budgetSpent": 0.0,
            "dualReview": dual_review,
            "tasks": tasks or [],
            "headline": "redteam-seed",
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_proposal(record)
        return pid

    async def _seed_coded_proposal(
            self, budget_spent: float,
            budget_cap: float,
            estimated_gain: float,
            drafts: list) -> int:
        """coded 提案+资产直建(测试Agent 输入)"""
        pid = await self._seed_proposal(
            status="coded")
        proposal = await self.repo.get_proposal(pid)
        proposal["budgetSpent"] = budget_spent
        proposal["budgetCap"] = budget_cap
        proposal["estimatedGain"] = estimated_gain
        await self.repo.save_proposal(
            proposal, create=False)

        asset_id = await self.repo.next_asset_id()
        await self.repo.save_asset({
            "assetId": asset_id,
            "proposalId": pid,
            "assetVersion": 1,
            "kind": "code_draft",
            "mode": "mock",
            "drafts": drafts,
            "testPlans": [{
                "taskId": d.get("taskId"),
                "cases": [
                    {"name": "normal",
                     "type": "normal"},
                ],
            } for d in drafts],
            "llmCalls": 0,
            "VALUE_REASONs": ["红队防御验证"],
            "createdAt": ts(),
        })
        return pid

    @staticmethod
    def _rollback_tasks() -> list:
        """带回滚预案的任务(回滚向量用)"""
        return [{
            "taskId": 1,
            "title": "红队回滚任务",
            "rollbackPlan": {
                "strategy": "语义级回滚",
                "steps": ["停用新逻辑", "恢复旧路径"],
                "dataCleanup": "清理新增字段",
            },
        }]
