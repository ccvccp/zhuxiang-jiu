"""智图·P0 意图引擎(zt_intent_service)

创新规划 §三 P0: 从"关键词匹配"到"全角色语义理解"
    - 业务本体表: 12 类意图词 → POI 能力标签(确定性映射)
    - 复合意图解析: 自然语言 → 意图词抽取 → 能力向量(AND 匹配)
    - 六角色画像: 角色语义/视图能力矩阵
    - 复合意图时空搜索: 能力 AND 匹配 × 距离/评分/余量/营业加权排序

铁律: 全链确定性分词与映射; LLM 禁入(如配置仅润色话术, 不产标签)。
"""

import logging

from repositories.location_repository import haversine_km
from services.zt_fabric_service import (
    _ZtStore, ZtFabricService, _round2, _now_iso)

logger = logging.getLogger(__name__)

# ============================================================
# 业务本体: 意图词 → POI 能力标签(确定性词表)
# ============================================================
INTENT_ONTOLOGY = {
    "buy_wine": {"words": ["买酒", "购酒", "买酒水", "售酒", "酒水零售",
                           "采购酒", "买瓶酒"],
                 "capability": "retail", "name": "购酒"},
    "dining": {"words": ["吃饭", "用餐", "就餐", "餐饮", "吃饭的地方",
                         "宴请", "午餐", "晚餐"],
               "capability": "dining", "name": "用餐"},
    "tasting": {"words": ["品鉴", "品酒", "试饮", "体验馆", "品鉴会"],
                "capability": "tasting", "name": "品鉴"},
    "lodging": {"words": ["住宿", "住一晚", "酒店", "民宿"],
                "capability": "lodging", "name": "住宿"},
    "pickup": {"words": ["提货", "自提", "取货", "取件"],
               "capability": "pickup", "name": "提货"},
    "parking": {"words": ["停车", "泊车", "车位"],
                "capability": "parking", "name": "停车"},
    "supply": {"words": ["送原料", "送货", "卸货", "供货", "交货",
                         "到货", "送粮食", "送高粱"],
               "capability": "dock", "name": "供货交付"},
    "service": {"words": ["售后", "维修", "客服", "服务点"],
                "capability": "service", "name": "服务"},
    "visit": {"words": ["参观", "打卡", "游览", "酒庄"],
              "capability": "tasting", "name": "参观体验"},
}

# 六角色画像(视图能力矩阵, 确定性)
ROLE_PROFILES = {
    "consumer": {
        "name": "消费者",
        "capabilities": ["buy_wine", "dining", "tasting", "lodging",
                         "pickup", "parking", "service", "visit"],
        "view": "购货+订餐+体验一体化(LBS 履约/动线引导)",
    },
    "supplier": {
        "name": "供货商/农户",
        "capabilities": ["supply"],
        "view": "原料交付(月台预约/收货可视/结算溯源)",
    },
    "b2b": {
        "name": "采购商/B端客户",
        "capabilities": ["buy_wine", "pickup"],
        "view": "批量订货(多仓匹配/履约看板/授权校验)",
    },
    "operations": {
        "name": "内部运营/客服",
        "capabilities": ["service", "supply"],
        "view": "全域调度(态势一张图/异常派单/弹性调度)",
    },
    "agent": {
        "name": "代理商/服务商",
        "capabilities": ["buy_wine", "service", "visit"],
        "view": "区域经营(驾驶舱/客户协同/合规自查)",
    },
    "management": {
        "name": "管理层",
        "capabilities": [],
        "view": "战略决策(沙盘/风险雷达/绩效画像)",
    },
}

# 复合排序权重(确定性)
W_DISTANCE = 0.40
W_RATING = 0.25
W_AVAILABILITY = 0.20
W_OPEN = 0.15
NEARBY_BASELINE_KM = 5.0


class ZtIntentService:
    """P0: 意图解析 + 角色画像 + 复合意图时空搜索"""

    def __init__(self, store: _ZtStore = None,
                 fabric: ZtFabricService = None):
        self.store = store or _ZtStore()
        self.fabric = fabric or ZtFabricService(self.store)

    # ============================================================
    # 意图解析(确定性分词→本体映射)
    # ============================================================

    @staticmethod
    def parse_text(text: str) -> dict:
        """自然语言 → 复合意图(确定性: 本体词表包含匹配)

        "找附近能吃饭还能买酒的地方" → [dining, buy_wine]
        同输入同输出; 未命中 → ValueError(引导换词, 附可用词表)。
        """
        text = str(text or "").strip()
        if not text or len(text) > 200:
            raise ValueError("意图文本无效(1-200字符)")
        matched = []
        for intent_id, spec in INTENT_ONTOLOGY.items():
            for word in spec["words"]:
                if word in text:
                    matched.append(intent_id)
                    break
        if not matched:
            samples = [w for spec in INTENT_ONTOLOGY.values()
                       for w in spec["words"][:2]][:8]
            raise ValueError(
                f"未识别业务意图, 可试试: {'/'.join(samples)}")
        capabilities = sorted({INTENT_ONTOLOGY[i]["capability"]
                               for i in matched})
        return {
            "input": text,
            "intents": [{"intentId": i, "intentName":
                         INTENT_ONTOLOGY[i]["name"]} for i in matched],
            "capabilities": capabilities,
            "compound": len(matched) > 1,
            "formula": "意图 = 本体词表包含匹配(确定性分词)",
            "parsedAt": _now_iso(),
        }

    def ontology(self) -> dict:
        """业务本体表(意图词→能力标签)"""
        return {
            "intents": [
                {"intentId": k, "name": v["name"],
                 "capability": v["capability"],
                 "words": v["words"]}
                for k, v in INTENT_ONTOLOGY.items()],
            "capabilities": sorted({v["capability"]
                                    for v in INTENT_ONTOLOGY.values()}),
            "note": "确定性词表映射; LLM 禁入判定链",
        }

    def roles(self) -> dict:
        """六角色画像矩阵"""
        return {
            "roles": [
                {"roleId": k, "name": v["name"],
                 "intents": v["capabilities"], "view": v["view"]}
                for k, v in ROLE_PROFILES.items()],
            "note": "角色→视图能力矩阵(确定性)",
        }

    # ============================================================
    # 复合意图时空搜索(能力 AND 匹配 + 确定性加权排序)
    # ============================================================

    def _availability_of(self, poi: dict) -> float:
        """资源余量分[0,1]: 座位/库存按 POI 类型取口径"""
        seats = poi.get("seats")
        if isinstance(seats, dict) and seats.get("total"):
            return seats.get("available", 0) / seats["total"]
        return float(poi.get("stockLevel", 0) or 0)

    async def search(self, text: str, longitude: float, latitude: float,
                     role: str = "consumer", radius_km: float = 10.0,
                     limit: int = 10) -> dict:
        """复合意图时空搜索: 解析→POI AND 匹配→四因子加权排序

        综合分 = 0.4×距离分 + 0.25×评分 + 0.2×余量 + 0.15×营业
        距离分 = min(1, 基准5km/实际km)
        """
        if role not in ROLE_PROFILES:
            raise ValueError(f"角色无效({role}: "
                             f"{'/'.join(ROLE_PROFILES)}")
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("坐标非法")
        if not (0.5 <= radius_km <= 200):
            raise ValueError("半径须在 [0.5, 200] km")

        parsed = self.parse_text(text)
        wanted = set(parsed["capabilities"])

        pois = await self.fabric.list_pois()
        results = []
        for p in pois:
            caps = set(p.get("capabilities") or [])
            if not wanted.issubset(caps):
                continue            # 复合意图须全部能力齐备(AND)
            dist = haversine_km(longitude, latitude,
                                float(p["longitude"]),
                                float(p["latitude"]))
            if dist > radius_km:
                continue
            dist_score = min(1.0, NEARBY_BASELINE_KM / dist) \
                if dist > 0 else 1.0
            rating = float(p.get("rating", 0) or 0) / 5.0
            avail = self._availability_of(p)
            open_score = 1.0 if p.get("open") else 0.0
            score = _round2(100 * (W_DISTANCE * dist_score
                                   + W_RATING * rating
                                   + W_AVAILABILITY * avail
                                   + W_OPEN * open_score))
            results.append({
                "poiCode": p["poiCode"], "name": p["name"],
                "poiType": p["poiType"],
                "distance": _round2(dist), "rating": p.get("rating", 0),
                "availability": _round2(avail),
                "open": p.get("open", False), "score": score,
            })
        results.sort(key=lambda r: (-r["score"], r["distance"]))
        return {
            "parsed": parsed, "role": role,
            "results": results[:limit],
            "total": len(results),
            "formula": (f"综合 = 距离×{W_DISTANCE} + 评分×{W_RATING} + "
                        f"余量×{W_AVAILABILITY} + 营业×{W_OPEN}; "
                        f"能力 AND 匹配(确定性)"),
            "searchedAt": _now_iso(),
        }

    def behavior_hints(self, role: str) -> dict:
        """角色主动服务提示(视图入口, 确定性)"""
        if role not in ROLE_PROFILES:
            raise ValueError(f"角色无效({role})")
        prof = ROLE_PROFILES[role]
        intents = [{"intentId": i, "name": INTENT_ONTOLOGY[i]["name"]}
                   for i in prof["capabilities"]
                   if i in INTENT_ONTOLOGY]
        return {
            "role": role, "roleName": prof["name"],
            "view": prof["view"], "entries": intents,
            "note": "主动服务入口(建议); 推送须人工策略配置",
            "generatedAt": _now_iso(),
        }
