"""智图·AI智能地图大模型 共享存储与织物(zt_fabric_service)

「全域角色时空服务中枢」底座(创新规划 §三):
    - _ZtStore: 智图四服务共享存储(内存/Redis 双模式, 对齐 _ZwStore 范式)
    - 全业务 POI 种子: 六类 POI(旗舰店/品鉴馆/餐饮合作/城市仓/月台/
      服务网点)统一时空底座, 含能力标签+实时状态(确定性种子)

铁律: 纯只读聚合 + 叠加式零回写既有位置模块 + LLM 禁入判定链。
"""

import logging
from datetime import datetime, UTC

logger = logging.getLogger(__name__)


def _round2(v) -> float:
    return round(float(v or 0), 2)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


ZT_TABLES = (
    "zt_pois",                    # 全业务 POI(注册表)
    "zt_dock_bookings",           # 供货商月台预约
    "zt_tickets",                 # 异常处置工单
    "zt_behaviors",               # 行为流留痕
    "zt_feedbacks",               # 反馈闭环
    "zt_sandboxes",               # 沙盘推演留痕
)

# ============================================================
# 全业务 POI 种子(确定性; 成都坐标系, 与既有门店域同城市)
# 能力标签: retail(售酒) / dining(餐饮) / tasting(品鉴) /
#          lodging(住宿) / pickup(提货) / parking(停车) /
#          warehouse(仓储) / dock(月台) / service(服务)
# ============================================================
ZT_POI_SEEDS = [
    # 旗舰店/体验店(售酒+品鉴)
    {"poiCode": "F-001", "name": "竹香酒·成都旗舰店", "poiType": "flagship",
     "longitude": 104.066, "latitude": 30.572, "city": "成都",
     "capabilities": ["retail", "tasting", "pickup"],
     "rating": 4.8, "stockLevel": 0.92, "seats": {"total": 30, "available": 12},
     "queue": {"waiting": 2, "avgMinutes": 8}, "open": True},
    {"poiCode": "E-001", "name": "竹香酒·锦江体验馆", "poiType": "experience",
     "longitude": 104.085, "latitude": 30.660, "city": "成都",
     "capabilities": ["retail", "tasting", "dining", "parking"],
     "rating": 4.6, "stockLevel": 0.85, "seats": {"total": 60, "available": 45},
     "queue": {"waiting": 0, "avgMinutes": 0}, "open": True},
    # 餐饮合作点(用餐+售酒)
    {"poiCode": "D-101", "name": "蜀香宴·春熙路店", "poiType": "dining",
     "longitude": 104.081, "latitude": 30.660, "city": "成都",
     "capabilities": ["dining", "retail"],
     "rating": 4.5, "stockLevel": 0.60, "seats": {"total": 80, "available": 8},
     "queue": {"waiting": 6, "avgMinutes": 25}, "open": True},
    {"poiCode": "D-102", "name": "蓉悦小馆·太古里", "poiType": "dining",
     "longitude": 104.083, "latitude": 30.653, "city": "成都",
     "capabilities": ["dining", "retail", "parking"],
     "rating": 4.7, "stockLevel": 0.75, "seats": {"total": 50, "available": 20},
     "queue": {"waiting": 1, "avgMinutes": 5}, "open": True},
    # 城市仓(仓储+提货)
    {"poiCode": "W-001", "name": "竹香酒·成都东仓", "poiType": "warehouse",
     "longitude": 104.210, "latitude": 30.590, "city": "成都",
     "capabilities": ["warehouse", "pickup"],
     "rating": 4.2, "stockLevel": 0.98, "seats": None,
     "queue": {"waiting": 0, "avgMinutes": 0}, "open": True},
    {"poiCode": "W-002", "name": "竹香酒·成都北仓", "poiType": "warehouse",
     "longitude": 104.050, "latitude": 30.720, "city": "成都",
     "capabilities": ["warehouse", "pickup"],
     "rating": 4.0, "stockLevel": 0.55, "seats": None,
     "queue": {"waiting": 0, "avgMinutes": 0}, "open": True},
    # 供货月台(工厂收货; 坐标=酒厂路1号, 对齐既有发货仓)
    {"poiCode": "K-001", "name": "3号车间·A月台", "poiType": "dock",
     "longitude": 104.120, "latitude": 30.540, "city": "成都",
     "capabilities": ["dock"],
     "rating": 4.0, "stockLevel": 1.0, "seats": None,
     "queue": {"waiting": 3, "avgMinutes": 40}, "open": True},
    {"poiCode": "K-002", "name": "3号车间·B月台", "poiType": "dock",
     "longitude": 104.121, "latitude": 30.541, "city": "成都",
     "capabilities": ["dock"],
     "rating": 4.0, "stockLevel": 1.0, "seats": None,
     "queue": {"waiting": 1, "avgMinutes": 15}, "open": True},
    {"poiCode": "K-003", "name": "原料库·C月台", "poiType": "dock",
     "longitude": 104.119, "latitude": 30.539, "city": "成都",
     "capabilities": ["dock"],
     "rating": 4.0, "stockLevel": 1.0, "seats": None,
     "queue": {"waiting": 0, "avgMinutes": 0}, "open": True},
    # 服务网点(售后/品鉴会)
    {"poiCode": "S-001", "name": "竹香酒·高新服务点", "poiType": "service",
     "longitude": 104.070, "latitude": 30.590, "city": "成都",
     "capabilities": ["service", "tasting", "pickup"],
     "rating": 4.4, "stockLevel": 0.70, "seats": {"total": 20, "available": 20},
     "queue": {"waiting": 0, "avgMinutes": 0}, "open": True},
    {"poiCode": "S-002", "name": "竹香酒·武侯服务点(休息中)",
     "poiType": "service",
     "longitude": 104.043, "latitude": 30.640, "city": "成都",
     "capabilities": ["service", "pickup"],
     "rating": 4.1, "stockLevel": 0.40, "seats": {"total": 15, "available": 0},
     "queue": {"waiting": 0, "avgMinutes": 0}, "open": False},
]

POI_TYPE_NAMES = {
    "flagship": "旗舰店", "experience": "体验馆", "dining": "餐饮合作",
    "warehouse": "城市仓", "dock": "供货月台", "service": "服务网点",
}


class _ZtStore:
    """智图大模型共享表存储

    Redis 键: zhuxiang:zt:{table}:{id} / zhuxiang:zt:seq:{entity}
    """

    def __init__(self):
        from repositories.backend import (
            is_redis_mode, get_redis_client, get_in_memory_store)
        self._is_redis = is_redis_mode
        self._get_redis = get_redis_client
        self._store = get_in_memory_store()
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        for table in ZT_TABLES:
            self._store.setdefault(f"zt_{table}", {})
        self._store.setdefault("zt_seq", 0)

    async def next_id(self, entity: str) -> int:
        if self._is_redis():
            client = await self._get_redis()
            return await client.incr(f"zhuxiang:zt:seq:{entity}")
        key = f"zt_seq_{entity}"
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    async def save(self, table: str, record_id, record: dict) -> None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            await client.set(f"zhuxiang:zt:{table}:{record_id}",
                             _json.dumps(record, ensure_ascii=False))
        else:
            self._ensure_tables()
            self._store[f"zt_{table}"][record_id] = record

    async def get(self, table: str, record_id) -> dict | None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            raw = await client.get(f"zhuxiang:zt:{table}:{record_id}")
            return _json.loads(raw) if raw else None
        self._ensure_tables()
        return self._store[f"zt_{table}"].get(record_id)

    async def list(self, table: str) -> list[dict]:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            rows = []
            async for key in client.scan_iter(
                    match=f"zhuxiang:zt:{table}:*"):
                skey = key.decode() if isinstance(key, bytes) else key
                if ":seq:" in skey:
                    continue
                raw = await client.get(skey)
                if raw:
                    try:
                        rows.append(_json.loads(raw))
                    except (ValueError, TypeError):
                        continue
            return rows
        self._ensure_tables()
        return list(self._store[f"zt_{table}"].values())


class ZtFabricService:
    """全业务 POI 织物: 种子 + 注册表统一读取"""

    def __init__(self, store: _ZtStore = None):
        self.store = store or _ZtStore()

    async def list_pois(self, poi_type: str = None,
                        capability: str = None) -> list[dict]:
        """全量 POI(种子 + 注册表, 确定性合并)"""
        rows = []
        seen = set()
        for s in ZT_POI_SEEDS:
            row = dict(s)
            row["source"] = "seed"
            rows.append(row)
            seen.add(s["poiCode"])
        for r in await self.store.list("zt_pois"):
            code = r.get("poiCode", "")
            if code in seen:
                # 注册表覆盖同码种子(更新语义)
                rows = [x for x in rows if x["poiCode"] != code]
            row = dict(r)
            row["source"] = "registered"
            rows.append(row)
            seen.add(code)
        if poi_type:
            rows = [r for r in rows if r.get("poiType") == poi_type]
        if capability:
            rows = [r for r in rows
                    if capability in (r.get("capabilities") or [])]
        return rows

    async def get_poi(self, poi_code: str) -> dict | None:
        for r in await self.list_pois():
            if r.get("poiCode") == poi_code:
                return r
        return None
