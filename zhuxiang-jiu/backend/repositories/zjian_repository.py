"""竹鉴·BambooVerify(77号)——质检典藏大模型·仓储层

设计依据: D:\竹奕酒的资料 双检测报告(ZZ26SW1489303A 52%vol /
ZZ26SW1489404B 42%vol——山东中质华检测试检验有限公司, 判定
依据 Q/SRQ 0001S-2023 / GB 2760-2024 / GB 7718-2025)
工程化裁剪(纯确定性, LLM 禁入——全站铁律; 75/76 号范式同源)。

存储(Redis 前缀 zhuxiang:zjian:*, 索引制):
    report:{rid}          检测报告元数据+指标明细 hash
    report:ids            报告 ID 索引 set
    ask:{seq}             质检问答留痕
    ask:seq               留痕自增
    stats:guard           护栏计数 hash(total/bench_fail/
                          cite_miss/out_context)
    stats:metric          指标命中分布 hash

红线: 报告典藏锚定(检测数据人工录入——原文事实源)、
护栏恢复 resume——永不自主(full 档亦人工)。
"""

import json
import logging

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store,
)

logger = logging.getLogger(__name__)

PREFIX = "zhuxiang:zjian:"


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class ZjianRepository:
    """竹鉴·质检典藏 + 问答留痕 + 统计"""

    def __init__(self):
        self._mem = get_in_memory_store()

    def _k(self, key: str) -> str:
        return f"{PREFIX}{key}"

    # ============================================================
    # 检测报告典藏(人工锚定——原文事实源)
    # ============================================================

    async def upsert_report(self, rid: str,
                            report: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(self._k("report"), rid,
                             _dump(report))
            await client.sadd(self._k("report:ids"), rid)
        else:
            self._mem[self._k(f"report:{rid}")] = report
            ids = self._mem.setdefault(
                self._k("report:ids"), set())
            ids.add(rid)

    async def get_report(self, rid: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hget(self._k("report"), rid)
            return json.loads(raw) if raw else None
        return self._mem.get(self._k(f"report:{rid}"))

    async def list_reports(self) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            ids = sorted(await client.smembers(
                self._k("report:ids")))
            out = []
            for rid in ids:
                raw = await client.hget(
                    self._k("report"), rid)
                if raw:
                    out.append(json.loads(raw))
            return out
        ids = sorted(self._mem.get(
            self._k("report:ids"), set()))
        return [self._mem[self._k(f"report:{i}")]
                for i in ids
                if self._k(f"report:{i}") in self._mem]

    # ============================================================
    # 问答留痕
    # ============================================================

    async def next_id(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(self._k(f"{kind}:seq"))
        seq = self._mem.get(self._k(f"{kind}:seq"), 0)
        self._mem[self._k(f"{kind}:seq")] = seq + 1
        return seq + 1

    async def save_ask(self, aid: int,
                       record: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(self._k(f"ask:{aid}"),
                             _dump(record))
        else:
            self._mem[self._k(f"ask:{aid}")] = record

    async def list_asks(self, limit: int = 20) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            seq = int(await client.get(
                self._k("ask:seq")) or 0)
            out = []
            for aid in range(max(1, seq - limit + 1),
                             seq + 1):
                raw = await client.get(
                    self._k(f"ask:{aid}"))
                if raw:
                    out.append(json.loads(raw))
            out.reverse()
            return out
        seq = self._mem.get(self._k("ask:seq"), 0)
        out = []
        for aid in range(max(1, seq - limit + 1),
                         seq + 1):
            rec = self._mem.get(self._k(f"ask:{aid}"))
            if rec:
                out.append(rec)
        out.reverse()
        return out

    # ============================================================
    # 分组计数(护栏/指标命中)
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
