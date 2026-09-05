"""55号·二维码AI智能管理 LLM 归因报告
(qr55_attribution_service, P4)

计划(docs/55号_二维码AI智能管理模块实施计划.md §六 P4):
    LLM 归因报告(mock/real 三态——数字来自数据层)

三态(44号 api_intelligence 范式, 54号
login54_health_service.attribution 同范式):
    - mock: 确定性模板(数字全部来自数据层——
      模型事件 weightDelta/版本对/回流信号分布)
    - real: LLM_ENABLED=on 时润色(失败回退
      mock——fail-soft)
    - 无可归因事件 → ValueError

归因域(55号特有):
    权重变更(learning/promoted/rollback/
    regression_rollback)+回流信号分布+
    六指标快照——生码决策质量的完整因果链。
"""

import logging

from core.helpers import ts

from repositories.qr55_repository import (
    Qr55Repository,
)

logger = logging.getLogger("qr55_attribution_service")

MODEL_VERSION = "v1-qr55-attribution"

SCORER_ID = "qr_orchestration"

# 归因事件窗口(最近 N 条模型事件)
ATTRIBUTION_EVENTS = 5

# 权重变更事件类型(归因域)
WEIGHT_EVENT_TYPES = (
    "learning", "promoted", "rollback",
    "regression_rollback")


class Qr55AttributionService:
    """55号 LLM 归因报告(权重变更+回流信号+
    六指标——数字来自数据层)"""

    def __init__(self):
        self.repo = Qr55Repository()

    # ============================================================
    # 归因报告入口
    # ============================================================

    async def attribution(self) -> dict:
        """归因报告(最近权重变更+回流信号分布+指标
        → 自然语言解释)

        三态: mock 确定性模板 / real LLM 润色
        (LLM_ENABLED=on, 失败回退 mock——fail-soft)

        Raises:
            ValueError: 无可归因的权重变更事件
        """
        events = await self.repo.list_model_events(
            limit=50)
        weight_events = [
            e for e in events
            if e.get("eventType") in WEIGHT_EVENT_TYPES]
        if not weight_events:
            raise ValueError(
                "暂无可归因的权重变更事件"
                "(先触发 POST /api/qr55/model/learn)")

        recent = weight_events[:ATTRIBUTION_EVENTS]

        # 数据层事实(数字唯一来源)
        facts = await self._extract_facts(recent)

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
                    system="你是二维码智能生码模型治理"
                           "助手。用不超过 4 句中文解释"
                           "模型权重变更原因与回流信号"
                           "结构。只使用用户提供的数据, "
                           "不编造任何数字。",
                    user=f"模型事实(以此为准):\n{answer}")
                if reply and reply.strip():
                    answer = reply.strip()
                    mode = "real"
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "qr55_attribution_llm_skip: %s", exc)

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

    # ============================================================
    # 归因数据层(数字唯一来源)
    # ============================================================

    async def _extract_facts(self, events: list) -> dict:
        """从权重变更事件+回流统计+指标提取事实"""
        latest = events[0]
        detail = latest.get("detail") or {}
        delta = detail.get("weightDelta") or {}

        # 主要变化因子(|Δ| 最大前三)
        top = sorted(
            delta.items(),
            key=lambda kv: abs(float(kv[1] or 0)),
            reverse=True)[:3]

        # 回流信号分布(最近一轮 collect 留痕)
        collect_events = [
            e for e in await self.repo.list_model_events(
                limit=50)
            if e.get("eventType")
            == "feedback_collect"]
        last_collect = ((collect_events[0]
                         .get("detail")) or {}) \
            if collect_events else {}
        signals = last_collect.get("signals") or {}

        # 六指标(最近快照)
        snapshot_events = [
            e for e in await self.repo.list_model_events(
                limit=50)
            if e.get("eventType")
            == "metrics_snapshot"]
        last_metrics = ((snapshot_events[0]
                         .get("detail") or {})
                        .get("metrics")) or {} \
            if snapshot_events else {}

        return {
            "eventType": latest.get("eventType"),
            "parentVersion":
                detail.get("parentVersion")
                or detail.get("fromVersion"),
            "newVersion":
                detail.get("newVersion")
                or detail.get("toVersion"),
            "learnedFrom":
                detail.get("learnedFrom"),
            "promoted": detail.get("promoted"),
            "channel": detail.get("channel"),
            "topWeightChanges": [
                {"factor": k,
                 "delta": round(float(v), 4)}
                for k, v in top],
            "feedbackSignals": signals,
            "metrics": last_metrics,
            "recentEvents": [
                {"type": e.get("eventType"),
                 "version": (e.get("detail") or {})
                 .get("newVersion")
                 or (e.get("detail") or {})
                 .get("toVersion"),
                 "at": e.get("createdAt")}
                for e in events],
        }

    # ============================================================
    # mock 确定性归因(数字全部来自数据层)
    # ============================================================

    @staticmethod
    def _mock_narrative(facts: dict) -> str:
        et = facts.get("eventType") or "unknown"
        parent = facts.get("parentVersion") or "-"
        new = facts.get("newVersion") or "-"
        learned = facts.get("learnedFrom")
        promoted = facts.get("promoted")
        channel = facts.get("channel")

        # 头部(事件类型语义)
        if et == "regression_rollback":
            head = (f"模型版本 {new} 因滑动窗口指标"
                    f"回退被自动回滚并冻结"
                    f"(人工复核解锁)")
        elif et == "rollback":
            head = (f"模型版本由 {parent} 回滚至 {new}"
                    f"({'自动' if channel == 'auto'
                        else '人工'}指令)")
        else:
            head = (f"模型版本由 {parent} 演进至 {new}")
            if learned is not None:
                head += (f"(本轮学习 {learned} 条回流反馈"
                         "驱动 Hedge 更新)")
            if promoted:
                head += ", 评估更优已晋升"
            else:
                head += ", 以挑战者影子运行"

        parts = [head]

        # 因子权重变化
        for ch in facts.get("topWeightChanges") or []:
            d = float(ch.get("delta") or 0)
            if abs(d) < 1e-9:
                continue
            direction = "上调" if d > 0 else "下调"
            parts.append(
                f"因子 {ch.get('factor')} "
                f"权重{direction} {abs(d):.4f}")

        # 回流信号结构
        signals = facts.get("feedbackSignals") or {}
        if signals:
            sig_str = ", ".join(
                f"{k}×{v}" for k, v in
                sorted(signals.items()))
            parts.append(f"回流信号结构: {sig_str}")

        # 指标锚点
        metrics = facts.get("metrics") or {}
        satisfaction = metrics.get("satisfactionScore")
        if satisfaction is not None:
            parts.append(
                f"当前满意度指标 {satisfaction}")

        parts.append(
            "全部变化受 [0.5,2.0] 倍护栏约束并已"
            "归一化, 可通过模型事件历史逐版本溯源。")
        return "; ".join(parts)
