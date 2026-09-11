"""智酿运通 P7 自主学习进化引擎 2.0(zw_evolution2_service)

创新规划 §三 P7: 让模型"越用越懂酒"
    - 个性化偏好学习: B 端特殊要求(工作日收货/指定联系人)/C 端习惯
      (放驿站/周末配送)确定性登记, 应用到订单画像(输出携带偏好提示)
    - 异常模式识别: 历史异常按(渠道×类型)确定性聚类 → 高频模式 →
      预防性规则建议(如"顺丰×破损 3 例→建议缓冲包装升级")
    - 碳足迹核算(排放因子法, 确定性): 单均碳排 = 重量(吨)×距离代理
      (同城50/省内300/跨省1000/偏远2500 km)×渠道因子; 绿色切换建议
      (对接 68 号碳积分语义: 不可交易)
    - 人机协同: 策略建议(熔断+碳排+模式聚合)→ decide 确认采纳/拒绝;
      拒绝记负样本回流观测面(永不自动生效)

铁律: 全部确定性统计; 建议永不自动执行; LLM 禁入决策链。
"""

import logging

from services.zw_fabric_service import (
    _ZwStore, ZwFabricService, _round2, _now_iso)
from services.zw_track_service import ZwTrackService
from services.zw_circuit_service import ZwCircuitService
from repositories.logistics_repository import CARRIER_NAMES

logger = logging.getLogger(__name__)

# 偏好键域(确定性枚举, 防脏数据)
B2B_PREF_KEYS = {"weekdayOnly", "contactPerson", "appointedSlot"}
B2C_PREF_KEYS = {"stationDrop", "weekendDelivery", "quietDelivery"}

# 碳排放因子(kg CO2/吨·公里, 确定性常量; 公路零担<整车<航空快件的
# 相对量级, 覆盖既有五渠道)
CARRIER_CARBON_FACTORS = {
    "SF": 0.90,   # 快件(含航空件占比, 因子最高)
    "JD": 0.85,   # 快件(仓配一体, 略低于散航空)
    "LLL": 0.70,  # 同城整车(柴油重载)
    "DB": 0.50,   # 公路零担(规模化摊薄)
    "YT": 0.40,   # 经济快递(陆运+集约中转)
}
DEFAULT_CARBON_FACTOR = 0.60

# 距离代理(km, 按收件地确定性分档)
DISTANCE_PROXY = {
    "same_city": 50.0, "inland": 300.0,
    "cross_province": 1000.0, "remote": 2500.0,
}
REMOTE_PROVINCES = ("新疆", "西藏", "青海", "内蒙古", "甘肃", "宁夏")

SUGGESTION_TYPE_NAMES = {
    "circuit_switch": "渠道熔断切换",
    "carbon_green": "碳排绿色切换",
    "anomaly_prevention": "异常预防规则",
}


class ZwEvolution2Service:
    """P7: 偏好学习 + 异常模式 + 碳足迹 + 人机协同"""

    def __init__(self, store: _ZwStore = None,
                 fabric: ZwFabricService = None,
                 track: ZwTrackService = None,
                 circuit: ZwCircuitService = None):
        self.store = store or _ZwStore()
        self.fabric = fabric or ZwFabricService(self.store)
        self.track = track or ZwTrackService(store=self.store)
        self.circuit = circuit or ZwCircuitService(store=self.store,
                                                   fabric=self.fabric,
                                                   track=self.track)

    # ============================================================
    # 个性化偏好学习(B端/C端, 确定性登记与应用)
    # ============================================================

    async def save_preference(self, member_id: int, scope: str,
                              prefs: dict, note: str = "") -> dict:
        """偏好登记(B端特殊要求 / C端收货习惯)"""
        if scope not in ("b2b", "b2c"):
            raise ValueError("偏好范围无效(b2b/b2c)")
        allowed = B2B_PREF_KEYS if scope == "b2b" else B2C_PREF_KEYS
        clean = {k: v for k, v in (prefs or {}).items()
                 if k in allowed}
        if not clean:
            raise ValueError(f"偏好键无效(允许: {sorted(allowed)})")

        existing = None
        for p in await self.store.list("preferences"):
            if p.get("memberId") == member_id and p.get("scope") == scope:
                existing = p
                break
        if existing:
            merged = {**existing["prefs"], **clean}
            existing.update({"prefs": merged, "note": note or existing
                            .get("note", ""), "updatedAt": _now_iso()})
            await self.store.save("preferences",
                                  existing["preferenceId"], existing)
            return existing
        record = {
            "preferenceId": await self.store.next_id("preferences"),
            "memberId": member_id, "scope": scope,
            "scopeName": "B端特殊要求" if scope == "b2b" else "C端收货习惯",
            "prefs": clean, "note": note,
            "createdAt": _now_iso(), "updatedAt": _now_iso(),
        }
        await self.store.save("preferences",
                              record["preferenceId"], record)
        return record

    async def preferences(self, scope: str = None) -> list[dict]:
        rows = await self.store.list("preferences")
        if scope:
            rows = [r for r in rows if r.get("scope") == scope]
        return sorted(rows, key=lambda r: r.get("updatedAt", ""),
                      reverse=True)

    async def preference_of(self, member_id: int) -> dict | None:
        for p in await self.store.list("preferences"):
            if p.get("memberId") == member_id:
                return p
        return None

    def apply_to_profile(self, pref: dict | None) -> list[str]:
        """偏好应用到订单画像(输出提示标签, 确定性拼接)"""
        if not pref:
            return []
        hints = []
        prefs = pref.get("prefs", {})
        if prefs.get("weekdayOnly"):
            hints.append("须工作日配送(B端要求)")
        if prefs.get("contactPerson"):
            hints.append(f"指定联系人: {prefs['contactPerson']}(B端要求)")
        if prefs.get("appointedSlot"):
            hints.append(f"预约时段: {prefs['appointedSlot']}(B端要求)")
        if prefs.get("stationDrop"):
            hints.append("习惯放驿站(C端偏好)")
        if prefs.get("weekendDelivery"):
            hints.append("偏好周末配送(C端偏好)")
        if prefs.get("quietDelivery"):
            hints.append("免打扰静默配送(C端偏好)")
        return hints

    # ============================================================
    # 异常模式识别(渠道×类型确定性聚类 → 预防性规则建议)
    # ============================================================

    async def anomaly_patterns(self) -> dict:
        """历史异常聚类: (渠道×类型) 计数排序 → 高频模式 + 预防建议"""
        anomalies = await self.track.detect_anomalies(limit=500)
        orders = await self.fabric.repo.list_orders(limit=500)
        total = len(orders) or 1

        buckets: dict[str, dict] = {}
        for a in anomalies:
            key = f"{a.get('carrier', '')}|{a.get('type', '')}"
            b = buckets.setdefault(key, {"carrier": a.get("carrier", ""),
                                         "type": a.get("type", ""),
                                         "count": 0, "details": []})
            b["count"] += 1
            if len(b["details"]) < 3:
                b["details"].append(a.get("detail", ""))

        prevention_map = {
            "pickup_timeout": "建议: 揽收SLA重申+渠道运力预约前置",
            "stagnation": "建议: 中转节点巡检+主动轨迹探测加密",
            "deliver_failed": "建议: 派前电联+地址二次确认",
            "sign_timeout": "建议: 签收超时预警线前移+客服前置介入",
            "damage": "建议: 缓冲包装升级+渠道约谈",
        }
        patterns = []
        for _key, b in sorted(buckets.items(),
                              key=lambda x: -x[1]["count"]):
            carrier_name = CARRIER_NAMES.get(b["carrier"], b["carrier"])
            patterns.append({
                "carrier": b["carrier"], "carrierName": carrier_name,
                "anomalyType": b["type"], "count": b["count"],
                "orderShare": _round2(b["count"] / total),
                "details": b["details"],
                "preventionRule": prevention_map.get(
                    b["type"], "建议: 人工归因分析"),
            })
        return {
            "patterns": patterns,
            "formula": "异常模式: (渠道×类型)确定性聚类计数排序",
            "note": "预防性规则为建议, 人工确认后纳入 SOP(永不自动)",
            "analyzedAt": _now_iso(),
        }

    # ============================================================
    # 碳足迹核算(排放因子法, 确定性)
    # ============================================================

    @staticmethod
    def _distance_of(order: dict) -> tuple[str, float]:
        """距离分档(确定性): 同城/省内/跨省/偏远

        订单字段为扁平结构(province/city 顶层)。
        """
        province = str(order.get("province", "") or "")
        city = str(order.get("city", "") or "")
        if city == "成都":
            return "same_city", DISTANCE_PROXY["same_city"]
        if any(p in province for p in REMOTE_PROVINCES):
            return "remote", DISTANCE_PROXY["remote"]
        if province == "四川":
            return "inland", DISTANCE_PROXY["inland"]
        return "cross_province", DISTANCE_PROXY["cross_province"]

    async def carbon_overview(self) -> dict:
        """碳足迹总览(单均公式 + 渠道占比 + 绿色切换建议)

        单均碳排(kg) = 重量(kg)/1000 × 距离代理(km) × 渠道因子
        """
        orders = await self.fabric.repo.list_orders(limit=500)
        rows, total_kg = [], 0.0
        by_carrier: dict[str, dict] = {}
        for o in orders:
            carrier = str(o.get("carrier", "") or "")
            weight = float(o.get("weight", 0) or 0)
            band, km = self._distance_of(o)
            factor = CARRIER_CARBON_FACTORS.get(carrier,
                                                DEFAULT_CARBON_FACTOR)
            kg = _round2(weight / 1000 * km * factor)
            total_kg += kg
            c = by_carrier.setdefault(carrier, {
                "carrier": carrier,
                "carrierName": CARRIER_NAMES.get(carrier, carrier),
                "orders": 0, "totalKg": 0.0, "avgKg": 0.0})
            c["orders"] += 1
            c["totalKg"] = _round2(c["totalKg"] + kg)
            if len(rows) < 10:   # 明细展示前 10 单
                rows.append({"waybillNo": o.get("waybillNo", ""),
                             "carrier": carrier, "distanceBand": band,
                             "km": km, "factor": factor, "kg": kg})
        for c in by_carrier.values():
            c["avgKg"] = _round2(c["totalKg"] / c["orders"]) \
                if c["orders"] else 0.0
            c["sharePct"] = _round2(100 * c["totalKg"] / total_kg) \
                if total_kg else 0.0

        # 绿色切换建议(确定性: 快件高因子→零担低因子可省, 逐单重算)
        saving = 0.0
        for o in orders:
            carrier = str(o.get("carrier", "") or "")
            if CARRIER_CARBON_FACTORS.get(carrier, .6) \
                    > CARRIER_CARBON_FACTORS["DB"]:
                _, km = self._distance_of(o)
                weight = float(o.get("weight", 0) or 0)
                saving += weight / 1000 * km * (
                    CARRIER_CARBON_FACTORS[carrier]
                    - CARRIER_CARBON_FACTORS["DB"])

        return {
            "totalOrders": len(orders),
            "totalCarbonKg": _round2(total_kg),
            "avgCarbonKg": _round2(total_kg / len(orders))
            if orders else 0.0,
            "formula": "单均碳排(kg) = 重量(kg)/1000 × 距离代理(km) × "
                       "渠道因子(确定性)",
            "distanceProxy": DISTANCE_PROXY,
            "carrierFactors": CARRIER_CARBON_FACTORS,
            "byCarrier": sorted(by_carrier.values(),
                                key=lambda x: -x["totalKg"]),
            "details": rows,
            "greenSuggestion": {
                "switchableSavingKg": _round2(saving),
                "body": (f"若快件高碳渠道可切部分至德邦零担(同线路), "
                         f"年化可减约 {_round2(saving * 12)}kg CO2; "
                         "碳数据对接 68 号碳积分语义(不可交易, 仅履约"
                         "透明化)"),
                "disposition": "绿色切换为建议书, 人工确认后执行",
            },
            "analyzedAt": _now_iso(),
        }

    # ============================================================
    # 人机协同进化(策略建议 → 确认采纳/拒绝负样本)
    # ============================================================

    async def suggest(self) -> dict:
        """聚合策略建议(熔断+碳排+异常模式 → 建议清单)"""
        suggestions = []

        status = await self.circuit.circuit_status()
        for c in status["carriers"]:
            if c.get("state") == "open":
                suggestions.append({
                    "type": "circuit_switch",
                    "typeName": SUGGESTION_TYPE_NAMES["circuit_switch"],
                    "title": f"{c['carrierName']} 熔断切换评估",
                    "body": f"渠道跌破阈值({'; '.join(c['reasons'])}), "
                            "建议评估流量切换(详见运力异常报告)",
                })

        carbon = await self.carbon_overview()
        top = carbon["byCarrier"][:1]
        if top and carbon["totalCarbonKg"] > 0:
            suggestions.append({
                "type": "carbon_green",
                "typeName": SUGGESTION_TYPE_NAMES["carbon_green"],
                "title": f"碳排榜首 {top[0]['carrierName']} 绿色优化",
                "body": (f"渠道碳排占比 {top[0]['sharePct']:.0f}%, "
                         f"可切换部分至低因子渠道, 预计年化减排 "
                         f"{carbon['greenSuggestion']['switchableSavingKg'] * 12:.0f}kg"),
            })

        patterns = await self.anomaly_patterns()
        for p in patterns["patterns"][:2]:
            if p["count"] >= 2:
                suggestions.append({
                    "type": "anomaly_prevention",
                    "typeName": SUGGESTION_TYPE_NAMES[
                        "anomaly_prevention"],
                    "title": (f"{p['carrierName']}×{p['anomalyType']}"
                              f" 频发({p['count']}例)"),
                    "body": p["preventionRule"],
                })

        saved = []
        for s in suggestions:
            record = {
                "suggestionId": await self.store.next_id(
                    "strategy_suggestions"),
                "status": "pending",   # pending/adopted/rejected
                **s, "raisedAt": _now_iso(),
            }
            await self.store.save("strategy_suggestions",
                                  record["suggestionId"], record)
            saved.append(record)
        return {
            "generated": len(saved), "suggestions": saved,
            "note": "策略建议须人工裁决; 拒绝记负样本(回流观测, "
                   "永不自动生效)", "suggestedAt": _now_iso(),
        }

    async def suggestions(self, status: str = None) -> list[dict]:
        rows = await self.store.list("strategy_suggestions")
        if status:
            rows = [r for r in rows if r.get("status") == status]
        return sorted(rows, key=lambda r: r.get("raisedAt", ""),
                      reverse=True)

    async def decide(self, suggestion_id: int,
                     verdict: str) -> dict:
        """人机协同裁决(adopted 采纳 / rejected 拒绝=负样本)"""
        record = await self.store.get("strategy_suggestions",
                                      suggestion_id)
        if record is None:
            raise KeyError(f"建议不存在(suggestionId={suggestion_id})")
        if verdict not in ("adopted", "rejected"):
            raise ValueError("裁决无效(adopted/rejected)")
        if record.get("status") != "pending":
            raise ValueError(f"建议已裁决({record['status']}, 不可重复)")
        record["status"] = verdict
        record["decidedAt"] = _now_iso()
        if verdict == "rejected":
            record["negativeSample"] = (
                "负样本回流: 建议被人工拒绝, 留痕供阈值/模板校准观测"
                "(决策权永在人工)")
        await self.store.save("strategy_suggestions",
                              suggestion_id, record)
        return record
