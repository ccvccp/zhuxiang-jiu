"""56号·AI智能升级管理 规划Agent
(aiup56_plan_service, P1)

计划(docs/56号_AI智能升级管理模块实施计划.md §三):
    规划Agent 职责:
    - 任务拆解与依赖分析: 升级需求→原子任务,
      识别代码/数据/配置依赖
    - 信值影响预估: 每任务标注预期隐私预算消耗/
      信值增益/体验变化
    - 回滚预案框架: 每任务预设语义级回滚策略
      (如"新增字段需同步清理历史数据")

mock/real 三态(计划 §〇-①: LLM 不进执行决策链):
    - mock: 确定性任务模板分发——按提案命中信号
      类型映射治理类/体验类/合规类任务模板
    - real: LLM_ENABLED=on 时润色任务描述+依赖
      补全(失败回退 mock——fail-soft)

状态机: draft → planned(plan 产出后)
预算铁律: real 轨按次计量(49号 check_and_spend,
    提案级封顶); mock 轨零成本。
"""

import logging
import os

from core.helpers import ts

from repositories.aiup56_repository import (
    Aiup56Repository,
)

logger = logging.getLogger("aiup56_plan_service")

MODEL_VERSION = "v1-aiup56-plan"

SCORER_ID = "upgrade_orchestration"

# 规划 LLM 调用成本(49号 计量——real 轨)
PLAN_LLM_COST = 0.01

# 信号→任务类别映射(确定性——mock 轨核心)
SIGNAL_TASK_KINDS = {
    "gov46_stagnation": "governance",
    "gov46_drift_high": "governance",
    "scorer_frozen": "governance",
    "pool44_alignment": "governance",
    "us52_usability_drop": "usability",
    "qr55_satisfaction_drop": "usability",
    "qr55_clarify_bloat": "usability",
    "qr55_generation_waste": "usability",
    "gov46_alert_open": "compliance",
    "registry_pending": "compliance",
}

# 任务类别模板(mock 确定性——计划 §三-2/3)
TASK_TEMPLATES = {
    "governance": [
        {
            "taskId": None, "title": "模型权重复核",
            "objective": "对相关评分档案执行权重"
                         "回归检测与复核",
            "dependencies": ["ai_learning_profiles"],
            "depType": "code",
            "privacyCost": 0.005,
            "estimatedGain": 0.5,
            "experienceNote": "决策质量提升——"
                              "间接体验增益",
            "rollback": {
                "strategy": "模型回滚(44号版本历史)",
                "steps": ["POST /model/rollback "
                          "(目标版本)"],
                "dataCleanup": "无数据迁移——"
                               "权重版本化可溯",
            },
        },
        {
            "taskId": None, "title": "反馈管道巡检",
            "objective": "检查回流埋点链路完整性"
                         "(事件→标注→入池)",
            "dependencies": ["ai_learning_feedback"],
            "depType": "data",
            "privacyCost": 0.0,
            "estimatedGain": 0.3,
            "experienceNote": "学习数据质量修复",
            "rollback": {
                "strategy": "无状态变更——无需回滚",
                "steps": [],
                "dataCleanup": "",
            },
        },
    ],
    "usability": [
        {
            "taskId": None, "title": "体验劣化归因分析",
            "objective": "对可用性/满意度下降指标"
                         "做维度归因定位根因",
            "dependencies": ["us52_snapshots",
                            "qr55_metrics"],
            "depType": "data",
            "privacyCost": 0.0,
            "estimatedGain": 0.4,
            "experienceNote": "定位后可定向优化——"
                              "直接体验路径",
            "rollback": {
                "strategy": "只读分析——无需回滚",
                "steps": [],
                "dataCleanup": "",
            },
        },
        {
            "taskId": None, "title": "参数模板调优草案",
            "objective": "按归因结果调整相关服务模板"
                         "参数白名单与有效期",
            "dependencies": ["qr55_registry"],
            "depType": "config",
            "privacyCost": 0.002,
            "estimatedGain": 0.6,
            "experienceNote": "意图命中精度提升——"
                              "澄清轮次下降",
            "rollback": {
                "strategy": "配置还原(旧参数快照)",
                "steps": ["恢复注册表参数基线"],
                "dataCleanup": "新增配置项需同步"
                               "清理引用",
            },
        },
    ],
    "compliance": [
        {
            "taskId": None, "title": "合规告警处置",
            "objective": "处理未决治理告警——"
                         "复核信号根因并闭环",
            "dependencies": ["ai46_alerts"],
            "depType": "process",
            "privacyCost": 0.0,
            "estimatedGain": 0.4,
            "experienceNote": "合规态势恢复——"
                              "审计信任基础",
            "rollback": {
                "strategy": "告警状态可逆"
                           "(open↔resolved)",
                "steps": ["告警复核留痕"],
                "dataCleanup": "",
            },
        },
        {
            "taskId": None, "title": "挂起变更清零",
            "objective": "清理待审批变更队列——"
                         "补齐审批或撤回",
            "dependencies": ["ai46_changes"],
            "depType": "process",
            "privacyCost": 0.0,
            "estimatedGain": 0.3,
            "experienceNote": "治理流转效率恢复",
            "rollback": {
                "strategy": "审批操作留痕可逆"
                           "(重新提交)",
                "steps": [],
                "dataCleanup": "",
            },
        },
    ],
}


class Aiup56PlanService:
    """56号规划Agent(任务拆解+信值预估+回滚预案)"""

    def __init__(self):
        self.repo = Aiup56Repository()

    # ============================================================
    # 规划入口
    # ============================================================

    async def plan(self, proposal_id: int) -> dict:
        """执行规划(提案→任务分解+回滚预案框架
        +信值影响预估; 状态 draft→planned)

        Raises:
            KeyError: 提案不存在
            ValueError: off 态/状态机非法(非 draft)
        """
        from services.aiup56_service import (
            current_mode, require_active_mode,
        )
        require_active_mode()

        proposal = await self.repo.get_proposal(
            int(proposal_id))
        if proposal is None:
            raise KeyError(f"提案 {proposal_id} 不存在")
        if proposal.get("status") != "draft":
            raise ValueError(
                f"提案状态 {proposal.get('status')}"
                f"(需 draft 方可规划)")

        # ① 信号→任务类别(确定性映射)
        snapshot = (proposal.get("signalSnapshot")
                    or {})
        hits = snapshot.get("hits") or []
        kinds = set()
        for h in hits:
            sig_id = str(h.get("signalId") or "")
            kind = SIGNAL_TASK_KINDS.get(sig_id)
            if kind:
                kinds.add(kind)

        # ② mock 确定性任务模板分发
        tasks, mode = self._build_tasks(kinds, proposal)

        # ③ real 润色(LLM fail-soft 回退 mock)
        llm_cost = 0.0
        if mode == "mock" \
                and self._llm_available():
            polished = await self._llm_polish(
                proposal, tasks)
            if polished is not None:
                tasks = polished
                mode = "real"
                llm_cost = PLAN_LLM_COST
                # real 轨计量(49号——系统账号)
                await self._spend_llm_budget(
                    proposal, PLAN_LLM_COST)

        # ④ 任务落库
        task_records = []
        for i, task in enumerate(tasks):
            task_id = await self._next_task_id()
            record = {
                "taskId": task_id,
                "proposalId": int(proposal_id),
                "title": task["title"],
                "objective": task["objective"],
                "dependencies": task["dependencies"],
                "depType": task["depType"],
                "privacyCost": task["privacyCost"],
                "estimatedGain": task["estimatedGain"],
                "experienceNote":
                    task["experienceNote"],
                "rollbackPlan": task["rollback"],
                "status": "planned",
                "createdAt": ts(),
            }
            task_records.append(record)

        # ⑤ 提案状态翻转+汇总更新(tasks 存完整
        # 记录——编码Agent/任务查询直接消费)
        proposal["status"] = "planned"
        proposal["tasks"] = task_records
        proposal["estimatedGain"] = round(sum(
            float(t["estimatedGain"]) for t
            in task_records), 4)
        proposal["plannedTasks"] = len(task_records)
        proposal["planMode"] = mode
        proposal["updatedAt"] = ts()
        await self.repo.save_proposal(
            proposal, create=False)

        # ⑥ 事件留痕
        await self._track(proposal_id, "plan", {
            "tasks": len(task_records),
            "kinds": sorted(kinds),
            "mode": mode,
            "estimatedGain":
                proposal["estimatedGain"],
        })

        return {
            "success": True,
            "proposalId": int(proposal_id),
            "status": "planned",
            "mode": mode,
            "tasks": task_records,
            "estimatedGain":
                proposal["estimatedGain"],
            "budgetSpent": llm_cost,
            "note": "规划Agent——任务拆解+信值预估+"
                    "回滚预案框架(编码Agent P1 接管)",
            "plannedAt": ts(),
        }

    # --------------------------------------------------------
    # mock 确定性任务构建
    # --------------------------------------------------------

    @staticmethod
    def _build_tasks(kinds: set,
                     proposal: dict) -> tuple:
        """任务模板分发(按命中信号类别)"""
        mode = "mock"
        if not kinds:
            # 无类别命中(理论不可达——提案必有命中)
            kinds = {"governance"}
        tasks = []
        for kind in sorted(kinds):
            templates = TASK_TEMPLATES.get(kind, [])
            for t in templates:
                item = dict(t)
                item["title"] = \
                    f"[{kind}] {t['title']}"
                tasks.append(item)
        # 信值影响预估汇总口径(提案级风险预告沿用)
        return tasks, mode

    # --------------------------------------------------------
    # LLM real 轨(fail-soft)
    # --------------------------------------------------------

    @staticmethod
    def _llm_available() -> bool:
        if os.environ.get("AIUP56_MODE") != "assist":
            return False   # 仅辅助开发期走 real
        try:
            from services.llm_client import llm_enabled
            return llm_enabled()
        except Exception:  # noqa: BLE001
            return False

    async def _llm_polish(self, proposal: dict,
                          tasks: list) -> list | None:
        """LLM 润色任务描述+依赖补全(失败返回
        None——mock 保持)"""
        try:
            from services.llm_client import (
                provider_client,
            )
            brief = (
                f"提案: {proposal.get('summary', {})
                          .get('headline', '')}\n"
                f"任务草案: "
                f"{[t['title'] for t in tasks]}")
            reply = provider_client().chat(
                system="你是升级规划助手。润色以下"
                       "任务描述并补充遗漏的依赖项"
                       "(JSON 数组: [{title, "
                       "objective}]). 只使用用户"
                       "提供的数据, 不编造。",
                user=brief)
            if not reply or not reply.strip():
                return None
            import json
            import re
            match = re.search(r"\[.*\]", reply, re.S)
            if not match:
                return None
            polished = json.loads(match.group())
            result = []
            for i, p in enumerate(polished[:len(tasks)]):
                item = dict(tasks[i])
                if p.get("title"):
                    item["title"] = str(p["title"])
                if p.get("objective"):
                    item["objective"] = \
                        str(p["objective"])
                result.append(item)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_plan_llm_skip: %s", exc)
            return None

    @staticmethod
    async def _spend_llm_budget(proposal: dict,
                                 cost: float) -> None:
        """LLM 调用预算计量(提案级封顶内——
        超支 fail-soft 记 poolError 留痕)"""
        try:
            spent = float(
                proposal.get("budgetSpent") or 0)
            cap = float(
                proposal.get("budgetCap") or 0.1)
            if spent + cost > cap:
                logger.warning(
                    "aiup56_plan_budget_cap_hit: "
                    "spent=%s cost=%s cap=%s",
                    spent, cost, cap)
                return
            proposal["budgetSpent"] = \
                round(spent + cost, 4)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_plan_budget_failed: %s", exc)

    # --------------------------------------------------------
    # 任务查询(观测面)
    # --------------------------------------------------------

    async def list_tasks(self,
                         proposal_id: int) -> dict:
        """提案任务列表(观测面)"""
        proposal = await self.repo.get_proposal(
            int(proposal_id))
        if proposal is None:
            raise KeyError(f"提案 {proposal_id} 不存在")
        tasks = [
            {"taskId": t.get("taskId"),
             "title": t.get("title"),
             "objective": t.get("objective"),
             "depType": t.get("depType"),
             "privacyCost": t.get("privacyCost"),
             "estimatedGain": t.get("estimatedGain"),
             "rollbackPlan": t.get("rollbackPlan"),
             "status": t.get("status")}
            for t in proposal.get("tasks") or []]
        return {
            "success": True,
            "proposalId": int(proposal_id),
            "total": len(tasks),
            "tasks": tasks,
            "estimatedGain":
                proposal.get("estimatedGain"),
            "planMode": proposal.get("planMode"),
            "note": "规划产出(任务+依赖+信值预估+"
                    "回滚预案框架)",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _next_task_id(self) -> int:
        return await self.repo._next_seq("tasks")

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
                "aiup56_plan_track_failed: %s", exc)
