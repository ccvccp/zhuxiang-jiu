"""智运·AI智能物流大模型 P0 智能路由中枢(zw_route_service)

创新规划 §二 P0: 从"规则表静态匹配"到"多维路由决策"
    - 多维路由决策: 复用既有 smart_route_carrier 规则基座(零改动),
      叠加物流商质量评分加权 → 综合分决策留痕
    - 物流商质量评分(确定性): 历史签收率 40% + 时效达标 30% +
      费率竞争 30%(基于数据织物聚合)
    - 容灾健康监测: 物流商近期失败率/停滞率 → 健康度 →
      切换《建议书》(永不自动切换)

铁律: 全链确定性; 切换/降级为建议书模式; 既有路由函数零改动。
"""

import logging

from services.logistics_service import smart_route_carrier
from services.zw_fabric_service import (
    _ZwStore, ZwFabricService, _round2, _now_iso)

logger = logging.getLogger(__name__)

# 质量评分权重(确定性)
W_SIGN_RATE = 0.40
W_TIMELINESS = 0.30
W_FEE = 0.30

# 时效基准线(小时, 时效达标分母——快于该线按比例给分)
TIMELINESS_BASELINE_HOURS = 72.0

# 费率竞争基准(元/单, 低费率给高分)
FEE_BASELINE = 30.0

# 健康度阈值: 近期失败率超过该值 → 建议降级
HEALTH_FAIL_RATE_LINE = 0.2
HEALTH_MIN_SAMPLE = 5


class ZwRouteService:
    """P0: 多维路由决策 + 物流商质量评分 + 容灾健康监测"""

    def __init__(self, store: _ZwStore = None,
                 fabric: ZwFabricService = None):
        self.store = store or _ZwStore()
        self.fabric = fabric or ZwFabricService(self.store)

    # ============================================================
    # 物流商质量评分(确定性三因子)
    # ============================================================

    async def carrier_scores(self) -> dict:
        """五物流商质量评分(基于历史订单聚合)

        评分 = 40%×签收率 + 30%×时效分 + 30%×费率分
        时效分 = min(1, 基准线72h/实际均时效)
        费率分 = min(1, 基准费30元/实际均费)
        无样本物流商给默认 70 分(冷启动保守)。
        """
        from repositories.logistics_repository import CARRIER_NAMES
        performance = await self.fabric.carrier_performance()
        scores = {}
        for carrier, name in CARRIER_NAMES.items():
            p = performance.get(carrier)
            if not p or p["total"] == 0:
                scores[carrier] = {
                    "carrier": carrier, "carrierName": name,
                    "score": 70.0, "sample": 0,
                    "signRate": 0.0, "avgSignHours": 0.0, "avgFee": 0.0,
                    "coldStart": True,
                    "explain": "无历史样本, 冷启动默认 70 分",
                }
                continue
            timeliness_score = (min(1.0, TIMELINESS_BASELINE_HOURS
                                    / p["avgSignHours"])
                               if p["avgSignHours"] > 0 else 0.5)
            fee_score = (min(1.0, FEE_BASELINE / p["avgFee"])
                         if p["avgFee"] > 0 else 0.5)
            score = _round2(100 * (W_SIGN_RATE * p["signRate"]
                                   + W_TIMELINESS * timeliness_score
                                   + W_FEE * fee_score))
            scores[carrier] = {
                "carrier": carrier, "carrierName": name,
                "score": score, "sample": p["total"],
                "signRate": p["signRate"],
                "avgSignHours": p["avgSignHours"],
                "avgFee": p["avgFee"], "coldStart": False,
                "explain": (f"签收率 {p['signRate']:.0%}×{W_SIGN_RATE} + "
                            f"时效 {p['avgSignHours']}h×{W_TIMELINESS} + "
                            f"均费 ¥{p['avgFee']}×{W_FEE}"),
            }
        return scores

    # ============================================================
    # 多维路由决策(规则基座 + 质量评分加权)
    # ============================================================

    async def route_decide(self, order_type: str, weight: float,
                           piece_count: int, insured_value: float,
                           sender: dict, receiver: dict) -> dict:
        """多维路由决策: 既有规则基座候选 × 质量评分 → 综合分

        综合 = 规则分 60% + 质量评分 40%
        (规则基座零改动复用; 质量分来自历史表现闭环)
        决策留痕(含候选对比与理由)。
        """
        if order_type not in ("retail", "groupbuy", "return"):
            raise ValueError(f"订单类型无效({order_type})")
        if weight < 0 or piece_count <= 0 or insured_value < 0:
            raise ValueError("重量/件数/货值不可为负(件数须为正)")

        base = smart_route_carrier(
            order_type=order_type, weight=weight,
            piece_count=piece_count, insured_value=insured_value,
            sender=sender or {}, receiver=receiver or {})
        scores = await self.carrier_scores()

        ranked = []
        for cand in base.get("candidates", []):
            carrier = cand["carrier"]
            rule_score = float(cand["score"])
            quality = scores.get(carrier, {}).get("score", 70.0)
            combined = _round2(rule_score * 0.6 + quality * 0.4)
            ranked.append({
                "carrier": carrier,
                "carrierName": cand.get("carrierName", carrier),
                "serviceType": cand.get("serviceType", ""),
                "ruleReason": cand.get("reason", ""),
                "ruleScore": rule_score,
                "qualityScore": quality,
                "combinedScore": combined,
            })
        ranked.sort(key=lambda r: r["combinedScore"], reverse=True)

        if not ranked:
            raise ValueError("路由决策失败: 无候选物流商(检查订单参数)")
        best = ranked[0]
        decision_id = await self.store.next_id("route_decision")
        record = {
            "decisionId": decision_id,
            "input": {
                "orderType": order_type, "weight": weight,
                "pieceCount": piece_count,
                "insuredValue": insured_value,
            },
            "decision": best,
            "candidates": ranked,
            "formula": "综合 = 规则分×0.6 + 质量分×0.4",
            "note": "决策为推荐, 实际物流商由下单方确认",
            "decidedAt": _now_iso(),
        }
        await self.store.save("route_decisions", decision_id, record)
        return record

    async def route_decisions(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("route_decisions")
        return sorted(rows, key=lambda r: r.get("decidedAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 容灾健康监测(切换建议书)
    # ============================================================

    async def carrier_health(self) -> dict:
        """物流商健康度监测 + 降级建议书(永不自动切换)

        健康度 = 签收率(近期样本≥5 才评); 失败率>20% → 生成
        《容灾切换建议书》(人工确认)。
        """
        performance = await self.fabric.carrier_performance()
        from repositories.logistics_repository import CARRIER_NAMES
        reports = []
        for carrier, name in CARRIER_NAMES.items():
            p = performance.get(carrier)
            if not p or p["total"] == 0:
                reports.append({
                    "carrier": carrier, "carrierName": name,
                    "sample": 0, "signRate": None,
                    "failRate": None, "health": "unknown",
                    "explain": "无近期样本",
                })
                continue
            fail_rate = _round2(1.0 - p["signRate"])
            if p["total"] < HEALTH_MIN_SAMPLE:
                health = "insufficient"
                explain = f"样本不足({p['total']}<{HEALTH_MIN_SAMPLE})"
            elif fail_rate > HEALTH_FAIL_RATE_LINE:
                health = "degraded"
                explain = (f"近期失败率 {fail_rate:.0%} 超阈值 "
                           f"{HEALTH_FAIL_RATE_LINE:.0%}")
            else:
                health = "healthy"
                explain = (f"签收率 {p['signRate']:.0%}, "
                           f"失败率 {fail_rate:.0%} 在阈值内")
            report = {
                "carrier": carrier, "carrierName": name,
                "sample": p["total"], "signRate": p["signRate"],
                "failRate": fail_rate, "health": health,
                "explain": explain,
            }
            if health == "degraded":
                report["switchSuggestion"] = {
                    "title": "容灾切换建议书",
                    "body": (f"{name} 近期失败率 {fail_rate:.0%} 超过 "
                             f"{HEALTH_FAIL_RATE_LINE:.0%} 阈值, 建议"
                             "评估流量切换至健康物流商"),
                    "disposition": "建议书模式: 切换须人工确认, "
                                   "永不自动执行",
                }
            reports.append(report)
        return {
            "carriers": reports,
            "thresholds": {
                "failRateLine": HEALTH_FAIL_RATE_LINE,
                "minSample": HEALTH_MIN_SAMPLE,
            },
            "note": "容灾监测为观测面; 切换建议书永不自动执行",
            "monitoredAt": _now_iso(),
        }
