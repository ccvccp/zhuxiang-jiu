"""超级会员智能库 仓储(mtier_repository)

三表(前缀 zhuxiang:mtier:, 镜像智客 _ZkStore 范式):
    mtier_profiles   会员卡制画像(id=memberId: tier/lifecycle/tags)
    mtier_logs       生命周期迁移留痕(自增 id)
    mtier_params     模块参数(单记录)

设计铁律(摸底结论):
    - 会员域只读消费: 累计消费口径=member.growth_value,
      订单明细经 zk_fabric_service 只读聚合——本模块永不回写
      member/订单/智客表
    - tier 是营销卡制叠加层(银卡/金卡/黑金), 与 L1-L5 成长值
      等级并存, 数据派生自等级引擎官方口径, 不参与其升降级考核
    - 100 万容量设计: Redis 键即索引(memberId 直寻), 无全表扫描
      热路径; tags 为 JSON dict 整体存取
"""

import json

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)

TABLE_PROFILES = "mtier_profiles"
TABLE_LOGS = "mtier_logs"
TABLE_PARAMS = "mtier_params"

_ALL_TABLES = (TABLE_PROFILES, TABLE_LOGS, TABLE_PARAMS)


def _now_ts() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()


class MtierRepository:
    """超级会员智能库仓储(双模式)"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    def _ensure_store(self) -> None:
        for table in _ALL_TABLES:
            self.store.setdefault(table, {})

    # ---------- 通用基元 ----------

    async def save(self, table: str, record_id, record: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("mtier", table, record_id),
                             json.dumps(record, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store[table][record_id] = record

    async def get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("mtier", table, record_id))
            return json.loads(raw) if raw else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def delete(self, table: str, record_id) -> bool:
        if is_redis_mode():
            client = await get_redis_client()
            return bool(await client.delete(
                _k("mtier", table, record_id)))
        self._ensure_store()
        return self.store[table].pop(record_id, None) is not None

    async def list_table(self, table: str) -> list[dict]:
        if is_redis_mode():
            # keys() 单命令往返(对齐 hook86/cooperation 主流模式);
            # scan_iter 全键空间多轮遍历在大 keyspace 下会 socket 超时
            client = await get_redis_client()
            keys = await client.keys(_k("mtier", table, "*"))
            rows = []
            for key in keys:
                skey = (key.decode() if isinstance(key, bytes)
                        else str(key))
                if ":seq:" in skey:
                    continue
                raw = await client.get(skey)
                if raw:
                    try:
                        rows.append(json.loads(raw))
                    except (ValueError, TypeError):
                        continue
            return rows
        self._ensure_store()
        return list(self.store[table].values())

    async def next_log_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("mtier", "seq", "log"))
        self._ensure_store()
        self.store["_mtier_log_seq"] = (
            self.store.get("_mtier_log_seq", 0) + 1)
        return self.store["_mtier_log_seq"]

    # ---------- 画像 ----------

    async def save_profile(self, profile: dict) -> dict:
        return await self.save(TABLE_PROFILES,
                               profile["memberId"], profile)

    async def get_profile(self, member_id) -> dict | None:
        return await self.get(TABLE_PROFILES, member_id)

    async def list_profiles(self) -> list[dict]:
        return await self.list_table(TABLE_PROFILES)

    async def add_log(self, rec: dict) -> dict:
        """生命周期迁移留痕(环形 10000 条内存约束在 service 层)"""
        rec["logId"] = await self.next_log_id()
        await self.save(TABLE_LOGS, rec["logId"], rec)
        return rec

    async def list_logs(self, limit: int = 200) -> list[dict]:
        rows = await self.list_table(TABLE_LOGS)
        rows.sort(key=lambda r: r.get("at", ""), reverse=True)
        return rows[:limit]
