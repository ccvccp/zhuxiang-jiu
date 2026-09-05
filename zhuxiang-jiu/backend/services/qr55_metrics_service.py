"""55号·二维码AI智能管理 六指标管道
(qr55_metrics_service, P2)

计划(docs/55号_二维码AI智能管理模块实施计划.md §六 P2):
    意图满足率/渗透率/预算健康度/拦截有效率/满意度/
    澄清效率(52号 compute_snapshot 范式——纯数据层
    聚合, 数字不造谎)

口径(全部可从 qr55 三表推导——零外部依赖):
    ① 意图满足率 intentSatisfactionRate
       = 完成码 / 生成码(生成→扫码→完成全链闭环占比)
    ② 渗透率 penetrationRate
       = 扫码码 / 生成码(码被使用的比例)
    ③ 预算健康度 budgetHealthRate
       = spent / (spent + degraded) 成本服务正常扣减
       占比(L0 零成本不计——永不降级不涉健康度)
    ④ 拦截有效率 interceptEffectiveRate
       = (tamper + replay) / (tamper + replay + expire)
       异常扫码中硬拦截(篡改/重放)占比(expired 为
       软拒绝不计拦截)
    ⑤ 满意度 satisfactionScore
       = (mean_reward + 1) / 2 × 100 回流 reward 均值
       归一 0-100(真值代理——无问卷口径)
    ⑥ 澄清效率 clarifyEfficiency
       = clarify_hit / (clarify_hit + clarify_inefficient)
       澄清链有效占比

设计约定:
    - 纯读取式聚合(无落库副作用——快照可重复计算)
    - 除零防御: 分母 0 → None(无样本不造数)
"""

import logging

from core.helpers import ts

from repositories.qr55_repository import (
    Qr55Repository,
)

logger = logging.getLogger("qr55_metrics_service")

MODEL_VERSION = "v1-qr55-metrics"

# 六指标元数据(名称/口径/方向)
METRICS_META = {
    "intentSatisfactionRate": {
        "label": "意图满足率",
        "formula": "完成码 / 生成码",
        "direction": "higher_better",
    },
    "penetrationRate": {
        "label": "渗透率(扫码率)",
        "formula": "扫码码 / 生成码",
        "direction": "higher_better",
    },
    "budgetHealthRate": {
        "label": "预算健康度",
        "formula": "spent / (spent + degraded)",
        "direction": "higher_better",
    },
    "interceptEffectiveRate": {
        "label": "拦截有效率",
        "formula": "(篡改+重放) / (篡改+重放+过期)",
        "direction": "higher_better",
    },
    "satisfactionScore": {
        "label": "满意度(reward 归一)",
        "formula": "(均值 reward + 1) / 2 × 100",
        "direction": "higher_better",
    },
    "clarifyEfficiency": {
        "label": "澄清效率",
        "formula": "澄清命中 / (澄清命中 + 澄清低效)",
        "direction": "higher_better",
    },
}


class Qr55MetricsService:
    """55号六指标管道(码量/事件/回流三表聚合)"""

    def __init__(self):
        self.repo = Qr55Repository()

    # ============================================================
    # 指标快照(compute——纯读取)
    # ============================================================

    async def compute_snapshot(self) -> dict:
        """计算六指标快照(观测面——GET /stats 数据源;
        P5 看板消费同口径)"""
        codes = await self.repo.list_codes(limit=10000)
        events = await self.repo.list_events(limit=10000)
        feedback = await self.repo.list_feedback(
            limit=10000)

        generated = len(codes)
        # 扫码码(scanCount>0 或 redeemed)
        scanned = sum(
            1 for c in codes
            if int(c.get("scanCount") or 0) > 0
            or c.get("status") == "redeemed")
        # 完成码(complete 事件按码去重)
        completed_codes = {
            int(e.get("codeId") or 0)
            for e in events
            if e.get("eventType") == "complete"}
        completed = len(completed_codes)

        # 预算健康度(scan 事件 detail.budgetMode 分布)
        spent = degraded = 0
        for e in events:
            if e.get("eventType") != "scan":
                continue
            mode = (e.get("detail") or {}).get(
                "budgetMode")
            if mode == "spent":
                spent += 1
            elif mode == "degraded":
                degraded += 1

        # 拦截有效率(异常扫码分布)
        tamper = sum(1 for e in events
                     if e.get("eventType") == "tamper")
        replay = sum(1 for e in events
                     if e.get("eventType") == "replay")
        expire = sum(1 for e in events
                     if e.get("eventType") == "expire")

        # 满意度(回流 reward 均值归一)
        rewards = [
            float(f.get("reward") or 0)
            for f in feedback
            if f.get("status") == "labeled"]
        mean_reward = (
            sum(rewards) / len(rewards)
            if rewards else None)

        # 澄清效率(回流信号分布)
        clarify_hit = sum(
            1 for f in feedback
            if f.get("source") == "clarify_hit")
        clarify_inefficient = sum(
            1 for f in feedback
            if f.get("source")
            == "clarify_inefficient")

        metrics = {
            "intentSatisfactionRate": _ratio(
                completed, generated),
            "penetrationRate": _ratio(
                scanned, generated),
            "budgetHealthRate": _ratio(
                spent, spent + degraded),
            "interceptEffectiveRate": _ratio(
                tamper + replay,
                tamper + replay + expire),
            "satisfactionScore": _normalize_reward(
                mean_reward),
            "clarifyEfficiency": _ratio(
                clarify_hit,
                clarify_hit + clarify_inefficient),
        }

        return {
            "success": True,
            "modelVersion": MODEL_VERSION,
            "metrics": metrics,
            "metricsMeta": METRICS_META,
            "basis": {
                "generatedCodes": generated,
                "scannedCodes": scanned,
                "completedCodes": completed,
                "scanBudgetModes": {
                    "spent": spent,
                    "degraded": degraded,
                },
                "abnormalScans": {
                    "tamper": tamper,
                    "replay": replay,
                    "expire": expire,
                },
                "labeledFeedback": len(rewards),
                "clarifySignals": {
                    "hit": clarify_hit,
                    "inefficient":
                        clarify_inefficient,
                },
            },
            "note": "六指标管道——码量/事件/回流三表"
                    "纯读取聚合(52号 compute_snapshot 范式)",
            "computedAt": ts(),
        }

    # ============================================================
    # 指标留痕(调度器消费——model_events 快照)
    # ============================================================

    async def record_snapshot(self) -> dict:
        """计算+留痕指标快照(T+1 调度器每轮存档——
        漂移监控 P5 消费)"""
        snapshot = await self.compute_snapshot()
        try:
            from services.qr55_service import (
                Qr55Service,
            )
            await Qr55Service().record_model_event(
                "metrics_snapshot", {
                    "metrics": snapshot["metrics"],
                    "basis": snapshot["basis"],
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_metrics_event_failed: %s", exc)
        return snapshot


def _ratio(numerator: int | float,
           denominator: int | float):
    """占比(分母 0 → None——无样本不造数)"""
    if not denominator:
        return None
    return round(float(numerator)
                  / float(denominator), 4)


def _normalize_reward(mean_reward):
    """reward 均值 [-1,1] → 0-100(无样本 None)"""
    if mean_reward is None:
        return None
    return round((mean_reward + 1.0) / 2.0 * 100.0, 1)
