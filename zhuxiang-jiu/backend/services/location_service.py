"""位置地图管理模块业务逻辑层

核心业务:
    - 收货地址管理: CRUD + 默认地址 + 地址上限20条
    - 附近门店: 按距离排序 + 半径筛选
    - 代理商查询: 按区域/等级/距离查询
    - 物流轨迹追踪: 创建/更新/查询轨迹
    - 配送范围检测: 坐标→配送范围匹配
    - 区块链存证: 地址/定位/物流记录上链

锁保护:
    - 地址默认设置: lock:loc:address:default:{user_id} (清除其他默认)
    - 物流轨迹更新: lock:loc:track:{track_id} (轨迹更新原子)

异常约定:
    - KeyError → 404(记录不存在)
    - ValueError → 409(地址超限/状态非法)
"""

from typing import Optional

from core.locks import get_lock
from core.helpers import ts, bc_hash
from repositories.location_repository import (
    LocationRepository,
    haversine_km,
    # 门店类型
    STORE_TYPE_FLAGSHIP, STORE_TYPE_EXPERIENCE, STORE_TYPE_EXCLUSIVE,
    STORE_STATUS_OPEN, STORE_STATUS_CLOSED,
    # 代理商等级
    AGENT_LEVEL_DIAMOND, AGENT_LEVEL_GOLD, AGENT_LEVEL_SILVER,
    # 物流状态
    SHIPMENT_STATUS_IN_TRANSIT, SHIPMENT_STATUS_DELIVERING, SHIPMENT_STATUS_SIGNED,
    # 配送范围类型
    ZONE_TYPE_NATIONAL, ZONE_TYPE_CITY, ZONE_TYPE_SELF, ZONE_TYPE_REMOTE,
    # 地址标签
    LABEL_HOME, LABEL_COMPANY, LABEL_SCHOOL,
    # 存证类型
    EVIDENCE_TYPE_ADDRESS, EVIDENCE_TYPE_LOCATION, EVIDENCE_TYPE_LOGISTICS,
    EVIDENCE_TYPE_DELIVERY, EVIDENCE_TYPE_HEATMAP, EVIDENCE_TYPE_SITE,
    # 常量
    MAX_ADDRESSES_PER_USER,
)


class LocationService:
    """位置地图管理业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: LocationRepository = LocationRepository()):
        self.repo = repo

    # ============================================================
    # 1. 收货地址管理
    # ============================================================

    async def add_address(self, user_id: int, receiver_name: str,
                            receiver_phone: str, province: str, city: str,
                            district: str, detail_address: str,
                            longitude: float = None, latitude: float = None,
                            adcode: str = None, label: str = None,
                            is_default: bool = False) -> dict:
        """新增收货地址

        规则:
            - 每用户最多20条地址
            - 设为默认时清除其他默认地址
        """
        if not user_id or not receiver_name or not receiver_phone:
            raise ValueError("用户ID/收件人/手机号不可为空")
        if not province or not city or not detail_address:
            raise ValueError("省/市/详细地址不可为空")

        # 地址上限校验
        count = await self.repo.count_user_addresses(user_id)
        if count >= MAX_ADDRESSES_PER_USER:
            raise ValueError(f"地址数量已达上限({MAX_ADDRESSES_PER_USER}条)")

        lock_key = f"loc:address:default:{user_id}"
        async with get_lock(lock_key):
            # 设为默认时清除其他默认
            if is_default:
                await self.repo.clear_default_address(user_id)

            address = {
                "userId": user_id,
                "receiverName": receiver_name,
                "receiverPhone": receiver_phone,
                "province": province,
                "city": city,
                "district": district,
                "detailAddress": detail_address,
                "longitude": longitude,
                "latitude": latitude,
                "adcode": adcode,
                "label": label,
                "isDefault": is_default,
                "createdAt": ts(),
            }
            address_id = await self.repo.create_address(address)
            address["id"] = address_id

            # 写入区块链存证
            evidence_hash = bc_hash()
            evidence = {
                "evidenceType": EVIDENCE_TYPE_ADDRESS,
                "evidenceHash": evidence_hash,
                "evidenceData": json_dumps({"addressId": address_id, "userId": user_id}),
                "txId": bc_hash(),
            }
            evidence_id = await self.repo.create_evidence(evidence)

            return {
                "id": address_id,
                "userId": user_id,
                "receiverName": receiver_name,
                "isDefault": is_default,
                "evidenceId": evidence_id,
                "evidenceHash": evidence_hash,
            }

    async def update_address(self, address_id: int, user_id: int = None,
                               receiver_name: str = None, receiver_phone: str = None,
                               province: str = None, city: str = None,
                               district: str = None, detail_address: str = None,
                               longitude: float = None, latitude: float = None,
                               adcode: str = None, label: str = None) -> dict:
        """编辑地址"""
        address = await self.repo.get_address(address_id)
        if address is None:
            raise KeyError(f"地址不存在(id={address_id})")

        updates = {}
        if receiver_name is not None:
            updates["receiverName"] = receiver_name
        if receiver_phone is not None:
            updates["receiverPhone"] = receiver_phone
        if province is not None:
            updates["province"] = province
        if city is not None:
            updates["city"] = city
        if district is not None:
            updates["district"] = district
        if detail_address is not None:
            updates["detailAddress"] = detail_address
        if longitude is not None:
            updates["longitude"] = longitude
        if latitude is not None:
            updates["latitude"] = latitude
        if adcode is not None:
            updates["adcode"] = adcode
        if label is not None:
            updates["label"] = label

        if updates:
            await self.repo.update_address(address_id, updates)
            address.update(updates)
        return address

    async def delete_address(self, address_id: int) -> dict:
        """删除地址"""
        address = await self.repo.get_address(address_id)
        if address is None:
            raise KeyError(f"地址不存在(id={address_id})")
        await self.repo.delete_address(address_id)
        return {"id": address_id, "deleted": True}

    async def set_default_address(self, address_id: int, user_id: int) -> dict:
        """设为默认地址"""
        address = await self.repo.get_address(address_id)
        if address is None:
            raise KeyError(f"地址不存在(id={address_id})")

        if address.get("userId") != user_id:
            raise ValueError("地址不属于该用户")

        lock_key = f"loc:address:default:{user_id}"
        async with get_lock(lock_key):
            # 清除其他默认
            await self.repo.clear_default_address(user_id)
            # 设置当前为默认
            await self.repo.update_address(address_id, {"isDefault": True})
            address["isDefault"] = True
            return address

    async def list_addresses(self, user_id: int) -> list[dict]:
        """查询用户地址列表(默认地址排前)"""
        return await self.repo.list_addresses(user_id)

    async def get_address(self, address_id: int) -> dict:
        """查询地址详情"""
        address = await self.repo.get_address(address_id)
        if address is None:
            raise KeyError(f"地址不存在(id={address_id})")
        return address

    # ============================================================
    # 2. 附近门店
    # ============================================================

    async def add_store(self, store_name: str, store_type: str, province: str,
                          city: str, district: str, address: str,
                          longitude: float, latitude: float,
                          phone: str = None, open_hours: str = None,
                          services: str = None, status: str = STORE_STATUS_OPEN) -> dict:
        """新增门店(管理员)"""
        if not store_name or not store_type:
            raise ValueError("门店名称和类型不可为空")
        if longitude is None or latitude is None:
            raise ValueError("门店经纬度不可为空")

        store = {
            "storeName": store_name,
            "storeType": store_type,
            "province": province,
            "city": city,
            "district": district,
            "address": address,
            "longitude": longitude,
            "latitude": latitude,
            "phone": phone,
            "openHours": open_hours,
            "services": services,
            "status": status,
            "createdAt": ts(),
        }
        store_id = await self.repo.create_store(store)
        store["id"] = store_id
        return store

    async def get_store(self, store_id: int) -> dict:
        """门店详情"""
        store = await self.repo.get_store(store_id)
        if store is None:
            raise KeyError(f"门店不存在(id={store_id})")
        return store

    async def list_nearby_stores(self, longitude: float, latitude: float,
                                   radius_km: float = 5.0, limit: int = 50) -> list[dict]:
        """附近门店(按距离排序)"""
        return await self.repo.list_nearby_stores(longitude, latitude, radius_km, limit)

    async def list_stores(self, city: str = None, store_type: str = None,
                            status: str = None, limit: int = 50) -> list[dict]:
        """查询门店列表"""
        return await self.repo.list_stores(city, store_type, status, limit)

    # ============================================================
    # 3. 代理商查询
    # ============================================================

    async def add_agent_location(self, agent_id: int, agent_name: str,
                                    agent_level: str, province: str, city: str,
                                    address: str, longitude: float, latitude: float,
                                    contact_name: str = None,
                                    contact_phone: str = None) -> dict:
        """新增代理商位置(管理员)"""
        if not agent_id or not agent_name:
            raise ValueError("代理商ID和名称不可为空")
        if longitude is None or latitude is None:
            raise ValueError("代理商经纬度不可为空")

        agent = {
            "agentId": agent_id,
            "agentName": agent_name,
            "agentLevel": agent_level,
            "province": province,
            "city": city,
            "address": address,
            "longitude": longitude,
            "latitude": latitude,
            "contactName": contact_name,
            "contactPhone": contact_phone,
            "createdAt": ts(),
        }
        loc_id = await self.repo.create_agent_location(agent)
        agent["id"] = loc_id
        return agent

    async def get_agent_location(self, agent_id: int) -> dict:
        """代理商详情"""
        agent = await self.repo.get_agent_location(agent_id)
        if agent is None:
            raise KeyError(f"代理商位置不存在(id={agent_id})")
        return agent

    async def list_agent_locations(self, province: str = None, city: str = None,
                                       agent_level: str = None,
                                       limit: int = 50) -> list[dict]:
        """代理商列表"""
        return await self.repo.list_agent_locations(province, city, agent_level, limit)

    async def list_nearby_agents(self, longitude: float, latitude: float,
                                    radius_km: float = 50.0, limit: int = 50) -> list[dict]:
        """附近代理商"""
        return await self.repo.list_nearby_agents(longitude, latitude, radius_km, limit)

    # ============================================================
    # 4. 物流轨迹追踪
    # ============================================================

    async def create_shipment_track(self, shipment_id: str, order_id: int,
                                       carrier: str, tracking_no: str,
                                       origin_lng: float, origin_lat: float,
                                       dest_lng: float, dest_lat: float,
                                       current_lng: float = None,
                                       current_lat: float = None,
                                       current_address: str = None,
                                       eta: str = None) -> dict:
        """创建物流轨迹"""
        if not shipment_id or not order_id:
            raise ValueError("运单号和订单ID不可为空")

        track = {
            "shipmentId": shipment_id,
            "orderId": order_id,
            "carrier": carrier,
            "trackingNo": tracking_no,
            "currentLng": current_lng if current_lng is not None else origin_lng,
            "currentLat": current_lat if current_lat is not None else origin_lat,
            "currentAddress": current_address or "",
            "originLng": origin_lng,
            "originLat": origin_lat,
            "destLng": dest_lng,
            "destLat": dest_lat,
            "eta": eta,
            "status": SHIPMENT_STATUS_IN_TRANSIT,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        track_id = await self.repo.create_shipment_track(track)
        track["id"] = track_id

        # 写入区块链存证
        evidence_hash = bc_hash()
        evidence = {
            "evidenceType": EVIDENCE_TYPE_LOGISTICS,
            "evidenceHash": evidence_hash,
            "evidenceData": json_dumps({"trackId": track_id, "shipmentId": shipment_id}),
            "txId": bc_hash(),
        }
        evidence_id = await self.repo.create_evidence(evidence)

        return {
            "id": track_id,
            "shipmentId": shipment_id,
            "status": SHIPMENT_STATUS_IN_TRANSIT,
            "evidenceId": evidence_id,
            "evidenceHash": evidence_hash,
        }

    async def update_shipment_track(self, track_id: int,
                                       current_lng: float = None,
                                       current_lat: float = None,
                                       current_address: str = None,
                                       status: str = None,
                                       eta: str = None) -> dict:
        """更新物流轨迹"""
        lock_key = f"loc:track:{track_id}"
        async with get_lock(lock_key):
            track = await self.repo.get_shipment_track(track_id)
            if track is None:
                raise KeyError(f"物流轨迹不存在(id={track_id})")

            updates = {}
            if current_lng is not None:
                updates["currentLng"] = current_lng
            if current_lat is not None:
                updates["currentLat"] = current_lat
            if current_address is not None:
                updates["currentAddress"] = current_address
            if status is not None:
                updates["status"] = status
            if eta is not None:
                updates["eta"] = eta

            if updates:
                await self.repo.update_shipment_track(track_id, updates)
                track.update(updates)

            # 计算剩余距离
            if current_lng is not None and current_lat is not None:
                dest_lng = track.get("destLng")
                dest_lat = track.get("destLat")
                if dest_lng is not None and dest_lat is not None:
                    track["remainingDistance"] = round(
                        haversine_km(current_lng, current_lat, dest_lng, dest_lat), 2
                    )

            return track

    async def get_shipment_track(self, track_id: int) -> dict:
        """查询物流轨迹"""
        track = await self.repo.get_shipment_track(track_id)
        if track is None:
            raise KeyError(f"物流轨迹不存在(id={track_id})")
        return track

    async def get_track_by_shipment(self, shipment_id: str) -> dict:
        """按运单号查询轨迹"""
        track = await self.repo.get_track_by_shipment(shipment_id)
        if track is None:
            raise KeyError(f"运单不存在(shipmentId={shipment_id})")
        return track

    # ============================================================
    # 5. 配送范围检测
    # ============================================================

    async def add_delivery_zone(self, zone_name: str, zone_type: str,
                                  center_lng: float = None, center_lat: float = None,
                                  radius: float = 0, polygon: str = None,
                                  shipping_fee: float = 0, free_threshold: float = 0,
                                  delivery_time: str = None) -> dict:
        """新增配送范围(管理员)"""
        if not zone_name or not zone_type:
            raise ValueError("范围名称和类型不可为空")

        zone = {
            "zoneName": zone_name,
            "zoneType": zone_type,
            "centerLng": center_lng,
            "centerLat": center_lat,
            "radius": radius,
            "polygon": polygon,
            "shippingFee": shipping_fee,
            "freeThreshold": free_threshold,
            "deliveryTime": delivery_time,
            "status": "active",
            "createdAt": ts(),
        }
        zone_id = await self.repo.create_delivery_zone(zone)
        zone["id"] = zone_id
        return zone

    async def check_delivery_point(self, longitude: float, latitude: float) -> dict:
        """检测坐标所在配送范围"""
        matched = await self.repo.check_delivery_point(longitude, latitude)
        return {
            "longitude": longitude,
            "latitude": latitude,
            "matchedZones": matched,
            "inDeliveryRange": len(matched) > 0,
        }

    async def list_delivery_zones(self, zone_type: str = None,
                                     limit: int = 50) -> list[dict]:
        """查询配送范围列表"""
        return await self.repo.list_delivery_zones(zone_type, limit)

    # ============================================================
    # 6. 区块链存证
    # ============================================================

    async def add_evidence(self, evidence_type: str, evidence_data: str = "") -> dict:
        """新增区块链存证"""
        valid_types = (EVIDENCE_TYPE_ADDRESS, EVIDENCE_TYPE_LOCATION,
                        EVIDENCE_TYPE_LOGISTICS, EVIDENCE_TYPE_DELIVERY,
                        EVIDENCE_TYPE_HEATMAP, EVIDENCE_TYPE_SITE)
        if evidence_type not in valid_types:
            raise ValueError(f"非法存证类型: {evidence_type}")

        evidence_hash = bc_hash()
        tx_id = bc_hash()
        evidence = {
            "evidenceType": evidence_type,
            "evidenceData": evidence_data,
            "evidenceHash": evidence_hash,
            "txId": tx_id,
            "blockHeight": 0,
            "createdAt": ts(),
        }
        evidence_id = await self.repo.create_evidence(evidence)
        evidence["id"] = evidence_id
        return evidence

    async def verify_evidence_by_hash(self, evidence_hash: str) -> dict:
        """按哈希验证存证"""
        evidence = await self.repo.get_evidence_by_hash(evidence_hash)
        if evidence is None:
            raise KeyError(f"存证哈希不存在(hash={evidence_hash})")
        return {
            "verified": True,
            "evidenceId": evidence.get("id"),
            "evidenceHash": evidence_hash,
            "evidenceType": evidence.get("evidenceType"),
            "txId": evidence.get("txId"),
        }

    async def get_evidence(self, evidence_id: int) -> dict:
        """查询存证详情"""
        evidence = await self.repo.get_evidence(evidence_id)
        if evidence is None:
            raise KeyError(f"区块链存证不存在(id={evidence_id})")
        return evidence

    async def list_evidence(self, evidence_type: str = None,
                               limit: int = 50) -> list[dict]:
        """查询存证列表"""
        return await self.repo.list_evidence(evidence_type, limit)

    # ============================================================
    # 7. 距离计算
    # ============================================================

    async def calculate_distance(self, lng1: float, lat1: float,
                                    lng2: float, lat2: float) -> dict:
        """计算两点间距离"""
        distance = haversine_km(lng1, lat1, lng2, lat2)
        return {
            "origin": {"longitude": lng1, "latitude": lat1},
            "destination": {"longitude": lng2, "latitude": lat2},
            "distance": round(distance, 2),
            "unit": "km",
        }

    # ============================================================
    # 8. 统计
    # ============================================================

    async def get_stats(self) -> dict:
        """位置地图统计"""
        stores = await self.repo.list_stores(limit=10000)
        agents = await self.repo.list_agent_locations(limit=10000)
        tracks = await self.repo.list_shipment_tracks(limit=10000)
        zones = await self.repo.list_delivery_zones(limit=10000)
        evidence = await self.repo.list_evidence(limit=10000)

        # 门店类型分布
        store_type_count = {}
        for s in stores:
            t = s.get("storeType", "unknown")
            store_type_count[t] = store_type_count.get(t, 0) + 1

        # 代理商等级分布
        agent_level_count = {}
        for a in agents:
            l = a.get("agentLevel", "unknown")
            agent_level_count[l] = agent_level_count.get(l, 0) + 1

        # 物流状态分布
        track_status_count = {}
        for t in tracks:
            s = t.get("status", "unknown")
            track_status_count[s] = track_status_count.get(s, 0) + 1

        return {
            "totalStores": len(stores),
            "totalAgentLocations": len(agents),
            "totalShipmentTracks": len(tracks),
            "totalDeliveryZones": len(zones),
            "totalEvidence": len(evidence),
            "storeTypeCount": store_type_count,
            "agentLevelCount": agent_level_count,
            "trackStatusCount": track_status_count,
        }


# ============================================================
# 辅助函数
# ============================================================

def json_dumps(data) -> str:
    """JSON序列化(中文字符不转义)"""
    import json
    return json.dumps(data, ensure_ascii=False)
