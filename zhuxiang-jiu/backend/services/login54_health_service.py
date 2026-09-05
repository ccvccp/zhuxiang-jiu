"""54号·小竹AI智能登录引擎大模型 漂移监控+健康治理
(login54_health_service, P4)

计划(docs/54号_小竹AI智能登录引擎大模型实施计划.md §六 P4):
    - EMA 漂移监控(DRIFT_ALPHA 0.1 复用)——
      44号 get_drift_view 包装+高位告警事件留痕
    - 46号三检测器接入(stagnation/exhaustion/
      high drift——_collect+detect 单档案评估)
    - 冻结守卫+变更审批联动(46号 is_frozen——
      学习拦截已在 P2/P3 覆盖, 此处观测面呈现)
    - LLM 归因报告(权重变更自然语言解释——
      LLM_ENABLED 依赖+fail-soft, 44号
      api_intelligence 三态范式: mock 确定性
      模板/real 润色, 数字永远来自数据层)

QC(计划 §六 P4):
    - 漂移告警: driftLevel=high → drift_alert
      模型事件留痕(测试覆盖)
    - 冻结期间学习跳过: P2 已断言, 此处
      治理视图呈现 frozen 状态(测试覆盖)

零侵入红线: 44号/46号 零改动(纯调用式包装)。
"""

import logging
from datetime import datetime, UTC

from core.helpers import ts

logger = logging.getLogger("login54_health_service")

MODEL_VERSION = "v1-login54-health"

SCORER_ID = "login_orchestration"

# 漂移高位阈值(44号 DRIFT_LEVELS high=0.25 对齐)
DRIFT_HIGH = "high"

# 归因回溯窗口(最近 N 条权重变更事件)
ATTRIBUTION_EVENTS = 3


class Login54HealthService:
    """54号漂移监控+健康治理(44号漂移+46号三检测器+
    LLM 归因——纯调用式包装)"""

    # --------------------------------------------------------
    # EMA 漂移监控(44号 get_drift_view 复用)
    # --------------------------------------------------------

    async def drift_view(self) -> dict:
        """漂移视图(44号 DRIFT_ALPHA 0.1 EMA——
        基线/EMA/漂移分/档位; high → drift_alert
        模型事件留痕(QC 漂移告警))"""
        from services.ai_learning_service import (
            get_drift_view,
        )
        view = await get_drift_view(SCORER_ID)
        drift = view.get("drift") or {}
        level = str(drift.get("driftLevel") or "low")

        alerted = False
        if level == DRIFT_HIGH:
            # 漂移高位 → 告警事件留痕(当日同事件
            # 去重由事件明细承载——重复调用幂等留痕)
            try:
                from services.login54_service import (
                    Login54Service,
                )
                await Login54Service().record_model_event(
                    "drift_alert", {
                        "scorerId": SCORER_ID,
                        "driftScore": drift.get(
                            "driftScore"),
                        "driftLevel": level,
                        "count": drift.get("count"),
                        "baselineScore": drift.get(
                            "baselineScore"),
                        "emaScore": drift.get("emaScore"),
                    })
                alerted = True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "login54_drift_alert_failed: %s",
                    exc)

        view.update({
            "module": "login54",
            "alerted": alerted,
            "note": "44号 EMA(DRIFT_ALPHA 0.1)复用——"
                    "high 档留痕 drift_alert 告警",
            "viewedAt": ts(),
        })
        return view

    # --------------------------------------------------------
    # 46号三检测器接入(单档案健康评估)
    # --------------------------------------------------------

    async def governance_health(self) -> dict:
        """模型健康(46号三检测器: stagnation 版本
        停滞/exhaustion 反馈枯竭/high drift 漂移——
        46号 _collect+detect+health_score 复用)+
        冻结状态+变更审批通道"""
        from repositories.ai_governance_repository \
            import AiGovernance46Repository
        from services.ai_governance_health import (
            AiGovernanceHealthService,
        )

        repo = AiGovernance46Repository()
        gov = await repo.get_gov(SCORER_ID)
        if gov is None:
            # 冷态自愈(sync 幂等——治理状态保留)
            from services.ai_governance_service import (
                AiGovernanceService,
            )
            await AiGovernanceService().sync_registry()
            gov = await repo.get_gov(SCORER_ID)
        if gov is None:
            return {
                "success": False,
                "error": "档案未入册(sync 失败)",
                "scorerId": SCORER_ID,
            }

        svc = AiGovernanceHealthService(repo=repo)
        entry = await svc._assess(
            gov, datetime.now(UTC))
        frozen = gov.get("status") == "frozen"

        return {
            "success": True,
            "module": "login54",
            "scorerId": SCORER_ID,
            "health": entry,
            "governance": {
                "status": gov.get("status"),
                "frozen": frozen,
                "ownerNote": gov.get("ownerNote") or "",
                "changeBus": {
                    "submit":
                        "POST /api/ai-gov/changes"
                        "(freeze/unfreeze 走审批总线)",
                    "note": "冻结期间学习跳过"
                            "(44号引擎内建守卫)",
                },
                "redlines": [
                    "冻结守卫: is_frozen → 学习拦截",
                    "变更审批: 权重变更走 46号总线留痕",
                    "版本可溯: login54_model_events",
                    "一键回滚: POST /model/rollback",
                    "漂移告警: drift_alert 事件留痕",
                ],
            },
            "note": "46号三检测器复用——停滞/枯竭/漂移",
            "checkedAt": ts(),
        }

    # --------------------------------------------------------
    # LLM 归因报告(权重变更自然语言解释)
    # --------------------------------------------------------

    async def attribution(self) -> dict:
        """归因报告(最近权重变更 → 自然语言解释)

        三态(44号 api_intelligence 范式):
            - mock: 确定性模板(数字全部来自数据层
              ——事件 weightDelta/版本对/反馈数)
            - real: LLM_ENABLED=on 时润色(失败回退
              mock——fail-soft)

        Raises:
            ValueError: 无可归因的权重变更事件
        """
        from repositories.login54_repository import (
            Login54Repository,
        )
        events = await Login54Repository(
        ).list_model_events(limit=50)
        weight_events = [
            e for e in events
            if e.get("eventType") in (
                "learning", "promoted", "rollback")
        ]
        if not weight_events:
            raise ValueError(
                "暂无可归因的权重变更事件"
                "(先触发 POST /api/login54/model/learn)")

        recent = weight_events[:ATTRIBUTION_EVENTS]
        latest = recent[0]
        detail = latest.get("detail") or {}

        # 数据层事实(数字唯一来源)
        facts = self._extract_facts(recent)

        # mock 确定性模板
        mode = "mock"
        answer = self._mock_narrative(facts)

        # real 润色(fail-soft)
        from services.llm_client import llm_enabled
        if llm_enabled():
            try:
                from services.llm_client import (
                    provider_client,
                )
                reply = provider_client().chat(
                    system="你是登录风控模型治理助手。"
                           "用不超过 4 句中文解释权重"
                           "变更原因。只使用用户提供的"
                           "数据, 不编造任何数字。",
                    user=f"模型事实(以此为准):\n{answer}")
                if reply and reply.strip():
                    answer = reply.strip()
                    mode = "real"
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "login54_attribution_llm_skip: %s",
                    exc)

        return {
            "success": True,
            "scorerId": SCORER_ID,
            "mode": mode,
            "attribution": answer,
            "facts": facts,
            "eventCount": len(weight_events),
            "note": "数字来自数据层, LLM 仅润色" if mode
                    == "real" else "mock 确定性模板"
                            "(LLM_ENABLED=on 开启润色)",
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # 归因数据层(数字唯一来源)
    # --------------------------------------------------------

    @staticmethod
    def _extract_facts(events: list) -> dict:
        """从权重变更事件提取事实(版本对/权重增量/
        反馈数/晋升态)"""
        latest = events[0]
        detail = latest.get("detail") or {}
        delta = detail.get("weightDelta") or {}
        # 主要变化因子(|Δ| 最大前三)
        top = sorted(
            delta.items(),
            key=lambda kv: abs(float(kv[1] or 0)),
            reverse=True)[:3]
        return {
            "eventType": latest.get("eventType"),
            "parentVersion":
                detail.get("parentVersion"),
            "newVersion":
                detail.get("newVersion")
                or detail.get("toVersion"),
            "learnedFrom":
                detail.get("learnedFrom"),
            "promoted": detail.get("promoted"),
            "topWeightChanges": [
                {"factor": k,
                 "delta": round(float(v), 4)}
                for k, v in top],
            "recentEvents": [
                {"type": e.get("eventType"),
                 "version": (e.get("detail") or {})
                 .get("newVersion")
                 or (e.get("detail") or {})
                 .get("toVersion"),
                 "at": e.get("createdAt")}
                for e in events],
        }

    @staticmethod
    def _mock_narrative(facts: dict) -> str:
        """mock 确定性归因(数字全部来自数据层)"""
        et = facts.get("eventType") or "unknown"
        parent = facts.get("parentVersion") or "-"
        new = facts.get("newVersion") or "-"
        learned = facts.get("learnedFrom")
        promoted = facts.get("promoted")

        if et == "rollback":
            head = (f"模型版本由 {parent} 回滚至 {new}"
                    f"(回归防护或人工指令)")
        else:
            head = (f"模型版本由 {parent} 演进至 {new}")
            if learned is not None:
                head += (f"(本轮学习 {learned} 条回流反馈"
                         "驱动 Hedge 更新)")
            if promoted:
                head += ", 评估更优已自动晋升"
            else:
                head += ", 以挑战者影子运行"

        parts = [head]
        for ch in facts.get("topWeightChanges") or []:
            d = float(ch.get("delta") or 0)
            if abs(d) < 1e-9:
                continue
            direction = "上调" if d > 0 else "下调"
            parts.append(
                f"因子 {ch.get('factor')} 权重{direction} "
                f"{abs(d):.4f}")
        parts.append(
            "全部变化受 [0.5,2.0] 倍护栏约束并已归一化, "
            "可通过模型事件历史逐版本溯源。")
        return "; ".join(parts)
