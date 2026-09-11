"""智图·P3 进化引擎(zt_evolution_service)

创新规划 §三 P3: 全链路进化闭环
    - 行为流萃取: 搜索→下单→核销→评价全链留痕(学习点标注)
    - 时空战略沙盘: 选址模拟——确定性因子加权
      (人口密度代理×商圈竞争距离×POI 密度×成本), 输出收益/风险量化
    - 时空绩效画像: 按区域/时段的 POI 效能评估
    - 反馈闭环: 采纳/拒绝负样本回流

铁律: 全部确定性统计; 沙盘为建议书; LLM 禁入。
"""

import logging

from repositories.location_repository import haversine_km
from services.zt_fabric_service import (
    _ZtStore, ZtFabricService, POI_TYPE_NAMES, _round2, _now_iso)

logger = logging.getLogger(__name__)

# 沙盘因子权重(确定性)
W_POPULATION = 0.30      # 人口密度代理(周边 POI 数量×评分均值)
W_COMPETITION = 0.30     # 竞争距离分(最近同类 POI 距离越远越好)
W_CLUSTER = 0.25         # 商圈密度分(周边 POI 总量)
W_COST = 0.15            # 成本分(距最近仓越近配送成本越低)

BEHAVIOR_STAGES = ("search", "order", "verify", "review")
BEHAVIOR_STAGE_NAMES = {
    "search": "搜索", "order": "下单", "verify": "核销", "review": "评价"}


class ZtEvolutionService:
    """P3: 行为萃取 + 选址沙盘 + 时空绩效 + 反馈闭环"""

    def __init__(self, store: _ZtStore = None,
                 fabric: ZtFabricService = None):
        self.store = store or _ZtStore()
        self.fabric = fabric or ZtFabricService(self.store)

    # ============================================================
    # 行为流萃取(全链留痕)
    # ============================================================

    async def save_behavior(self, member_id: int, stage: str,
                            role: str = "consumer", query: str = "",
                            poi_code: str = "",
                            result_score: float = None) -> dict:
        """行为留痕: 搜索→下单→核销→评价(学习点标注)"""
        if stage not in BEHAVIOR_STAGES:
            raise ValueError(f"行为阶段无效({stage}: "
                            f"{'/'.join(BEHAVIOR_STAGES)})")
        if not member_id or member_id < 1:
            raise ValueError("会员 ID 无效")
        record = {
            "behaviorId": await self.store.next_id("zt_behaviors"),
            "memberId": member_id, "stage": stage,
            "stageName": BEHAVIOR_STAGE_NAMES[stage],
            "role": role, "query": query or "",
            "poiCode": poi_code or "",
            "resultScore": _round2(result_score)
            if result_score is not None else None,
            "learningPoint": f"{BEHAVIOR_STAGE_NAMES[stage]}行为留痕"
                             "(排序/推送时机校准观测)",
            "occurredAt": _now_iso(),
        }
        await self.store.save("zt_behaviors",
                              record["behaviorId"], record)
        return record

    async def behaviors(self, member_id: int = None,
                        stage: str = None, limit: int = 50) -> list[dict]:
        rows = await self.store.list("zt_behaviors")
        if member_id:
            rows = [r for r in rows if r.get("memberId") == member_id]
        if stage:
            rows = [r for r in rows if r.get("stage") == stage]
        return sorted(rows, key=lambda r: r.get("occurredAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 时空战略沙盘(选址模拟, 确定性因子加权)
    # ============================================================

    async def sandbox(self, name: str, longitude: float,
                      latitude: float, poi_type: str = "flagship",
                      monthly_cost: float = 50000.0) -> dict:
        """选址沙盘: 四因子确定性加权 → 收益/风险量化(建议书)

        人口代理 = 周边3km POI 评分均值×100
        竞争距离分 = min(1, 最近同类/2km)
        商圈密度分 = min(1, 周边3km POI 数/8)
        成本分 = min(1, 距最近仓/20km)
        """
        if not name or len(name) > 40:
            raise ValueError("方案名称无效")
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("坐标非法")
        if poi_type not in POI_TYPE_NAMES:
            raise ValueError(f"POI 类型无效({poi_type})")
        if not (1000 <= monthly_cost <= 10_000_000):
            raise ValueError("月成本须在 [1000, 10000000]")

        pois = await self.fabric.list_pois()
        nearby_all = [p for p in pois
                     if haversine_km(longitude, latitude,
                                     float(p["longitude"]),
                                     float(p["latitude"])) <= 3.0]
        same_type = [p for p in nearby_all
                     if p["poiType"] == poi_type]

        population = (_round2(sum(float(p.get("rating", 0))
                                  for p in nearby_all) / len(nearby_all)
                              * 100) if nearby_all else 0.0)
        if same_type:
            nearest = min(haversine_km(longitude, latitude,
                                       float(p["longitude"]),
                                       float(p["latitude"]))
                          for p in same_type)
            competition = min(1.0, nearest / 2.0)
            nearest_km = _round2(nearest)
        else:
            competition = 1.0
            nearest_km = None
        cluster = min(1.0, len(nearby_all) / 8.0)
        warehouses = [p for p in pois
                      if p["poiType"] == "warehouse"]
        if warehouses:
            near_wh = min(haversine_km(longitude, latitude,
                                       float(w["longitude"]),
                                       float(w["latitude"]))
                          for w in warehouses)
            cost_score = min(1.0, near_wh / 20.0)
            near_wh_km = _round2(near_wh)
        else:
            cost_score = 0.5
            near_wh_km = None

        score = _round2(100 * (W_POPULATION * (population / 100)
                               + W_COMPETITION * competition
                               + W_CLUSTER * cluster
                               + W_COST * cost_score))
        # 收益测算(确定性线性口径): 月毛利代理 = 评分分×客流系数×系数
        est_monthly_revenue = _round2(score * 6000)
        est_monthly_profit = _round2(est_monthly_revenue - monthly_cost)
        risk = "low" if score >= 65 else "medium" if score >= 45 else "high"

        record = {
            "sandboxId": await self.store.next_id("zt_sandboxes"),
            "name": name,
            "candidate": {"longitude": longitude,
                          "latitude": latitude, "poiType": poi_type},
            "factors": {
                "populationProxy": population,
                "competition": _round2(competition),
                "nearestSameTypeKm": nearest_km,
                "cluster": _round2(cluster),
                "nearbyPoiCount": len(nearby_all),
                "costScore": _round2(cost_score),
                "nearestWarehouseKm": near_wh_km,
            },
            "score": score,
            "prediction": {
                "estMonthlyRevenue": est_monthly_revenue,
                "monthlyCost": monthly_cost,
                "estMonthlyProfit": est_monthly_profit,
                "risk": risk,
            },
            "formula": (f"沙盘分 = 人口×{W_POPULATION} + 竞争×"
                        f"{W_COMPETITION} + 密度×{W_CLUSTER} + "
                        f"成本×{W_COST}(确定性加权)"),
            "disposition": "选址为建议书; 决策须管理层人工确认",
            "simulatedAt": _now_iso(),
        }
        await self.store.save("zt_sandboxes",
                              record["sandboxId"], record)
        return record

    # ============================================================
    # 时空绩效画像(区域/时段效能)
    # ============================================================

    async def performance(self) -> dict:
        """按 POI 类型/营业状态聚合效能(坪效代理)"""
        pois = await self.fabric.list_pois()
        by_type: dict[str, dict] = {}
        for p in pois:
            t = by_type.setdefault(p["poiType"], {
                "total": 0, "open": 0, "ratingSum": 0.0,
                "avgLoad": 0.0})
            t["total"] += 1
            if p.get("open"):
                t["open"] += 1
            t["ratingSum"] += float(p.get("rating", 0) or 0)
            seats = p.get("seats")
            load = (seats["available"] / seats["total"]
                    if isinstance(seats, dict) and seats.get("total")
                    else float(p.get("stockLevel", 0) or 0))
            t["avgLoad"] += load
        rows = []
        for t, s in sorted(by_type.items()):
            avg_rating = _round2(s["ratingSum"] / s["total"])
            avg_load = _round2(s["avgLoad"] / s["total"])
            # 效能代理 = 评分/5 × 开业率 × (1-余量=利用率)
            efficiency = _round2(100 * (avg_rating / 5)
                                 * (s["open"] / s["total"])
                                 * (1 - avg_load))
            rows.append({
                "poiType": t, "poiTypeName": POI_TYPE_NAMES.get(t, t),
                "total": s["total"], "openRate": _round2(
                    s["open"] / s["total"]),
                "avgRating": avg_rating, "avgAvailability": avg_load,
                "efficiencyProxy": efficiency,
            })
        rows.sort(key=lambda r: -r["efficiencyProxy"])
        return {
            "byType": rows,
            "formula": "效能代理 = 评分/5 × 开业率 × 利用率(确定性)",
            "note": "绩效为观测面; 激励与资源倾斜须管理层决策",
            "analyzedAt": _now_iso(),
        }

    # ============================================================
    # 反馈闭环(负样本回流)
    # ============================================================

    async def feedback(self, target_type: str, verdict: str,
                       note: str = "") -> dict:
        """反馈闭环(adopted 采纳 / rejected 拒绝=负样本)"""
        if target_type not in ("intent_search", "sandbox",
                               "ticket_dispatch", "performance"):
            raise ValueError("反馈对象无效(intent_search/sandbox/"
                             "ticket_dispatch/performance)")
        if verdict not in ("adopted", "rejected"):
            raise ValueError("裁决无效(adopted/rejected)")
        record = {
            "feedbackId": await self.store.next_id("zt_feedbacks"),
            "targetType": target_type, "verdict": verdict,
            "note": note or "",
            "negativeSample": ("负样本回流: 留痕供阈值/排序校准观测"
                               "(决策权在人工)")
            if verdict == "rejected" else None,
            "feedbackAt": _now_iso(),
        }
        await self.store.save("zt_feedbacks",
                              record["feedbackId"], record)
        return record

    async def feedbacks(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("zt_feedbacks")
        return sorted(rows, key=lambda r: r.get("feedbackAt", ""),
                      reverse=True)[:limit]
