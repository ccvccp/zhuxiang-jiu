"""52号·小竹语音可用性评估引擎 服务层(us52_service)

P0 范围(计划 §七 P0):
    - 注册表视图(自描述)
    - 决策规则引擎(四态: veto/mandatory/priority/pass
      + regression 负向改进红线)
    - 指标快照框架(手工注入值→评估→留痕——
      P1-P4 计算管道逐期接入)

off 语义:
    US52_MODE=off → 计算面拒绝(测试停铁律——
    采集停 409 同款), 观测面(registry/快照查询)
    不受影响(与 51号语义区分对齐)。
"""

import logging

from core.helpers import ts

from repositories.us52_repository import Us52Repository
from services.us52_registry import (
    USABILITY_REGISTRY, DECISION_RULES, DIMENSIONS,
    DIMENSION_LABELS, current_mode, decide,
    evaluate_metric, registry_view,
)

logger = logging.getLogger("us52_service")


class Us52MetricsService:
    """52号评估服务(P0: 注册表+决策+快照框架)"""

    def __init__(self):
        self.repo = Us52Repository()

    # --------------------------------------------------------
    # 注册表视图
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        """指标注册表视图(治理面——不受开关影响)"""
        return registry_view()

    # --------------------------------------------------------
    # 指标快照(P0: 手工注入框架; P1-P4 计算接入)
    # --------------------------------------------------------

    async def compute_snapshot(
            self, metrics: dict = None) -> dict:
        """指标快照生成(输入 {metricKey: value} →
        逐项判定+决策+留痕)

        P0: metrics 由调用方注入(测试/手工评估);
        P1-P4: 各维计算管道逐期填充后调本方法。

    Raises:
        ValueError: off 态/未注册指标/空指标集
    """
        mode = current_mode()
        if mode != "on":
            raise ValueError(
                f"US52_MODE={mode}(默认 off——计算面"
                f"关闭; 开启请置 US52_MODE=on)")
        if not metrics or not isinstance(metrics, dict):
            raise ValueError(
                "metrics 需为非空 {metricKey: value}")

        # 未注册指标拒绝(注册表封闭)
        unknown = [k for k in metrics
                   if k not in USABILITY_REGISTRY]
        if unknown:
            raise ValueError(
                f"未注册指标: {unknown[:5]}"
                f"(注册表封闭——20 项)")

        # 逐项判定
        evaluated: dict = {}
        passed_count = 0
        for key, value in metrics.items():
            status = evaluate_metric(key, value)
            meta = USABILITY_REGISTRY[key]
            if status == "pass":
                passed_count += 1
            evaluated[key] = {
                "value": round(float(value), 4),
                "baseline": float(meta["baseline"]),
                "direction": meta["direction"],
                "dimension": meta["dimension"],
                "status": status,
                "veto": meta["veto"],
                "proxy": meta["proxy"],
            }

        decision = decide(metrics)

        snap_id = await self.repo.next_snap_id()
        record = {
            "snapId": snap_id,
            "mode": mode,
            "sampleCount": len(metrics),
            "passedCount": passed_count,
            "metrics": evaluated,
            "decision": decision["decision"],
            "rationale": decision["rationale"],
            "vetoFailed": decision["vetoFailed"],
            "failedByDimension":
                decision["failedByDimension"],
            "createdAt": ts(),
        }
        await self.repo.save_snapshot(record)
        logger.info("us52_snapshot id=%s decision=%s "
                    "passed=%s/%s", snap_id,
                    decision["decision"], passed_count,
                    len(metrics))
        return {"success": True,
                "snapshot": record}

    async def latest_snapshot(self) -> dict:
        """最近一次快照(无则空态)"""
        records = await self.repo.list_snapshots(
            limit=1)
        if records:
            return {"success": True,
                    "snapshot": records[0]}
        return {"success": True, "snapshot": None,
                "note": "尚无快照(P1-P4 计算管道"
                        "逐期接入; P0 可手工注入)"}

    async def list_snapshots(self) -> dict:
        """快照历史(最新在前——回溯可比)"""
        records = await self.repo.list_snapshots(
            limit=50)
        return {"success": True,
                "total": len(records),
                "snapshots": records}

    # --------------------------------------------------------
    # 上线门禁(release-gate 决策入口)
    # --------------------------------------------------------

    @staticmethod
    def release_gate(metrics: dict,
                      sacrifice_flags:
                      list = None) -> dict:
        """上线门禁(决策规则引擎直译)

        一票否决: 安全韧性任一未达 → 禁止上线
        负向红线: 牺牲 privacy/explainability/
        fairness → regression
        """
        gate = decide(metrics, sacrifice_flags)
        return {
            "success": True,
            "gate": gate["decision"],
            "passed": gate["passed"],
            "rationale": gate["rationale"],
            "vetoFailed": gate["vetoFailed"],
            "failedByDimension":
                gate["failedByDimension"],
            "rules": DECISION_RULES,
            "note": "veto/regression → 禁止上线; "
                    "mandatory → 限期修复+回归",
        }

    # --------------------------------------------------------
    # 维度聚合视图
    # --------------------------------------------------------

    @staticmethod
    def dimensions_view() -> dict:
        """五维结构(供看板分区)"""
        return {
            "dimensions": [
                {"key": d,
                 "label": DIMENSION_LABELS[d],
                 "metricCount": sum(
                     1 for m in
                     USABILITY_REGISTRY.values()
                     if m["dimension"] == d)}
                for d in DIMENSIONS],
            "totalMetrics": len(USABILITY_REGISTRY),
        }
