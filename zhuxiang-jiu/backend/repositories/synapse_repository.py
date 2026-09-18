"""织智·Synapse-Weave(76号)——AI智能内容织造大模型·仓储层

设计依据: 《Synapse-Weave(织智) 创新方案文档》工程化裁剪
(双师协同蒸馏/织机式动态融合/织补式进化/织智日记——全站铁律
LLM 禁入, 纯确定性实现, 75号范式同源)。

存储(Redis 前缀 zhuxiang:synapse:*, 索引制):
    corpus:{cid}          织造语料(黄金样本库, hash)
    corpus:ids            语料 ID 索引 set
    hotspot:{hid}         热点源数据(标题/内容/采集日)
    hotspot:ids           热点 ID 索引 set
    weave:{wid}           织造产物(文本/经纬权重/双维评分)
    weave:ids             织造 ID 索引 set
    patch:{pid}           织补记录(损伤定位/修复/缝合验证)
    patch:ids             织补 ID 索引 set
    diary:{date}          织智日记(按日, 品牌语言指标转译)
    diary:index           日记日期索引 list(截断防膨胀)
    stats:router          路由分布计数 hash(任务域×权重档)
    stats:guard           护栏计数 hash(total/fact_fail/
                          persona_fail/compliance_hit)
    stats:score           双维评分累计 hash(logic_sum/human_sum/
                          pds_sum/eval_count)

红线: 人格经线(persona)定义变更/织补回滚/红线词库/知识结晶
固化——永不自主(full 档亦人工显式)。
"""

import json
import logging

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store,
)

logger = logging.getLogger(__name__)

PREFIX = "zhuxiang:synapse:"

# 日记索引截断(防无限膨胀)
DIARY_INDEX_MAX = 90


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class SynapseRepository:
    """织智·Synapse-Weave 语料/热点/织造/织补/日记仓储"""

    def __init__(self):
        self._mem = get_in_memory_store()

    def _k(self, key: str) -> str:
        return f"{PREFIX}{key}"

    # ============================================================
    # 织造语料(黄金样本库)
    # ============================================================

    async def upsert_corpus(self, cid: str, item: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(self._k("corpus"), cid, _dump(item))
            await client.sadd(self._k("corpus:ids"), cid)
        else:
            self._mem[self._k(f"corpus:{cid}")] = item
            ids = self._mem.setdefault(
                self._k("corpus:ids"), set())
            ids.add(cid)

    async def get_corpus(self, cid: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hget(self._k("corpus"), cid)
            return json.loads(raw) if raw else None
        return self._mem.get(self._k(f"corpus:{cid}"))

    async def list_corpus(self) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            ids = sorted(await client.smembers(
                self._k("corpus:ids")))
            out = []
            for cid in ids:
                raw = await client.hget(self._k("corpus"), cid)
                if raw:
                    out.append(json.loads(raw))
            return out
        ids = sorted(self._mem.get(
            self._k("corpus:ids"), set()))
        return [self._mem[self._k(f"corpus:{i}")]
                for i in ids
                if self._k(f"corpus:{i}") in self._mem]

    # ============================================================
    # 热点源数据
    # ============================================================

    async def upsert_hotspot(self, hid: str,
                             item: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(self._k("hotspot"), hid,
                             _dump(item))
            await client.sadd(self._k("hotspot:ids"), hid)
        else:
            self._mem[self._k(f"hotspot:{hid}")] = item
            ids = self._mem.setdefault(
                self._k("hotspot:ids"), set())
            ids.add(hid)

    async def get_hotspot(self, hid: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hget(self._k("hotspot"), hid)
            return json.loads(raw) if raw else None
        return self._mem.get(self._k(f"hotspot:{hid}"))

    async def list_hotspots(self) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            ids = sorted(await client.smembers(
                self._k("hotspot:ids")))
            out = []
            for hid in ids:
                raw = await client.hget(self._k("hotspot"), hid)
                if raw:
                    out.append(json.loads(raw))
            return out
        ids = sorted(self._mem.get(
            self._k("hotspot:ids"), set()))
        return [self._mem[self._k(f"hotspot:{i}")]
                for i in ids
                if self._k(f"hotspot:{i}") in self._mem]

    # ============================================================
    # 织造产物(留痕)
    # ============================================================

    async def next_id(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(self._k(f"{kind}:seq"))
        seq = self._mem.get(self._k(f"{kind}:seq"), 0)
        self._mem[self._k(f"{kind}:seq")] = seq + 1
        return seq + 1

    async def save_weave(self, wid: int,
                         record: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(self._k(f"weave:{wid}"),
                             _dump(record))
            await client.sadd(self._k("weave:ids"), wid)
        else:
            self._mem[self._k(f"weave:{wid}")] = record
            ids = self._mem.setdefault(
                self._k("weave:ids"), set())
            ids.add(wid)

    async def get_weave(self, wid: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(self._k(f"weave:{wid}"))
            return json.loads(raw) if raw else None
        return self._mem.get(self._k(f"weave:{wid}"))

    async def list_weaves(self, limit: int = 20) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            ids = sorted(await client.smembers(
                self._k("weave:ids")), reverse=True)
            out = []
            for wid in ids[:limit]:
                raw = await client.get(self._k(f"weave:{wid}"))
                if raw:
                    out.append(json.loads(raw))
            return out
        ids = sorted(self._mem.get(
            self._k("weave:ids"), set()), reverse=True)
        out = []
        for wid in ids[:limit]:
            rec = self._mem.get(self._k(f"weave:{wid}"))
            if rec:
                out.append(rec)
        return out

    # ============================================================
    # 织补记录(损伤定位→修复→缝合验证)
    # ============================================================

    async def save_patch(self, pid: int,
                         record: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(self._k(f"patch:{pid}"),
                             _dump(record))
            await client.sadd(self._k("patch:ids"), pid)
        else:
            self._mem[self._k(f"patch:{pid}")] = record
            ids = self._mem.setdefault(
                self._k("patch:ids"), set())
            ids.add(pid)

    async def list_patches(self,
                           limit: int = 20) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            ids = sorted(await client.smembers(
                self._k("patch:ids")), reverse=True)
            out = []
            for pid in ids[:limit]:
                raw = await client.get(
                    self._k(f"patch:{pid}"))
                if raw:
                    out.append(json.loads(raw))
            return out
        ids = sorted(self._mem.get(
            self._k("patch:ids"), set()), reverse=True)
        out = []
        for pid in ids[:limit]:
            rec = self._mem.get(self._k(f"patch:{pid}"))
            if rec:
                out.append(rec)
        return out

    # ============================================================
    # 织智日记(按日, 品牌语言转译)
    # ============================================================

    async def save_diary(self, date: str,
                         record: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(self._k(f"diary:{date}"),
                             _dump(record))
            await client.lrem(
                self._k("diary:index"), 0, date)
            await client.lpush(self._k("diary:index"), date)
            await client.ltrim(
                self._k("diary:index"), 0, DIARY_INDEX_MAX - 1)
        else:
            self._mem[self._k(f"diary:{date}")] = record
            idx = self._mem.setdefault(
                self._k("diary:index"), [])
            if date in idx:
                idx.remove(date)
            idx.insert(0, date)
            del idx[DIARY_INDEX_MAX:]

    async def get_diary(self, date: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                self._k(f"diary:{date}"))
            return json.loads(raw) if raw else None
        return self._mem.get(self._k(f"diary:{date}"))

    async def list_diary_dates(self,
                              limit: int = 10) -> list[str]:
        if is_redis_mode():
            client = await get_redis_client()
            return [d for d in await client.lrange(
                self._k("diary:index"), 0, limit - 1)]
        return list(self._mem.get(
            self._k("diary:index"), [])[:limit])

    # ============================================================
    # 分组计数(路由分布/护栏/评分累计)
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
