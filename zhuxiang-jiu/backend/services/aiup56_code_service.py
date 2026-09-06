"""56号·AI智能升级管理 编码Agent
(aiup56_code_service, P1)

计划(docs/56号_AI智能升级管理模块实施计划.md §三):
    编码Agent 职责:
    - 代码草稿生成: 严格遵循站点代码风格/安全编码
      准则/国密调用规范(模板约束)
    - 注释即证据: 每段关键代码附带
      # VALUE_REASON: ... 注释(对信值体系的
      贡献依据)
    - 资产版本化: assetVersion 递增(v1→v2 迭代,
      失败重生成不覆盖)
    - 测试计划草稿(用例矩阵——P2 测试Agent 消费)

mock/real 三态: mock 确定性模板渲染(按任务类别);
real LLM 草稿(assist 态+LLM_ENABLED, 失败回退 mock)。

三铁律之一: AI 产出止于版本化资产包(Redis/内存态)
    ——永不直接落盘生产代码; 交付走 P4 人工出口。
"""

import logging
import os

from core.helpers import ts

from repositories.aiup56_repository import (
    Aiup56Repository,
)

logger = logging.getLogger("aiup56_code_service")

MODEL_VERSION = "v1-aiup56-code"

SCORER_ID = "upgrade_orchestration"

# 编码 LLM 调用成本(49号 计量——real 轨)
CODE_LLM_COST = 0.01

# 代码模板库(mock 确定性——按任务类别)
CODE_TEMPLATES = {
    "governance": {
        "language": "python",
        "draft": (
            "# [56号编码Agent草稿] 模型权重复核任务\n"
            "async def review_weights(scorer_id: str):\n"
            "    # VALUE_REASON: 模型健康是信值体系的\n"
            "    # 决策基础——权重漂移复核保障决策可信\n"
            "    from services.ai_learning_service "
            "import get_weights_view\n"
            "    view = await get_weights_view(scorer_id)\n"
            "    champion = view.get('champion') or {}\n"
            "    # 护栏校验([0.5,2.0]×基线)\n"
            "    # VALUE_REASON: 护栏约束防止权重投毒\n"
            "    # ——信值体系的反作弊底线\n"
            "    weights = champion.get('weights') or {}\n"
            "    base = _default_weights(scorer_id)\n"
            "    healthy = all(\n"
            "        base[k] / 2.0 <= weights.get(k, 0)\n"
            "        <= base[k] * 2.0\n"
            "        for k in base)\n"
            "    return {'healthy': healthy,\n"
            "            'version': champion.get('version')}\n"
        ),
        "testPlan": {
            "cases": [
                {"name": "护栏内权重通过",
                 "type": "normal"},
                {"name": "越界权重检出",
                 "type": "boundary"},
                {"name": "空权重容错",
                 "type": "exception"},
            ],
            "sandbox": "价值关回放+预算模拟",
        },
    },
    "usability": {
        "language": "python",
        "draft": (
            "# [56号编码Agent草稿] 体验归因分析任务\n"
            "async def analyze_metric_drop(\n"
            "        metric: str, window: int = 2):\n"
            "    # VALUE_REASON: 用户体验劣化直接侵蚀\n"
            "    # 信值的 L2 平台言行因子——归因定位\n"
            "    # 是修复的第一步\n"
            "    from repositories.qr55_repository "
            "import Qr55Repository\n"
            "    events = await Qr55Repository()\n"
            "    snaps = [e for e in await events\n"
            "             .list_model_events(limit=50)\n"
            "             if e.get('eventType')\n"
            "             == 'metrics_snapshot']\n"
            "    # 两帧环比(计划 §二口径)\n"
            "    if len(snaps) < window:\n"
            "        return None\n"
            "    latest = (snaps[0].get('detail')\n"
            "              or {}).get('metrics', {})\n"
            "    prev = (snaps[1].get('detail')\n"
            "            or {}).get('metrics', {})\n"
            "    return round(float(latest.get(metric)\n"
            "                        or 0)\n"
            "                 - float(prev.get(metric)\n"
            "                         or 0), 4)\n"
        ),
        "testPlan": {
            "cases": [
                {"name": "两帧环比计算",
                 "type": "normal"},
                {"name": "快照不足返回 None",
                 "type": "boundary"},
                {"name": "指标缺失容错",
                 "type": "exception"},
            ],
            "sandbox": "静态规则+价值回放",
        },
    },
    "compliance": {
        "language": "python",
        "draft": (
            "# [56号编码Agent草稿] 合规告警处置任务\n"
            "async def resolve_alert(alert_id: int,\n"
            "                        note: str):\n"
            "    # VALUE_REASON: 合规零事故是信值体系\n"
            "    # 的宪法级目标——告警闭环即合规修复\n"
            "    from repositories.ai_governance_repository \\\n"
            "        import AiGovernance46Repository\n"
            "    repo = AiGovernance46Repository()\n"
            "    alert = await repo.get_alert(alert_id)\n"
            "    if alert is None:\n"
            "        raise KeyError(\n"
            "            f'告警 {alert_id} 不存在')\n"
            "    alert['status'] = 'resolved'\n"
            "    alert['resolveNote'] = note\n"
            "    # VALUE_REASON: 处置留痕可审计——\n"
            "    # 治理透明度是 L2 因子组成\n"
            "    await repo.save_alert(alert, new=False)\n"
            "    return alert\n"
        ),
        "testPlan": {
            "cases": [
                {"name": "告警正常闭环",
                 "type": "normal"},
                {"name": "不存在告警 KeyError",
                 "type": "exception"},
                {"name": "重复处置幂等",
                 "type": "boundary"},
            ],
            "sandbox": "静态规则+合规校验",
        },
    },
    "default": {
        "language": "python",
        "draft": (
            "# [56号编码Agent草稿] 通用任务草稿\n"
            "def execute(context: dict):\n"
            "    # VALUE_REASON: 待规划Agent细化——\n"
            "    # 默认占位实现\n"
            "    return context\n"
        ),
        "testPlan": {
            "cases": [
                {"name": "占位执行", "type": "normal"},
            ],
            "sandbox": "静态规则",
        },
    },
}


class Aiup56CodeService:
    """56号编码Agent(代码草稿+测试计划+资产版本化)"""

    def __init__(self):
        self.repo = Aiup56Repository()

    # ============================================================
    # 编码入口
    # ============================================================

    async def code(self, proposal_id: int) -> dict:
        """执行编码(规划任务→代码草稿+测试计划草稿
        →版本化资产包; 状态 planned→coded)

        Raises:
            KeyError: 提案不存在
            ValueError: off/assist 态校验/状态机非法
        """
        from services.aiup56_service import (
            require_active_mode,
        )
        require_active_mode()

        proposal = await self.repo.get_proposal(
            int(proposal_id))
        if proposal is None:
            raise KeyError(f"提案 {proposal_id} 不存在")
        if proposal.get("status") != "planned":
            raise ValueError(
                f"提案状态 {proposal.get('status')}"
                f"(需 planned 方可编码——先触发 plan)")

        tasks = proposal.get("tasks") or []
        if not tasks:
            raise ValueError(
                "提案无规划任务(规划Agent 未产出)")

        # ① mock 确定性草稿(按任务标题前缀类别)
        draft_bundle, mode = self._render_drafts(
            tasks)

        # ② real LLM 草稿(assist 态——fail-soft)
        llm_cost = 0.0
        if mode == "mock" \
                and self._llm_available():
            llm_draft = await self._llm_draft(
                proposal, draft_bundle)
            if llm_draft is not None:
                draft_bundle = llm_draft
                mode = "real"
                llm_cost = CODE_LLM_COST
                proposal["budgetSpent"] = round(
                    float(proposal.get("budgetSpent")
                          or 0) + llm_cost, 4)

        # ③ 资产版本化落库
        existing = await self.repo.list_assets(
            proposal_id=int(proposal_id))
        next_version = len(existing) + 1
        asset_id = await self.repo.next_asset_id()
        asset = {
            "assetId": asset_id,
            "proposalId": int(proposal_id),
            "assetVersion": next_version,
            "kind": "code_draft",
            "mode": mode,
            "drafts": draft_bundle["drafts"],
            "testPlans": draft_bundle["testPlans"],
            "llmCalls": 1 if mode == "real" else 0,
            "VALUE_REASONs": draft_bundle[
                "valueReasons"],
            "createdAt": ts(),
        }
        await self.repo.save_asset(asset)

        # ④ 提案状态翻转
        proposal["status"] = "coded"
        proposal["assetId"] = asset_id
        proposal["assetVersion"] = next_version
        proposal["codeMode"] = mode
        proposal["updatedAt"] = ts()
        await self.repo.save_proposal(
            proposal, create=False)

        # ⑤ 事件留痕
        await self._track(proposal_id, "code", {
            "assetId": asset_id,
            "assetVersion": next_version,
            "tasks": len(tasks),
            "mode": mode,
            "valueReasonCount":
                len(draft_bundle["valueReasons"]),
        })

        return {
            "success": True,
            "proposalId": int(proposal_id),
            "status": "coded",
            "assetId": asset_id,
            "assetVersion": next_version,
            "mode": mode,
            "draftCount":
                len(draft_bundle["drafts"]),
            "valueReasonCount":
                len(draft_bundle["valueReasons"]),
            "budgetSpent": llm_cost,
            "note": "编码Agent——代码草稿+测试计划"
                    "(VALUE_REASON 注释即证据; "
                    "测试Agent+沙箱 P2 接管)",
            "codedAt": ts(),
        }

    # --------------------------------------------------------
    # mock 确定性草稿渲染
    # --------------------------------------------------------

    @staticmethod
    def _render_drafts(tasks: list) -> tuple:
        """按任务类别分发模板(标题 [kind] 前缀)"""
        drafts = []
        test_plans = []
        value_reasons = []
        for task in tasks:
            title = str(task.get("title") or "")
            kind = "default"
            for k in CODE_TEMPLATES:
                if k != "default" \
                        and title.startswith(f"[{k}]"):
                    kind = k
                    break
            template = CODE_TEMPLATES[kind]
            drafts.append({
                "taskId": task.get("taskId"),
                "title": title,
                "language": template["language"],
                "code": template["draft"],
            })
            test_plans.append({
                "taskId": task.get("taskId"),
                "title": title,
                **template["testPlan"],
            })
            # VALUE_REASON 提取(注释即证据)
            for line in template["draft"].splitlines():
                if "VALUE_REASON" in line:
                    value_reasons.append({
                        "taskId": task.get("taskId"),
                        "reason": line.strip(),
                    })
        return ({"drafts": drafts,
                 "testPlans": test_plans,
                 "valueReasons": value_reasons},
                "mock")

    # --------------------------------------------------------
    # LLM real 轨(fail-soft)
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

    async def _llm_draft(self, proposal: dict,
                          bundle: dict) -> dict | None:
        """LLM 草稿生成(站点代码风格/安全编码/国密
        规范约束——失败返回 None 回退 mock)"""
        try:
            from services.llm_client import (
                provider_client,
            )
            titles = [d["title"] for d
                      in bundle["drafts"]]
            headline = ((proposal.get("summary")
                         or {}).get("headline") or "")
            reply = provider_client().chat(
                system="你是升级编码助手。遵循: "
                       "1)Python 类型注解; 2)仅标准库与"
                       "站内模块; 3)禁 eval/exec/外部"
                       "网络; 4)关键逻辑附 "
                       "VALUE_REASON 注释说明对信值"
                       "体系的贡献。输出纯代码。",
                user=f"任务: {titles}\n提案: {headline}")
            if not reply or not reply.strip():
                return None
            # LLM 产出作为补充草稿(不替换模板——
            # VALUE_REASON 完整性保障)
            result = dict(bundle)
            result["drafts"] = list(bundle["drafts"])
            result["drafts"].append({
                "taskId": 0,
                "title": "[llm] 补充草稿",
                "language": "python",
                "code": str(reply)[:4000],
            })
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_code_llm_skip: %s", exc)
            return None

    # --------------------------------------------------------
    # 资产查询(观测面)
    # --------------------------------------------------------

    async def list_assets(self,
                          proposal_id: int) -> dict:
        """提案资产列表(版本化——观测面)"""
        assets = await self.repo.list_assets(
            proposal_id=int(proposal_id))
        return {
            "success": True,
            "proposalId": int(proposal_id),
            "total": len(assets),
            "assets": assets,
            "note": "版本化资产包(assetVersion 递增——"
                    "失败重生成不覆盖)",
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
                "aiup56_code_track_failed: %s", exc)
