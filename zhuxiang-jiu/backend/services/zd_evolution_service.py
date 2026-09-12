"""智单·AI智能订单大模型 P3 进化闭环(zd_evolution_service)

「反馈→学习→观测→备忘」全链路进化:
    - 反馈闭环: adopted/corrected/rejected 三态裁决留痕; 预测类
      裁决驱动 etaRecentWeight 参数学习(adopted ×1.05 /
      corrected ×0.95 / rejected ×0.9, clamp [0.4, 0.8] 安全阀,
      权重永不出界), 参数存 zhuxiang:zd:params 单条 JSON
    - 三检测器: 单量 spike / PAID 停滞 drop / 取消 surge
      (复用 μ±3σ/×3 范式, 作用于织物日时序, 确定性)
    - 决策备忘录: 大促备货 / 超时策略两主题(模板 + 数据插值 +
      假设标注, 建议书)

铁律: 参数进化确定性可复现; 检测只观测不处置; 备忘录为
建议书; LLM 禁入。
"""

import logging
import math

from services.zd_fabric_service import (
    _ZdStore, ZdFabricService, _round2, _now_iso)

logger = logging.getLogger(__name__)

# 反馈闭环(三态裁决)
FEEDBACK_TARGETS = ("eta_forecast", "volume_forecast", "whatif",
                    "refund_score", "anomaly_scan", "checkup", "portrait")
FEEDBACK_VERDICTS = ("adopted", "corrected", "rejected")
VERDICT_NAME = {"adopted": "采纳", "corrected": "修正", "rejected": "拒绝"}
VERDICT_MULTIPLIER = {"adopted": 1.05, "corrected": 0.95, "rejected": 0.90}

# ETA 近期权重学习(安全阀)
ETA_WEIGHT_DEFAULT = 0.6
ETA_WEIGHT_CLAMP = (0.4, 0.8)

# 三检测器阈值(对齐 blogger_trust / API 先例)
SPIKE_SIGMA = 3.0        # spike: > μ+3σ 且 μ≥5
SPIKE_MU_MIN = 5.0
DROP_SIGMA = 3.0         # drop: < μ−3σ 且绝对降幅 ≥20
DROP_ABS_FLOOR = 20.0
SURGE_RATIO = 3.0        # surge: ≥ 前日×3 且当期样本 ≥20
SURGE_MIN_SAMPLE = 20

# 备忘录主题
MEMO_TOPICS = ("promotion_prep", "timeout_policy")
MEMO_TOPIC_NAMES = {"promotion_prep": "大促备货",
                    "timeout_policy": "超时策略"}


class ZdEvolutionService:
    """P3: 反馈进化 + 三检测器 + 决策备忘录"""

    def __init__(self, fabric: ZdFabricService = None,
                 store: _ZdStore = None):
        self.store = store or _ZdStore()
        self.fabric = fabric or ZdFabricService(self.store)

    # ============================================================
    # 反馈闭环(参数权重确定性学习, 安全阀内)
    # ============================================================

    async def feedback(self, target_type: str, verdict: str,
                       note: str = "") -> dict:
        """反馈闭环: 裁决留痕 + eta_forecast 类驱动权重学习

        进化口径(确定性):
            adopted ×1.05 / corrected ×0.95 / rejected ×0.9,
            clamp [0.4, 0.8](安全阀, 权重永不出界)
        """
        if target_type not in FEEDBACK_TARGETS:
            raise ValueError(
                f"反馈目标无效({'/'.join(FEEDBACK_TARGETS)})")
        if verdict not in FEEDBACK_VERDICTS:
            raise ValueError(
                f"裁决须为 {'/'.join(FEEDBACK_VERDICTS)}(当前 {verdict})")
        record = {
            "feedbackId": await self.store.next_id("feedbacks"),
            "targetType": target_type,
            "verdict": verdict,
            "verdictName": VERDICT_NAME[verdict],
            "note": note or "",
            "feedbackAt": _now_iso(),
        }
        # 仅预测类(eta_forecast)裁决驱动参数进化
        if target_type == "eta_forecast":
            params = await self.get_params()
            raw = params["etaRecentWeight"] * VERDICT_MULTIPLIER[verdict]
            new_weight = _round2(
                max(ETA_WEIGHT_CLAMP[0],
                    min(ETA_WEIGHT_CLAMP[1], raw)))
            await self.store.save_params({
                **params, "etaRecentWeight": new_weight,
                "updatedAt": _now_iso()})
            record["etaRecentWeightAfter"] = new_weight
        await self.store.save("feedbacks",
                              record["feedbackId"], record)
        return record

    async def get_params(self) -> dict:
        """当前进化参数(默认 etaRecentWeight=0.6)"""
        params = await self.store.get_params()
        return params or {"etaRecentWeight": ETA_WEIGHT_DEFAULT,
                          "updatedAt": ""}

    async def params(self) -> dict:
        """进化参数视图(含安全阀与乘子说明)"""
        params = await self.get_params()
        return {
            **params,
            "default": ETA_WEIGHT_DEFAULT,
            "clamp": list(ETA_WEIGHT_CLAMP),
            "multipliers": dict(VERDICT_MULTIPLIER),
            "note": ("反馈闭环驱动 etaRecentWeight 学习; "
                     "安全阀 clamp [0.4, 0.8], 权重永不出界"),
        }

    async def feedbacks(self, limit: int = 50) -> list[dict]:
        """反馈留痕列表(feedbackAt 降序)"""
        rows = await self.store.list("feedbacks")
        rows = sorted(rows, key=lambda r: (
            str(r.get("feedbackAt", "")),
            int(r.get("feedbackId", 0))), reverse=True)
        return rows[:max(1, int(limit))]

    # ============================================================
    # 三检测器(织物日时序, μ±3σ/×3 范式)
    # ============================================================

    async def detect(self) -> dict:
        """三检测器: 单量 spike / PAID 停滞 drop / 取消 surge

        作用于织物日时序(createdAt 日聚合, 末位日为当期):
            - spike: 当日单量 > μ+3σ 且 μ≥5(防冷启动误报)
            - drop: 当日 PAID 停滞量 < μ−3σ 且绝对降幅 ≥20
            - surge: 当日取消量 ≥ 前日×3 且当期样本 ≥20
            - 零方差防误报: 恒定历史下 翻倍即 spike / 腰斩即 drop
        检测只观测不处置(处置走建议书)。
        """
        orders = await self.fabric.orders(limit=2000)
        daily: dict = {}
        for order in orders:
            date = str(order.get("createdAt", ""))[:10]
            if not date:
                continue
            row = daily.setdefault(
                date, {"orders": 0, "paid": 0, "cancelled": 0})
            row["orders"] += 1
            status = str(order.get("status", ""))
            if status == "PAID":
                row["paid"] += 1
            if status in ("CANCELLED", "CLOSED"):
                row["cancelled"] += 1
        dates = sorted(daily)
        alerts: list[dict] = []
        if len(dates) >= 2:
            history = dates[:-1]
            cur_date = dates[-1]
            cur = daily[cur_date]

            def mu_sigma(key: str) -> tuple[float, float]:
                values = [float(daily[d][key]) for d in history]
                mu = sum(values) / len(values)
                sigma = math.sqrt(
                    sum((v - mu) ** 2 for v in values) / len(values))
                return mu, sigma

            # 1. 单量 spike
            mu, sigma = mu_sigma("orders")
            cur_volume = cur["orders"]
            if mu >= SPIKE_MU_MIN:
                hit = ((sigma > 0 and cur_volume > mu
                        + SPIKE_SIGMA * sigma)
                       or (sigma == 0 and cur_volume >= mu * 2))
                if hit:
                    alerts.append({
                        "type": "spike", "metric": "日单量",
                        "date": cur_date, "value": cur_volume,
                        "baseline": _round2(mu),
                        "detail": (f"日单量 {cur_volume} 超阈值"
                                   f"(μ={_round2(mu)}, "
                                   f"σ={_round2(sigma)}, μ+3σ)"),
                    })
            # 2. PAID 停滞 drop(如 PAID 停滞占比骤降)
            mu, sigma = mu_sigma("paid")
            cur_paid = cur["paid"]
            hit = ((sigma > 0 and cur_paid < mu - DROP_SIGMA * sigma
                    and (mu - cur_paid) >= DROP_ABS_FLOOR)
                   or (sigma == 0 and cur_paid <= mu / 2
                       and (mu - cur_paid) >= DROP_ABS_FLOOR))
            if hit:
                alerts.append({
                    "type": "drop", "metric": "PAID 停滞量",
                    "date": cur_date, "value": cur_paid,
                    "baseline": _round2(mu),
                    "detail": (f"PAID 停滞量 {cur_paid} 低于 μ−3σ"
                               f"(μ={_round2(mu)}, "
                               f"降幅 {_round2(mu - cur_paid)} ≥ "
                               f"{DROP_ABS_FLOOR})"),
                })
            # 3. 取消 surge
            prev_cancel = daily[history[-1]]["cancelled"]
            cur_cancel = cur["cancelled"]
            if (cur_cancel >= SURGE_MIN_SAMPLE
                    and cur_cancel >= prev_cancel * SURGE_RATIO):
                alerts.append({
                    "type": "surge", "metric": "日取消量",
                    "date": cur_date, "value": cur_cancel,
                    "baseline": prev_cancel,
                    "detail": (f"日取消量 {cur_cancel} 达前日 "
                               f"{prev_cancel} 的 3 倍以上"
                               f"且样本 ≥{SURGE_MIN_SAMPLE}"),
                })
        return {
            "alertCount": len(alerts),
            "alerts": alerts,
            "days": len(dates),
            "detectors": [
                "spike: >μ+3σ 且 μ≥5(单量)",
                "drop: <μ−3σ 且降幅 ≥20(PAID 停滞量)",
                "surge: ×3 且样本 ≥20(取消量)",
            ],
            "formula": ("三检测器作用于织物日时序"
                        "(μ±3σ/×3 确定性阈值, LLM 禁入)"),
            "disposition": "检测为观测告警; 处置走建议书, 不自动干预订单",
        }

    # ============================================================
    # 决策备忘录(模板 + 数据插值 + 假设标注, 建议书)
    # ============================================================

    async def memo(self, topic: str, notes: str = "") -> dict:
        """决策备忘录: 大促备货 / 超时策略(数据插值 + 假设标注)

        Raises:
            ValueError: 主题无效
        """
        if topic not in MEMO_TOPICS:
            raise ValueError(
                f"备忘录主题无效({'/'.join(MEMO_TOPICS)})")
        ov = await self.fabric.overview()
        daily = ov.get("dailySeries") or []
        volumes = [int(r.get("orders", 0) or 0) for r in daily]
        daily_avg = (_round2(sum(volumes) / len(volumes))
                     if volumes else 0.0)
        if topic == "promotion_prep":
            items_total = sum(p["quantity"]
                              for p in ov["productAggregates"])
            items_per_order = (_round2(items_total / ov["paidOrders"])
                               if ov["paidOrders"] else 0.0)
            est_orders = _round2(daily_avg * 7 * 3)
            suggest_stock = int(round(
                est_orders * items_per_order * 1.2))
            assumptions = [
                "假设: 大促周期 7 天(可按实际档期调整)",
                "假设: 大促流量为日常 3 倍(历史无大促样本, 无实测校准)",
                "假设: 备货安全余量 1.2(防断货, 不含在途)",
            ]
            recommendation = (f"近 {len(volumes)} 日日均 {daily_avg} 单, "
                              f"均件数 {items_per_order}; "
                              f"大促预估 {est_orders} 单, "
                              f"建议备货 {suggest_stock} 件"
                              "(建议书, 人工核定)")
            data_snapshot = {
                "dailyAvg": daily_avg,
                "itemsPerOrder": items_per_order,
                "estPromoOrders": est_orders,
                "suggestedStock": suggest_stock,
            }
        else:
            pending = ov["statusDistribution"].get("PENDING", 0)
            avg_hours = ov["avgFulfillmentHours"]
            warn_hours = _round2(avg_hours * 1.5)
            assumptions = [
                "假设: 支付超时维持 30 分钟口径(既有 TIMEOUT_PAY, 不动)",
                "假设: 履约预警线 = 平均履约 × 1.5(线性口径)",
            ]
            recommendation = (f"当前待付款 {pending} 单, "
                              f"平均履约 {avg_hours} 小时"
                              f"(样本 {ov['fulfillmentSamples']}); "
                              f"建议履约预警线 {warn_hours} 小时, "
                              "超线订单优先加急(建议书, 人工核定)")
            data_snapshot = {
                "pendingOrders": pending,
                "avgFulfillmentHours": avg_hours,
                "warnHours": warn_hours,
            }
        record = {
            "memoId": await self.store.next_id("memos"),
            "topic": topic,
            "topicName": MEMO_TOPIC_NAMES[topic],
            "notes": notes or "",
            "dataSnapshot": data_snapshot,
            "assumptions": assumptions,
            "recommendation": recommendation,
            "disposition": "决策备忘录为建议书; 决定权在管理员"
                           "(不自动执行)",
            "createdAt": _now_iso(),
        }
        await self.store.save("memos", record["memoId"], record)
        return record

    async def memos(self, limit: int = 50) -> list[dict]:
        """备忘录列表(createdAt 降序)"""
        rows = await self.store.list("memos")
        return sorted(rows, key=lambda r: str(r.get("createdAt", "")),
                      reverse=True)[:max(1, int(limit))]
