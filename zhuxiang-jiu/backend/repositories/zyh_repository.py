"""竹韵·智衡·竹奕酒智能大模型(75号)——仓储层

设计依据: 《"竹韵·智衡"竹奕酒及产业韧性生态系统 SDD V3.0》
(全竹竹材发酵蒸馏工艺宪法 + DTDAE 范式)工程化裁剪落地。

存储(Redis 前缀 zhuxiang:zyh:*, 索引制):
    knowledge:{kid}      知识条目 hash(工艺宪法/香型/专利/企标/
                          竹筒酒对立/发酵机理等, 单一事实源)
    knowledge:ids        条目 ID 索引 set
    graph:nodes           图谱节点 hash( nodeId -> JSON )
    graph:edges           图谱关系 list( [src, rel, dst, meta] )
    cache:{qhash}         语义缓存(归一化 query -> 应答, TTL 7d)
    qa:{seq}             问答留痕(守门拦截分布/缓存命中/反馈)
    qa:seq                留痕自增
    qa:index              留痕 ID 索引 list(截断防膨胀)
    stats:guard           守门分层计数 hash( L1/L2/L3 分类命中 )
    stats:cache           缓存计数 hash( hit/miss )

图谱 Schema(对齐 SDD V3.0 §4.1):
    节点: Product(竹奕酒/竹筒酒) Process(全竹发酵蒸馏/竹腔浸泡)
          AromaType(竹香) Standard(ZZ26SW1489404B)
          Patent(ZZ26SW1489303A) RawMaterial(全竹竹材)
    关系: 竹奕酒-USES_PROCESS->全竹发酵蒸馏
          全竹发酵蒸馏-PROTECTED_BY->ZZ26SW1489303A
          竹奕酒-HAS_AROMA_TYPE->竹香
          竹香-DEFINED_BY->ZZ26SW1489404B
          竹筒酒-USES_PROCESS->竹腔浸泡(他企工艺标记)
          竹筒酒-IS_DIFFERENT_FROM->竹奕酒(工艺对立标记)
          竹筒酒-HAS_NO_AROMA_TYPE->无(显式无香型)
"""

import json
import logging

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store,
)

logger = logging.getLogger(__name__)

PREFIX = "zhuxiang:zyh:"

# TTL(秒): 语义缓存 7 天(SDD V3.0 语义缓存层规范)
CACHE_TTL = 7 * 24 * 3600
# 留痕截断(防无限膨胀)
QA_INDEX_MAX = 500


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class ZyhRepository:
    """竹韵·智衡知识内核 + 图谱 + 语义缓存 + 留痕"""

    def __init__(self):
        self._mem = get_in_memory_store()

    # ---------- 基础键(内存模式扁平化) ----------

    def _k(self, key: str) -> str:
        return f"{PREFIX}{key}"

    # ============================================================
    # 知识条目(单一事实源)
    # ============================================================

    async def seed_knowledge(self, entries: list[dict]) -> int:
        """播种知识条目(幂等: 已有 id 跳过)

        条目结构:
            {id, title, content, keywords: [..],
             citations: [{type, id, clause?}]}
        """
        seeded = 0
        for e in entries:
            if await self.get_knowledge(e["id"]) is None:
                await self.upsert_knowledge(e)
                seeded += 1
        return seeded

    async def upsert_knowledge(self, entry: dict) -> None:
        data = _dump(entry)
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(self._k("knowledge"), entry["id"], data)
            await client.sadd(self._k("knowledge:ids"), entry["id"])
        else:
            k = self._k(f"knowledge:{entry['id']}")
            self._mem[k] = entry
            ids = self._mem.setdefault(
                self._k("knowledge:ids"), set())
            ids.add(entry["id"])

    async def get_knowledge(self, kid: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hget(self._k("knowledge"), kid)
            return json.loads(raw) if raw else None
        return self._mem.get(self._k(f"knowledge:{kid}"))

    async def list_knowledge(self) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            ids = sorted(await client.smembers(
                self._k("knowledge:ids")))
            out = []
            for kid in ids:
                raw = await client.hget(
                    self._k("knowledge"), kid)
                if raw:
                    out.append(json.loads(raw))
            return out
        ids = sorted(self._mem.get(
            self._k("knowledge:ids"), set()))
        return [self._mem[self._k(f"knowledge:{i}")]
                for i in ids
                if self._k(f"knowledge:{i}") in self._mem]

    # ============================================================
    # 工艺图谱(节点 + 关系)
    # ============================================================

    async def seed_graph(self, nodes: list[dict],
                         edges: list[list]) -> int:
        """播种图谱(幂等)"""
        count = 0
        if is_redis_mode():
            client = await get_redis_client()
            for n in nodes:
                if not await client.hexists(
                        self._k("graph:nodes"), n["id"]):
                    await client.hset(
                        self._k("graph:nodes"),
                        n["id"], _dump(n))
                    count += 1
            existing = await client.llen(self._k("graph:edges"))
            if existing == 0:
                for e in edges:
                    await client.rpush(self._k("graph:edges"),
                                        _dump(e))
                    count += 1
        else:
            nodes_k = self._k("graph:nodes")
            if nodes_k not in self._mem:
                self._mem[nodes_k] = {}
            for n in nodes:
                if n["id"] not in self._mem[nodes_k]:
                    self._mem[nodes_k][n["id"]] = n
                    count += 1
            edges_k = self._k("graph:edges")
            if edges_k not in self._mem:
                self._mem[edges_k] = []
                for e in edges:
                    self._mem[edges_k].append(e)
                    count += 1
        return count

    async def get_graph(self) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            nodes_raw = await client.hgetall(
                self._k("graph:nodes"))
            nodes = [json.loads(v)
                     for v in nodes_raw.values()]
            edges_raw = await client.lrange(
                self._k("graph:edges"), 0, -1)
            edges = [json.loads(v) for v in edges_raw]
        else:
            nodes = list(self._mem.get(
                self._k("graph:nodes"), {}).values())
            edges = list(self._mem.get(
                self._k("graph:edges"), []))
        return {"nodes": nodes, "edges": edges}

    # ============================================================
    # 语义缓存(归一化 query hash -> 应答)
    # ============================================================

    async def cache_get(self, qhash: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(self._k(f"cache:{qhash}"))
            return json.loads(raw) if raw else None
        return self._mem.get(self._k(f"cache:{qhash}"))

    async def cache_set(self, qhash: str, value: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(self._k(f"cache:{qhash}"),
                             _dump(value), ex=CACHE_TTL)
        else:
            self._mem[self._k(f"cache:{qhash}")] = value

    async def cache_clear(self) -> int:
        """清空语义缓存(管理面)"""
        removed = 0
        if is_redis_mode():
            client = await get_redis_client()
            keys = [k async for k in client.scan_iter(
                self._k("cache:*"))]
            for k in keys:
                await client.delete(k)
                removed += 1
        else:
            keys = [k for k in list(self._mem)
                    if k.startswith(self._k("cache:"))]
            for k in keys:
                del self._mem[k]
                removed += 1
        return removed

    async def cache_count(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return sum(1 async for _ in client.scan_iter(
                self._k("cache:*")))
        return sum(1 for k in self._mem
                   if k.startswith(self._k("cache:")))

    # ============================================================
    # 守门/缓存计数(分层聚合)
    # ============================================================

    async def bump_stat(self, group: str, field: str,
                        n: int = 1) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hincrby(
                self._k(f"stats:{group}"), field, n)
        else:
            k = self._k(f"stats:{group}")
            st = self._mem.setdefault(k, {})
            st[field] = st.get(field, 0) + n

    async def get_stats(self, group: str) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                self._k(f"stats:{group}"))
            return {k: int(v) for k, v in data.items()}
        return dict(self._mem.get(
            self._k(f"stats:{group}"), {}))

    # ============================================================
    # 问答留痕(截断索引)
    # ============================================================

    async def add_qa(self, record: dict) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            qid = await client.incr(self._k("qa:seq"))
            record["qaId"] = qid
            await client.set(
                self._k(f"qa:{qid}"), _dump(record))
            await client.lpush(self._k("qa:index"), qid)
            await client.ltrim(
                self._k("qa:index"), 0, QA_INDEX_MAX - 1)
            return qid
        qid = self._mem.get(self._k("qa:seq"), 0) + 1
        self._mem[self._k("qa:seq")] = qid
        record["qaId"] = qid
        self._mem[self._k(f"qa:{qid}")] = record
        index = self._mem.setdefault(
            self._k("qa:index"), [])
        index.insert(0, qid)
        del index[QA_INDEX_MAX:]
        return qid

    async def list_qa(self, limit: int = 50) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                self._k("qa:index"), 0, limit - 1)
            out = []
            for qid in ids:
                raw = await client.get(
                    self._k(f"qa:{qid}"))
                if raw:
                    out.append(json.loads(raw))
            return out
        ids = list(self._mem.get(
            self._k("qa:index"), []))[:limit]
        return [self._mem[self._k(f"qa:{i}")]
                for i in ids
                if self._k(f"qa:{i}") in self._mem]
