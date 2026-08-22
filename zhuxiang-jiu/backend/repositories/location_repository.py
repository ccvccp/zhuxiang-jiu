"""位置地图管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    user_addresses:       收货地址表
    stores:               门店表
    agent_locations:      代理商位置表
    shipment_tracks:      物流轨迹表
    delivery_zones:       配送范围表
    blockchain_evidence:  区块链存证表

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 收货地址: 最多20条/用户
    - 门店类型: 旗舰店/体验店/专卖店
    - 配送范围: 全国/同城/自提/偏远
    - 存证类型: address/location/logistics/delivery/heatmap/site
"""

import json
import math
from datetime import datetime
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 门店类型
# ============================================================

STORE_TYPE_FLAGSHIP = "flagship"    # 旗舰店
STORE_TYPE_EXPERIENCE = "experience"  # 体验店
STORE_TYPE_EXCLUSIVE = "exclusive"  # 专卖店

# 门店状态
STORE_STATUS_OPEN = "open"          # 营业中
STORE_STATUS_CLOSED = "closed"      # 休息中

# 代理商等级
AGENT_LEVEL_DIAMOND = "diamond"    # 钻石
AGENT_LEVEL_GOLD = "gold"          # 金牌
AGENT_LEVEL_SILVER = "silver"      # 银牌

# 物流状态
SHIPMENT_STATUS_IN_TRANSIT = "in_transit"  # 在途
SHIPMENT_STATUS_DELIVERING = "delivering"  # 派送中
SHIPMENT_STATUS_SIGNED = "signed"          # 签收

# 配送范围类型
ZONE_TYPE_NATIONAL = "national"    # 全国
ZONE_TYPE_CITY = "city"            # 同城
ZONE_TYPE_SELF = "self"            # 自提
ZONE_TYPE_REMOTE = "remote"        # 偏远

# 地址标签
LABEL_HOME = "home"
LABEL_COMPANY = "company"
LABEL_SCHOOL = "school"

# 存证类型
EVIDENCE_TYPE_ADDRESS = "address"
EVIDENCE_TYPE_LOCATION = "location"
EVIDENCE_TYPE_LOGISTICS = "logistics"
EVIDENCE_TYPE_DELIVERY = "delivery"
EVIDENCE_TYPE_HEATMAP = "heatmap"
EVIDENCE_TYPE_SITE = "site"

# 收货地址上限
MAX_ADDRESSES_PER_USER = 20

# 地球半径(km)
EARTH_RADIUS_KM = 6371.0


def haversine_km(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """计算两点间球面距离(km, Haversine公式)"""
    rad_lat1 = math.radians(lat1)
    rad_lat2 = math.radians(lat2)
    d_lat = rad_lat2 - rad_lat1
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(rad_lat1) * math.cos(rad_lat2) * math.sin(d_lng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


class LocationRepository:
    """位置地图管理数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_address_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("loc_address")
        return self._mem_next_id("_loc_address_seq")

    async def next_store_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("loc_store")
        return self._mem_next_id("_loc_store_seq")

    async def next_agent_loc_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("loc_agent")
        return self._mem_next_id("_loc_agent_seq")

    async def next_track_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("loc_track")
        return self._mem_next_id("_loc_track_seq")

    async def next_zone_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("loc_zone")
        return self._mem_next_id("_loc_zone_seq")

    async def next_evidence_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("loc_evidence")
        return self._mem_next_id("_loc_evidence_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("loc", entity, "seq"))

    # ============================================================
    # 收货地址表 CRUD
    # ============================================================

    async def create_address(self, address: dict) -> int:
        address_id = await self.next_address_id()
        address["id"] = address_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in address:
            address["createdAt"] = now
        if "isDefault" not in address:
            address["isDefault"] = False
        if is_redis_mode():
            await self._redis_create("loc", "address", address_id, address)
            await self._redis_add_to_list("loc", "address_by_user",
                                            address.get("userId"), address_id)
        else:
            self._mem_create("loc_addresses", address_id, address)
            self._mem_add_to_list("loc_addresses_by_user", address.get("userId"), address_id)
        return address_id

    async def get_address(self, address_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get("loc", "address", address_id)
        return self._mem_get("loc_addresses", address_id)

    async def list_addresses(self, user_id: int) -> list[dict]:
        """查询用户地址列表(默认地址排前, 再按创建时间倒序)"""
        if is_redis_mode():
            ids = await self._redis_get_list("loc", "address_by_user", user_id)
            addresses = []
            for aid in ids:
                data = await self._redis_get("loc", "address", int(aid))
                if data:
                    addresses.append(data)
        else:
            self._ensure_store()
            ids = self.store.get("loc_addresses_by_user", {}).get(user_id, [])
            addresses = [self.store["loc_addresses"][aid] for aid in ids
                         if aid in self.store["loc_addresses"]]
        # 两阶段排序: 先按创建时间倒序, 再按默认优先(stable sort保持时间顺序)
        addresses.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        addresses.sort(key=lambda a: not a.get("isDefault", False))
        return addresses

    async def update_address(self, address_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("loc", "address", address_id, updates)
        else:
            self._mem_update("loc_addresses", address_id, updates)

    async def delete_address(self, address_id: int) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.delete(_k("loc", "address", address_id))
        else:
            self._ensure_store()
            self.store["loc_addresses"].pop(address_id, None)

    async def count_user_addresses(self, user_id: int) -> int:
        """统计用户地址数量"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.llen(_k("loc", "address_by_user", user_id))
        self._ensure_store()
        return len(self.store.get("loc_addresses_by_user", {}).get(user_id, []))

    async def clear_default_address(self, user_id: int) -> None:
        """清除用户其他默认地址"""
        addresses = await self.list_addresses(user_id)
        for addr in addresses:
            if addr.get("isDefault"):
                await self.update_address(addr["id"], {"isDefault": False})

    # ============================================================
    # 门店表 CRUD
    # ============================================================

    async def create_store(self, store: dict) -> int:
        store_id = await self.next_store_id()
        store["id"] = store_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in store:
            store["createdAt"] = now
        if "status" not in store:
            store["status"] = STORE_STATUS_OPEN
        if is_redis_mode():
            await self._redis_create("loc", "store", store_id, store)
        else:
            self._mem_create("loc_stores", store_id, store)
        return store_id

    async def get_store(self, store_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get("loc", "store", store_id)
        return self._mem_get("loc_stores", store_id)

    async def list_stores(self, city: str = None, store_type: str = None,
                            status: str = None, limit: int = 50) -> list[dict]:
        if is_redis_mode():
            stores = await self._redis_list_all("loc", "store", limit)
        else:
            stores = self._mem_list_all("loc_stores", limit)
        if city:
            stores = [s for s in stores if s.get("city") == city]
        if store_type:
            stores = [s for s in stores if s.get("storeType") == store_type]
        if status:
            stores = [s for s in stores if s.get("status") == status]
        return stores[:limit]

    async def list_nearby_stores(self, longitude: float, latitude: float,
                                   radius_km: float = 5.0, limit: int = 50) -> list[dict]:
        """查询附近门店(按距离排序)"""
        if is_redis_mode():
            stores = await self._redis_list_all("loc", "store", limit * 5)
        else:
            stores = self._mem_list_all("loc_stores", limit * 5)
        nearby = []
        for s in stores:
            s_lng = s.get("longitude")
            s_lat = s.get("latitude")
            if s_lng is None or s_lat is None:
                continue
            distance = haversine_km(longitude, latitude, s_lng, s_lat)
            if distance <= radius_km:
                s_copy = dict(s)
                s_copy["distance"] = round(distance, 2)
                nearby.append(s_copy)
        nearby.sort(key=lambda s: s["distance"])
        return nearby[:limit]

    # ============================================================
    # 代理商位置表 CRUD
    # ============================================================

    async def create_agent_location(self, agent: dict) -> int:
        agent_id = await self.next_agent_loc_id()
        agent["id"] = agent_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in agent:
            agent["createdAt"] = now
        if is_redis_mode():
            await self._redis_create("loc", "agent", agent_id, agent)
        else:
            self._mem_create("loc_agent_locations", agent_id, agent)
        return agent_id

    async def get_agent_location(self, agent_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get("loc", "agent", agent_id)
        return self._mem_get("loc_agent_locations", agent_id)

    async def list_agent_locations(self, province: str = None, city: str = None,
                                     agent_level: str = None, limit: int = 50) -> list[dict]:
        if is_redis_mode():
            agents = await self._redis_list_all("loc", "agent", limit)
        else:
            agents = self._mem_list_all("loc_agent_locations", limit)
        if province:
            agents = [a for a in agents if a.get("province") == province]
        if city:
            agents = [a for a in agents if a.get("city") == city]
        if agent_level:
            agents = [a for a in agents if a.get("agentLevel") == agent_level]
        return agents[:limit]

    async def list_nearby_agents(self, longitude: float, latitude: float,
                                    radius_km: float = 50.0, limit: int = 50) -> list[dict]:
        """查询附近代理商"""
        if is_redis_mode():
            agents = await self._redis_list_all("loc", "agent", limit * 5)
        else:
            agents = self._mem_list_all("loc_agent_locations", limit * 5)
        nearby = []
        for a in agents:
            a_lng = a.get("longitude")
            a_lat = a.get("latitude")
            if a_lng is None or a_lat is None:
                continue
            distance = haversine_km(longitude, latitude, a_lng, a_lat)
            if distance <= radius_km:
                a_copy = dict(a)
                a_copy["distance"] = round(distance, 2)
                nearby.append(a_copy)
        nearby.sort(key=lambda a: a["distance"])
        return nearby[:limit]

    # ============================================================
    # 物流轨迹表 CRUD
    # ============================================================

    async def create_shipment_track(self, track: dict) -> int:
        track_id = await self.next_track_id()
        track["id"] = track_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in track:
            track["createdAt"] = now
        if "updatedAt" not in track:
            track["updatedAt"] = now
        if "status" not in track:
            track["status"] = SHIPMENT_STATUS_IN_TRANSIT
        if is_redis_mode():
            await self._redis_create("loc", "track", track_id, track)
            if track.get("shipmentId"):
                client = await get_redis_client()
                await client.set(_k("loc", "track_by_shipment", track["shipmentId"]), track_id)
        else:
            self._mem_create("loc_shipment_tracks", track_id, track)
            if track.get("shipmentId"):
                self.store["loc_tracks_by_shipment"][track["shipmentId"]] = track_id
        return track_id

    async def get_shipment_track(self, track_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get("loc", "track", track_id)
        return self._mem_get("loc_shipment_tracks", track_id)

    async def get_track_by_shipment(self, shipment_id: str) -> Optional[dict]:
        """按运单号查询轨迹"""
        if is_redis_mode():
            client = await get_redis_client()
            track_id = await client.get(_k("loc", "track_by_shipment", shipment_id))
            if not track_id:
                return None
            return await self._redis_get("loc", "track", int(track_id))
        self._ensure_store()
        track_id = self.store.get("loc_tracks_by_shipment", {}).get(shipment_id)
        if track_id is None:
            return None
        return self.store["loc_shipment_tracks"].get(track_id)

    async def update_shipment_track(self, track_id: int, updates: dict) -> None:
        updates["updatedAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_update("loc", "track", track_id, updates)
        else:
            self._mem_update("loc_shipment_tracks", track_id, updates)

    async def list_shipment_tracks(self, order_id: int = None, status: str = None,
                                     limit: int = 50) -> list[dict]:
        if is_redis_mode():
            tracks = await self._redis_list_all("loc", "track", limit)
        else:
            tracks = self._mem_list_all("loc_shipment_tracks", limit)
        if order_id:
            tracks = [t for t in tracks if t.get("orderId") == order_id]
        if status:
            tracks = [t for t in tracks if t.get("status") == status]
        tracks.sort(key=lambda t: t.get("updatedAt", ""), reverse=True)
        return tracks[:limit]

    # ============================================================
    # 配送范围表 CRUD
    # ============================================================

    async def create_delivery_zone(self, zone: dict) -> int:
        zone_id = await self.next_zone_id()
        zone["id"] = zone_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in zone:
            zone["createdAt"] = now
        if "status" not in zone:
            zone["status"] = "active"
        if is_redis_mode():
            await self._redis_create("loc", "zone", zone_id, zone)
        else:
            self._mem_create("loc_delivery_zones", zone_id, zone)
        return zone_id

    async def get_delivery_zone(self, zone_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get("loc", "zone", zone_id)
        return self._mem_get("loc_delivery_zones", zone_id)

    async def list_delivery_zones(self, zone_type: str = None,
                                    limit: int = 50) -> list[dict]:
        if is_redis_mode():
            zones = await self._redis_list_all("loc", "zone", limit)
        else:
            zones = self._mem_list_all("loc_delivery_zones", limit)
        if zone_type:
            zones = [z for z in zones if z.get("zoneType") == zone_type]
        return zones[:limit]

    async def check_delivery_point(self, longitude: float, latitude: float) -> list[dict]:
        """检测坐标所在配送范围"""
        if is_redis_mode():
            zones = await self._redis_list_all("loc", "zone", 10000)
        else:
            zones = self._mem_list_all("loc_delivery_zones", 10000)
        matched = []
        for z in zones:
            z_type = z.get("zoneType")
            if z_type == ZONE_TYPE_NATIONAL:
                matched.append(z)
                continue
            center_lng = z.get("centerLng")
            center_lat = z.get("centerLat")
            radius = z.get("radius", 0)
            if center_lng is None or center_lat is None or radius == 0:
                continue
            distance = haversine_km(longitude, latitude, center_lng, center_lat)
            if distance <= radius:
                z_copy = dict(z)
                z_copy["distance"] = round(distance, 2)
                matched.append(z_copy)
        return matched

    # ============================================================
    # 区块链存证表 CRUD
    # ============================================================

    async def create_evidence(self, evidence: dict) -> int:
        evidence_id = await self.next_evidence_id()
        evidence["id"] = evidence_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in evidence:
            evidence["createdAt"] = now
        if "blockHeight" not in evidence:
            evidence["blockHeight"] = 0
        if is_redis_mode():
            await self._redis_create("loc", "evidence", evidence_id, evidence)
            evidence_hash = evidence.get("evidenceHash")
            if evidence_hash:
                client = await get_redis_client()
                await client.set(_k("loc", "evidence_hash", evidence_hash), evidence_id)
        else:
            self._mem_create("loc_evidence", evidence_id, evidence)
            if evidence.get("evidenceHash"):
                self.store["loc_evidence_by_hash"][evidence["evidenceHash"]] = evidence_id
        return evidence_id

    async def get_evidence(self, evidence_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get("loc", "evidence", evidence_id)
        return self._mem_get("loc_evidence", evidence_id)

    async def get_evidence_by_hash(self, evidence_hash: str) -> Optional[dict]:
        """按哈希查询存证"""
        if is_redis_mode():
            client = await get_redis_client()
            evidence_id = await client.get(_k("loc", "evidence_hash", evidence_hash))
            if not evidence_id:
                return None
            return await self._redis_get("loc", "evidence", int(evidence_id))
        self._ensure_store()
        evidence_id = self.store.get("loc_evidence_by_hash", {}).get(evidence_hash)
        if evidence_id is None:
            return None
        return self.store["loc_evidence"].get(evidence_id)

    async def list_evidence(self, evidence_type: str = None,
                              limit: int = 50) -> list[dict]:
        if is_redis_mode():
            evidences = await self._redis_list_all("loc", "evidence", limit)
        else:
            evidences = self._mem_list_all("loc_evidence", limit)
        if evidence_type:
            evidences = [e for e in evidences if e.get("evidenceType") == evidence_type]
        evidences.sort(key=lambda e: e.get("createdAt", ""), reverse=True)
        return evidences[:limit]

    # ============================================================
    # 内存模式通用实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含位置模块的键(懒初始化)"""
        if "loc_addresses" not in self.store:
            self.store["loc_addresses"] = {}
            self.store["loc_addresses_by_user"] = {}
            self.store["loc_stores"] = {}
            self.store["loc_agent_locations"] = {}
            self.store["loc_shipment_tracks"] = {}
            self.store["loc_tracks_by_shipment"] = {}
            self.store["loc_delivery_zones"] = {}
            self.store["loc_evidence"] = {}
            self.store["loc_evidence_by_hash"] = {}
            self.store["_loc_address_seq"] = 0
            self.store["_loc_store_seq"] = 0
            self.store["_loc_agent_seq"] = 0
            self.store["_loc_track_seq"] = 0
            self.store["_loc_zone_seq"] = 0
            self.store["_loc_evidence_seq"] = 0

    def _mem_create(self, table: str, record_id: int, record: dict) -> None:
        self._ensure_store()
        self.store[table][record_id] = record

    def _mem_get(self, table: str, record_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store[table].get(record_id)

    def _mem_update(self, table: str, record_id: int, updates: dict) -> None:
        self._ensure_store()
        record = self.store[table].get(record_id)
        if record:
            record.update(updates)

    def _mem_list_all(self, table: str, limit: int) -> list[dict]:
        self._ensure_store()
        return list(self.store[table].values())[:limit]

    def _mem_add_to_list(self, list_table: str, key, value) -> None:
        self._ensure_store()
        self.store.setdefault(list_table, {}).setdefault(key, []).append(value)

    # ============================================================
    # Redis 模式通用实现
    # ============================================================

    async def _redis_create(self, module: str, entity: str, record_id: int,
                              record: dict) -> None:
        client = await get_redis_client()
        await client.set(_k(module, entity, record_id),
                         json.dumps(record, ensure_ascii=False))

    async def _redis_get(self, module: str, entity: str,
                           record_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k(module, entity, record_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_update(self, module: str, entity: str,
                              record_id: int, updates: dict) -> None:
        client = await get_redis_client()
        data = await client.get(_k(module, entity, record_id))
        if data:
            record = json.loads(data)
            record.update(updates)
            await client.set(_k(module, entity, record_id),
                             json.dumps(record, ensure_ascii=False))

    async def _redis_list_all(self, module: str, entity: str,
                                limit: int) -> list[dict]:
        client = await get_redis_client()
        records = []
        keys = await client.keys(_k(module, entity, "*"))
        for key in keys:
            if "seq" in key or "by_user" in key or "by_shipment" in key or "evidence_hash" in key:
                continue
            data = await client.get(key)
            if data:
                records.append(json.loads(data))
        return records[:limit]

    async def _redis_add_to_list(self, module: str, list_name: str,
                                   key, value) -> None:
        client = await get_redis_client()
        await client.lpush(_k(module, list_name, key), value)

    async def _redis_get_list(self, module: str, list_name: str,
                                key) -> list:
        client = await get_redis_client()
        return await client.lrange(_k(module, list_name, key), 0, -1)
