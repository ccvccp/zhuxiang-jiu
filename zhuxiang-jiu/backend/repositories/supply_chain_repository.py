"""供应链四件套扩展数据域 Repository(P4.4)

双模式(内存/Redis)透明切换, 覆盖 store.py 中新增的供应链扩展域:
    - List 域: inventory_logs / stock_alerts / checkout_orders /
      profit_records / service_fees / inbound_orders / outbound_orders /
      stock_movements / stocktaking_records / loss_records /
      transfer_orders / cross_dock_records / environment_monitoring
    - Hash 域: checkout_coupons / points_accounts /
      shipping_claim_details(键: region)
    - 全量读写域: supply_warehouses / warehouse_locations / warehouse_stock
      (记录含主键, 需按字段查找与整体回写)

内存模式直接操作 _mock_store; Redis 模式:
    - List 域 → zhuxiang:sc:{domain} (List, RPUSH JSON)
    - Hash 域 → zhuxiang:sc:{domain} (Hash, field → JSON/标量)
    - 全量域   → zhuxiang:sc:{domain} (List of JSON, 读改写整体回写)

事务语义由 services 层负责(先校验后执行 + 攒批提交 + 逆序补偿),
本仓库只提供原子读写原语。
"""

import json

from repositories.backend import (
    get_in_memory_store, get_redis_client, is_redis_mode, _k,
)

# List 型域(追加/全读)
LIST_DOMAINS = (
    "inventory_logs", "stock_alerts", "checkout_orders",
    "profit_records", "service_fees", "inbound_orders",
    "outbound_orders", "stock_movements", "stocktaking_records",
    "loss_records", "transfer_orders", "cross_dock_records",
    "environment_monitoring",
)

# Hash 型域(键值)
# 注意: checkout_points 不能叫 points_accounts(与积分模块惰性初始化探针键冲突)
HASH_DOMAINS = (
    "checkout_coupons", "checkout_points", "shipping_claim_details",
)

# 全量读写域(记录列表, 按字段查找)
FULL_DOMAINS = ("supply_warehouses", "warehouse_locations", "warehouse_stock")


class SupplyChainRepository:
    """供应链扩展域数据访问(双模式)"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ---------- List 域 ----------

    async def append(self, domain: str, record: dict) -> dict:
        """List 域追加一条记录"""
        if domain not in LIST_DOMAINS:
            raise ValueError(f"非法 List 域({domain})")
        if is_redis_mode():
            client = await get_redis_client()
            await client.rpush(_k("sc", domain), json.dumps(record, ensure_ascii=False))
        else:
            self.store.setdefault(domain, []).append(record)
        return record

    async def list_all(self, domain: str) -> list[dict]:
        """List 域全量读取"""
        if domain not in LIST_DOMAINS:
            raise ValueError(f"非法 List 域({domain})")
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.lrange(_k("sc", domain), 0, -1)
            return [json.loads(r) for r in raw]
        return list(self.store.get(domain) or [])

    async def remove_last(self, domain: str) -> dict | None:
        """List 域弹出末尾记录(事务补偿用)"""
        if domain not in LIST_DOMAINS:
            raise ValueError(f"非法 List 域({domain})")
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.rpop(_k("sc", domain))
            return json.loads(raw) if raw else None
        records = self.store.get(domain) or []
        return records.pop() if records else None

    # ---------- Hash 域 ----------

    async def hget(self, domain: str, key: str) -> dict | None:
        """Hash 域取值(JSON 解析)"""
        if domain not in HASH_DOMAINS:
            raise ValueError(f"非法 Hash 域({domain})")
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hget(_k("sc", domain), key)
            if raw is None:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return raw   # 标量(积分余额)
        return (self.store.get(domain) or {}).get(key)

    async def hset(self, domain: str, key: str, value) -> None:
        """Hash 域写值(dict/标量统一 JSON 编码)"""
        if domain not in HASH_DOMAINS:
            raise ValueError(f"非法 Hash 域({domain})")
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("sc", domain), key,
                              json.dumps(value, ensure_ascii=False))
        else:
            self.store.setdefault(domain, {})[key] = value

    async def hdel(self, domain: str, key: str) -> None:
        """Hash 域删键"""
        if domain not in HASH_DOMAINS:
            raise ValueError(f"非法 Hash 域({domain})")
        if is_redis_mode():
            client = await get_redis_client()
            await client.hdel(_k("sc", domain), key)
        else:
            (self.store.get(domain) or {}).pop(key, None)

    async def hgetall(self, domain: str) -> dict:
        """Hash 域全量读取(P5.1: claims 富记录列表用)"""
        if domain not in HASH_DOMAINS:
            raise ValueError(f"非法 Hash 域({domain})")
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hgetall(_k("sc", domain))
            out = {}
            for key, value in raw.items():
                try:
                    out[key] = json.loads(value)
                except (TypeError, ValueError):
                    out[key] = value   # 标量
            return out
        return dict(self.store.get(domain) or {})

    async def hget_int(self, domain: str, key: str, default: int = 0) -> int:
        """Hash 域取整数值(积分余额等, 缺失返回 default)"""
        v = await self.hget(domain, key)
        if v is None:
            return default
        return int(v) if not isinstance(v, dict) else default

    # ---------- 全量读写域(仓储主数据) ----------

    async def load(self, domain: str) -> list[dict]:
        """全量域读取(返回内存引用或反序列化副本)"""
        if domain not in FULL_DOMAINS:
            raise ValueError(f"非法全量域({domain})")
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.lrange(_k("sc", domain), 0, -1)
            if raw:
                return [json.loads(r) for r in raw]
            return []
        return self.store.get(domain) or []

    async def save(self, domain: str, records: list[dict]) -> None:
        """全量域整体回写(内存: 原地更新; Redis: 删后重灌)"""
        if domain not in FULL_DOMAINS:
            raise ValueError(f"非法全量域({domain})")
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("sc", domain)
            await client.delete(key)
            if records:
                pipe = client.pipeline()
                for r in records:
                    pipe.rpush(key, json.dumps(r, ensure_ascii=False))
                await pipe.execute()
        else:
            self.store[domain] = records
