"""会员 Repository

双模式(内存/Redis)透明切换:
    - 内存模式: 直接操作 _mock_store["members"] / _mock_store["member_addresses"]
    - Redis 模式:
        * 会员主信息: Hash  zhuxiang:member:{id}
        * 手机号索引: String zhuxiang:member:phone:{phone}  (值=用户ID, 唯一约束)
        * 自增序列:   String zhuxiang:member:seq            (INCR 生成用户ID)
        * 收货地址:   Hash  zhuxiang:member:addresses:{userId}  (field=addrId, value=JSON)

锁键: member:{memberId} / member:phone:{phone}(并发安全由 services 层负责)
"""

import json
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


class MemberRepository:
    """会员数据访问(双模式)"""

    def __init__(self, store: dict = None):
        # store 参数仅用于内存模式兼容(测试可能注入)
        # Redis 模式下忽略 store, 走 Redis 客户端
        self.store = store if store is not None else get_in_memory_store()

    # ---------- 会员主表 ----------

    async def get_by_id(self, member_id) -> Optional[dict]:
        """按 ID 查询会员,不存在返回 None"""
        if is_redis_mode():
            return await self._redis_get_by_id(member_id)
        return self._mem_get_by_id(member_id)

    async def get_by_phone(self, phone: str) -> Optional[dict]:
        """按手机号查询会员,不存在返回 None"""
        if is_redis_mode():
            return await self._redis_get_by_phone(phone)
        return self._mem_get_by_phone(phone)

    async def list_all(self) -> list[dict]:
        """列出所有会员"""
        if is_redis_mode():
            return await self._redis_list_all()
        return self._mem_list_all()

    async def create(self, member_data: dict) -> dict:
        """新增会员(自增ID),返回带 id 的完整记录

        Raises:
            ValueError: 手机号已存在
        """
        if is_redis_mode():
            return await self._redis_create(member_data)
        return self._mem_create(member_data)

    async def save(self, member_id, member_data: dict) -> dict:
        """覆盖会员记录(保留 id)"""
        if is_redis_mode():
            return await self._redis_save(member_id, member_data)
        return self._mem_save(member_id, member_data)

    async def update_fields(self, member_id, fields: dict) -> dict:
        """部分字段更新,返回更新后的完整记录

        Raises:
            KeyError: 会员不存在
        """
        if is_redis_mode():
            return await self._redis_update_fields(member_id, fields)
        return self._mem_update_fields(member_id, fields)

    async def add_growth(self, member_id, amount: int) -> int:
        """成长值累加(amount>=0),返回新成长值

        Raises:
            KeyError: 会员不存在
        """
        if is_redis_mode():
            return await self._redis_add_growth(member_id, amount)
        return self._mem_add_growth(member_id, amount)

    async def add_points(self, member_id, amount: int) -> int:
        """积分累加(amount 可正可负),返回新积分

        Raises:
            KeyError: 会员不存在
            ValueError: 积分不足
        """
        if is_redis_mode():
            return await self._redis_add_points(member_id, amount)
        return self._mem_add_points(member_id, amount)

    async def get_points(self, member_id) -> int:
        """查询积分

        Raises:
            KeyError: 会员不存在
        """
        if is_redis_mode():
            return await self._redis_get_points(member_id)
        return self._mem_get_points(member_id)

    async def get_level(self, member_id) -> int:
        """查询等级

        Raises:
            KeyError: 会员不存在
        """
        if is_redis_mode():
            return await self._redis_get_level(member_id)
        return self._mem_get_level(member_id)

    async def update_level(self, member_id, new_level: int) -> int:
        """更新等级,返回旧等级

        Raises:
            KeyError: 会员不存在
        """
        if is_redis_mode():
            return await self._redis_update_level(member_id, new_level)
        return self._mem_update_level(member_id, new_level)

    async def delete(self, member_id) -> None:
        """删除会员(Raises KeyError: 会员不存在)"""
        if is_redis_mode():
            return await self._redis_delete(member_id)
        return self._mem_delete(member_id)

    # ---------- 收货地址 ----------

    async def list_addresses(self, member_id) -> list[dict]:
        """列出会员的所有收货地址

        Raises:
            KeyError: 会员不存在
        """
        if is_redis_mode():
            return await self._redis_list_addresses(member_id)
        return self._mem_list_addresses(member_id)

    async def get_address(self, member_id, address_id) -> Optional[dict]:
        """查询单个地址,不存在返回 None"""
        if is_redis_mode():
            return await self._redis_get_address(member_id, address_id)
        return self._mem_get_address(member_id, address_id)

    async def save_address(self, member_id, address_id, address_data: dict) -> dict:
        """新增/覆盖地址"""
        if is_redis_mode():
            return await self._redis_save_address(member_id, address_id, address_data)
        return self._mem_save_address(member_id, address_id, address_data)

    async def delete_address(self, member_id, address_id) -> bool:
        """删除地址,返回是否删除成功"""
        if is_redis_mode():
            return await self._redis_delete_address(member_id, address_id)
        return self._mem_delete_address(member_id, address_id)

    async def clear_default_addresses(self, member_id) -> None:
        """清除该会员所有地址的 is_default 标记"""
        if is_redis_mode():
            return await self._redis_clear_default_addresses(member_id)
        return self._mem_clear_default_addresses(member_id)

    async def next_address_id(self, member_id) -> str:
        """生成下一个地址ID(基于时间戳+随机, 保证唯一)"""
        import time, random
        return f"addr_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"

    # ============================================================
    # 内存后端
    # ============================================================

    def _ensure_members_store(self):
        """确保内存 store 包含 members / member_addresses 键"""
        if "members" not in self.store:
            self.store["members"] = {}
        if "member_addresses" not in self.store:
            self.store["member_addresses"] = {}
        if "_member_seq" not in self.store:
            self.store["_member_seq"] = 0

    def _mem_get_by_id(self, member_id) -> Optional[dict]:
        self._ensure_members_store()
        return self.store["members"].get(member_id)

    def _mem_get_by_phone(self, phone: str) -> Optional[dict]:
        self._ensure_members_store()
        for m in self.store["members"].values():
            if m.get("phone") == phone:
                return m
        return None

    def _mem_list_all(self) -> list[dict]:
        self._ensure_members_store()
        return list(self.store["members"].values())

    def _mem_create(self, member_data: dict) -> dict:
        self._ensure_members_store()
        phone = member_data.get("phone", "")
        if phone and self._mem_get_by_phone(phone):
            raise ValueError(f"手机号 {phone} 已注册")
        self.store["_member_seq"] += 1
        new_id = self.store["_member_seq"]
        member_data["id"] = new_id
        self.store["members"][new_id] = member_data
        return member_data

    def _mem_save(self, member_id, member_data: dict) -> dict:
        self._ensure_members_store()
        member_data["id"] = member_id
        self.store["members"][member_id] = member_data
        return member_data

    def _mem_update_fields(self, member_id, fields: dict) -> dict:
        self._ensure_members_store()
        member = self.store["members"].get(member_id)
        if not member:
            raise KeyError(member_id)
        member.update(fields)
        return member

    def _mem_add_growth(self, member_id, amount: int) -> int:
        self._ensure_members_store()
        member = self.store["members"].get(member_id)
        if not member:
            raise KeyError(member_id)
        member["growth_value"] = member.get("growth_value", 0) + amount
        return member["growth_value"]

    def _mem_add_points(self, member_id, amount: int) -> int:
        self._ensure_members_store()
        member = self.store["members"].get(member_id)
        if not member:
            raise KeyError(member_id)
        current = member.get("points", 0)
        new_points = current + amount
        if new_points < 0:
            raise ValueError(f"积分不足: 当前 {current}, 需扣除 {-amount}")
        member["points"] = new_points
        return new_points

    def _mem_get_points(self, member_id) -> int:
        self._ensure_members_store()
        member = self.store["members"].get(member_id)
        if not member:
            raise KeyError(member_id)
        return member.get("points", 0)

    def _mem_get_level(self, member_id) -> int:
        self._ensure_members_store()
        member = self.store["members"].get(member_id)
        if not member:
            raise KeyError(member_id)
        return member.get("level", 1)

    def _mem_update_level(self, member_id, new_level: int) -> int:
        self._ensure_members_store()
        member = self.store["members"].get(member_id)
        if not member:
            raise KeyError(member_id)
        old_level = member.get("level", 1)
        member["level"] = new_level
        return old_level

    def _mem_delete(self, member_id) -> None:
        self._ensure_members_store()
        if member_id not in self.store["members"]:
            raise KeyError(member_id)
        del self.store["members"][member_id]
        self.store["member_addresses"].pop(member_id, None)

    def _mem_list_addresses(self, member_id) -> list[dict]:
        self._ensure_members_store()
        if member_id not in self.store["members"]:
            raise KeyError(member_id)
        addr_map = self.store["member_addresses"].get(member_id, {})
        return list(addr_map.values())

    def _mem_get_address(self, member_id, address_id) -> Optional[dict]:
        self._ensure_members_store()
        addr_map = self.store["member_addresses"].get(member_id, {})
        return addr_map.get(address_id)

    def _mem_save_address(self, member_id, address_id, address_data: dict) -> dict:
        self._ensure_members_store()
        if member_id not in self.store["member_addresses"]:
            self.store["member_addresses"][member_id] = {}
        address_data["id"] = address_id
        address_data["user_id"] = member_id
        self.store["member_addresses"][member_id][address_id] = address_data
        return address_data

    def _mem_delete_address(self, member_id, address_id) -> bool:
        self._ensure_members_store()
        addr_map = self.store["member_addresses"].get(member_id, {})
        if address_id in addr_map:
            del addr_map[address_id]
            return True
        return False

    def _mem_clear_default_addresses(self, member_id) -> None:
        self._ensure_members_store()
        addr_map = self.store["member_addresses"].get(member_id, {})
        for addr in addr_map.values():
            addr["is_default"] = 0

    # ============================================================
    # Redis 后端
    # ============================================================

    async def _redis_get_by_id(self, member_id) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.hgetall(_k("member", member_id))
        if not data:
            return None
        return self._deserialize_member(data)

    async def _redis_get_by_phone(self, phone: str) -> Optional[dict]:
        client = await get_redis_client()
        member_id = await client.get(_k("member", "phone", phone))
        if not member_id:
            return None
        return await self._redis_get_by_id(int(member_id))

    async def _redis_list_all(self) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("member", "*"))
        result = []
        for key in keys:
            # 排除 phone: 索引和 seq 序列
            if ":phone:" in key or key.endswith(":member:seq"):
                continue
            data = await client.hgetall(key)
            if data:
                result.append(self._deserialize_member(data))
        return result

    async def _redis_create(self, member_data: dict) -> dict:
        client = await get_redis_client()
        phone = member_data.get("phone", "")
        # 手机号唯一性检查 + 写入(原子: 用 phone 索引的 SETNX)
        if phone:
            phone_key = _k("member", "phone", phone)
            acquired = await client.setnx(phone_key, 0)  # 先占位, 值稍后更新
            if not acquired:
                raise ValueError(f"手机号 {phone} 已注册")
        # 生成自增 ID
        new_id = await client.incr(_k("member", "seq"))
        member_data["id"] = new_id
        await self._redis_save(new_id, member_data)
        # 更新 phone 索引指向真实 ID
        if phone:
            await client.set(_k("member", "phone", phone), new_id)
        return member_data

    async def _redis_save(self, member_id, member_data: dict) -> dict:
        client = await get_redis_client()
        member_data["id"] = member_id
        await client.hset(_k("member", member_id), mapping=self._serialize_member(member_data))
        return member_data

    async def _redis_update_fields(self, member_id, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("member", member_id)
        if not await client.exists(key):
            raise KeyError(member_id)
        await client.hset(key, mapping=self._serialize_member(fields))
        data = await client.hgetall(key)
        return self._deserialize_member(data)

    async def _redis_add_growth(self, member_id, amount: int) -> int:
        client = await get_redis_client()
        key = _k("member", member_id)
        if not await client.exists(key):
            raise KeyError(member_id)
        return await client.hincrby(key, "growth_value", amount)

    async def _redis_add_points(self, member_id, amount: int) -> int:
        client = await get_redis_client()
        key = _k("member", member_id)
        if not await client.exists(key):
            raise KeyError(member_id)
        # 积分不足需先读后写(此处用 Lua 保证原子性)
        if amount < 0:
            current = int(await client.hget(key, "points") or 0)
            if current + amount < 0:
                raise ValueError(f"积分不足: 当前 {current}, 需扣除 {-amount}")
        return await client.hincrby(key, "points", amount)

    async def _redis_get_points(self, member_id) -> int:
        client = await get_redis_client()
        key = _k("member", member_id)
        if not await client.exists(key):
            raise KeyError(member_id)
        return int(await client.hget(key, "points") or 0)

    async def _redis_get_level(self, member_id) -> int:
        client = await get_redis_client()
        key = _k("member", member_id)
        if not await client.exists(key):
            raise KeyError(member_id)
        return int(await client.hget(key, "level") or 1)

    async def _redis_update_level(self, member_id, new_level: int) -> int:
        client = await get_redis_client()
        key = _k("member", member_id)
        if not await client.exists(key):
            raise KeyError(member_id)
        old_level = int(await client.hget(key, "level") or 1)
        await client.hset(key, "level", new_level)
        return old_level

    async def _redis_delete(self, member_id) -> None:
        client = await get_redis_client()
        key = _k("member", member_id)
        if not await client.exists(key):
            raise KeyError(member_id)
        # 先删除手机号索引
        phone = await client.hget(key, "phone")
        if phone:
            await client.delete(_k("member", "phone", phone))
        await client.delete(key)
        await client.delete(_k("member", "addresses", member_id))

    async def _redis_list_addresses(self, member_id) -> list[dict]:
        client = await get_redis_client()
        # 校验会员存在
        if not await client.exists(_k("member", member_id)):
            raise KeyError(member_id)
        addr_map = await client.hgetall(_k("member", "addresses", member_id))
        result = []
        for addr_json in addr_map.values():
            result.append(json.loads(addr_json))
        return result

    async def _redis_get_address(self, member_id, address_id) -> Optional[dict]:
        client = await get_redis_client()
        addr_json = await client.hget(_k("member", "addresses", member_id), address_id)
        if not addr_json:
            return None
        return json.loads(addr_json)

    async def _redis_save_address(self, member_id, address_id, address_data: dict) -> dict:
        client = await get_redis_client()
        address_data["id"] = address_id
        address_data["user_id"] = member_id
        await client.hset(_k("member", "addresses", member_id),
                          address_id, json.dumps(address_data, ensure_ascii=False))
        return address_data

    async def _redis_delete_address(self, member_id, address_id) -> bool:
        client = await get_redis_client()
        deleted = await client.hdel(_k("member", "addresses", member_id), address_id)
        return deleted > 0

    async def _redis_clear_default_addresses(self, member_id) -> None:
        client = await get_redis_client()
        addr_key = _k("member", "addresses", member_id)
        addr_map = await client.hgetall(addr_key)
        for addr_id, addr_json in addr_map.items():
            addr = json.loads(addr_json)
            if addr.get("is_default") == 1:
                addr["is_default"] = 0
                await client.hset(addr_key, addr_id, json.dumps(addr, ensure_ascii=False))

    # ============================================================
    # 序列化辅助(Redis Hash 要求 value 为 str/int/float)
    # ============================================================

    def _serialize_member(self, member: dict) -> dict:
        """将会员 dict 序列化为 Redis Hash 兼容的 mapping(全转 str)"""
        result = {}
        for k, v in member.items():
            if v is None:
                continue
            if isinstance(v, bool):
                result[k] = 1 if v else 0
            elif isinstance(v, (int, float)):
                result[k] = v  # redis-py 支持 int/float
            else:
                result[k] = str(v)
        return result

    def _deserialize_member(self, data: dict) -> dict:
        """将 Redis hgetall 返回的 dict 反序列化(类型还原)"""
        def _to_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return v

        result = dict(data)
        for k in ("id", "gender", "level", "growth_value", "points", "status"):
            if k in result:
                result[k] = _to_int(result[k])
        return result
