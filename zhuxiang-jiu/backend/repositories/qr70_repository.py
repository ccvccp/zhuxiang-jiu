"""70号·AI智能二维码大模型 仓储
(qr70_repository, P0)

规划(docs/70号_AI智能二维码大模型_创新规划方案.md
§七 P0):
    3 表(前缀 qr70):
        qr70_codes    码实例留痕
                      (nonce 键——生命周期)
        qr70_events   全链事件埋点
                      (生成/核销/愉悦样本)
        qr70_joy      愉悦度观测样本
                      (P7 引擎消费底座)

69号 pay69_repository 范式平移:
    - 通用读写基元(_save/_get/_list)
    - 五清单显式序列化(新增字段必须同步)
    - 70号永不写 55/69号表(叠加铁律)
"""

import contextlib
import json

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)


class Qr70Repository:
    """70号五表仓储(双模式——asyncio/Redis)"""

    TABLE_CODES = "qr70_codes"
    TABLE_EVENTS = "qr70_events"
    TABLE_JOY = "qr70_joy"
    TABLE_HYPOTHESES = "qr70_hypotheses"
    TABLE_PARAMS = "qr70_params"

    _ALL_TABLES = (TABLE_CODES, TABLE_EVENTS,
                   TABLE_JOY, TABLE_HYPOTHESES,
                   TABLE_PARAMS)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "codeSeq", "eventSeq", "joySeq",
        "memberId", "merchantId", "orderId",
        "durationMs", "sampleCount",
        "completeCount", "misTouchCount")
    _FLOAT_FIELDS = ("avgDurationMs", "completeRate")
    _BOOL_FIELDS = ("completed", "misTouch",
                    "redeemed", "scanned")
    _JSON_DICT_FIELDS = ("detail", "params",
                         "ribbons")
    _JSON_LIST_FIELDS = ("scenes", "skuNames",
                         "layoutBadges",
                         "confusionPairs")

    def __init__(self, store: dict = None):
        self.store = (store if store is not None
                      else get_in_memory_store())

    # ============================================================
    # 通用基元(69号范式)
    # ============================================================

    def _ensure_store(self):
        for table in self._ALL_TABLES:
            self.store.setdefault(table, {})

    def _serialize(self, record: dict) -> dict:
        """五清单序列化(Redis Hash 兼容)"""
        out = {}
        for k, v in record.items():
            if v is None:
                continue
            if k in self._INT_FIELDS:
                out[k] = int(v)
            elif k in self._FLOAT_FIELDS:
                out[k] = float(v)
            elif k in self._BOOL_FIELDS:
                out[k] = 1 if v else 0
            elif k in self._JSON_DICT_FIELDS:
                out[k] = (json.dumps(
                    v, ensure_ascii=False)
                    if isinstance(v, dict) else v)
            elif k in self._JSON_LIST_FIELDS:
                out[k] = (json.dumps(
                    v, ensure_ascii=False)
                    if isinstance(v, (list, tuple))
                    else v)
            else:
                out[k] = str(v)
        return out

    def _deserialize(self, data: dict) -> dict:
        """五清单反序列化(读回类型还原)"""
        out = dict(data)
        for k in self._INT_FIELDS:
            if k in out:
                with contextlib.suppress(
                        TypeError, ValueError):
                    out[k] = int(float(out[k]))
        for k in self._FLOAT_FIELDS:
            if k in out:
                with contextlib.suppress(
                        TypeError, ValueError):
                    out[k] = float(out[k])
        for k in self._BOOL_FIELDS:
            if k in out:
                out[k] = str(out[k]) == "1"
        for k in (self._JSON_DICT_FIELDS
                  + self._JSON_LIST_FIELDS):
            if k in out and isinstance(out[k], str):
                with contextlib.suppress(
                        ValueError, TypeError):
                    out[k] = json.loads(out[k])
        return out

    # ============================================================
    # 码实例留痕(qr70_codes——nonce 键)
    # ============================================================

    async def find_code_by_nonce(
            self, nonce: str) -> dict | None:
        """按 nonce 查码实例(核销防重放)"""
        if not nonce:
            return None
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("qr70", "code", nonce))
            if not data:
                return None
            return self._deserialize(data)
        self._ensure_store()
        rec = self.store[self.TABLE_CODES]\
            .get(nonce)
        return dict(rec) if rec else None

    async def save_code(self, nonce: str,
                        record: dict) -> dict:
        """保存/覆盖码实例(生命周期状态机)"""
        record["nonce"] = nonce
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("qr70", "code", nonce),
                mapping=self._serialize(record))
            await client.sadd(
                _k("qr70", "code", "index"),
                str(nonce))
            return record
        self._ensure_store()
        self.store[self.TABLE_CODES][nonce] \
            = dict(record)
        return record

    async def list_codes(
            self, limit: int = 50,
            kind: str = "") -> list[dict]:
        """码实例列表(倒序, 可按码类过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            nonces = await client.smembers(
                _k("qr70", "code", "index"))
            result = []
            for n in nonces:
                data = await client.hgetall(
                    _k("qr70", "code", n))
                if data:
                    rec = self._deserialize(data)
                    if not kind \
                            or rec.get("kind") == kind:
                        result.append(rec)
            result.sort(
                key=lambda r: int(
                    r.get("codeSeq") or 0),
                reverse=True)
            return result[:limit]
        self._ensure_store()
        recs = [dict(r) for r
                in self.store[self.TABLE_CODES]
                .values()]
        if kind:
            recs = [r for r in recs
                    if r.get("kind") == kind]
        recs.sort(
            key=lambda r: int(
                r.get("codeSeq") or 0),
            reverse=True)
        return recs[:limit]

    async def next_code_seq(self) -> int:
        """码实例序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("qr70", "code", "seq"))
        self._ensure_store()
        self.store["_qr70_code_seq"] = \
            self.store.get("_qr70_code_seq", 0) + 1
        return self.store["_qr70_code_seq"]

    # ============================================================
    # 全链事件埋点(qr70_events)
    # ============================================================

    async def next_event_seq(self) -> int:
        """事件序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("qr70", "event", "seq"))
        self._ensure_store()
        self.store["_qr70_event_seq"] = \
            self.store.get("_qr70_event_seq", 0) + 1
        return self.store["_qr70_event_seq"]

    async def save_event(self, event: dict) -> dict:
        """保存事件(生成/核销/冻结等全留痕)"""
        seq = await self.next_event_seq()
        event["eventSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("qr70", "event", seq),
                json.dumps(event, ensure_ascii=False))
            await client.sadd(
                _k("qr70", "event", "index"),
                str(seq))
            return event
        self._ensure_store()
        self.store[self.TABLE_EVENTS][seq] \
            = dict(event)
        return event

    async def list_events(
            self, limit: int = 50) -> list[dict]:
        """事件列表(倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            seqs = await client.smembers(
                _k("qr70", "event", "index"))
            result = []
            for s in sorted(
                    (int(x) for x in seqs),
                    reverse=True)[:limit]:
                data = await client.get(
                    _k("qr70", "event", s))
                if data:
                    result.append(json.loads(data))
            return result
        self._ensure_store()
        recs = sorted(
            self.store[self.TABLE_EVENTS].values(),
            key=lambda r: int(
                r.get("eventSeq") or 0),
            reverse=True)
        return [dict(r) for r in recs[:limit]]

    # ============================================================
    # 愉悦度观测样本(qr70_joy——P7 引擎
    # 消费底座; 快环纯统计不受开关影响)
    # ============================================================

    async def next_joy_seq(self) -> int:
        """愉悦样本序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("qr70", "joy", "seq"))
        self._ensure_store()
        self.store["_qr70_joy_seq"] = \
            self.store.get("_qr70_joy_seq", 0) + 1
        return self.store["_qr70_joy_seq"]

    async def save_joy(self, record: dict) -> dict:
        """保存愉悦度观测样本"""
        seq = await self.next_joy_seq()
        record["joySeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("qr70", "joy", seq),
                json.dumps(record, ensure_ascii=False))
            await client.sadd(
                _k("qr70", "joy", "index"),
                str(seq))
            return record
        self._ensure_store()
        self.store[self.TABLE_JOY][seq] \
            = dict(record)
        return record

    async def list_joy(
            self, limit: int = 200) -> list[dict]:
        """愉悦样本列表(统计口径全量倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            seqs = await client.smembers(
                _k("qr70", "joy", "index"))
            result = []
            for s in sorted(
                    (int(x) for x in seqs),
                    reverse=True)[:limit]:
                data = await client.get(
                    _k("qr70", "joy", s))
                if data:
                    result.append(json.loads(data))
            return result
        self._ensure_store()
        recs = sorted(
            self.store[self.TABLE_JOY].values(),
            key=lambda r: int(
                r.get("joySeq") or 0),
            reverse=True)
        return [dict(r) for r in recs[:limit]]

    # ============================================================
    # 进化假设建议书(qr70_hypotheses——P7
    # 决策优化层; proposed→submitted/
    # rejected, 46号审批回执留痕)
    # ============================================================

    async def next_hypothesis_id(self) -> int:
        """假设建议书 ID"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("qr70", "hyp", "seq"))
        self._ensure_store()
        self.store["_qr70_hyp_seq"] = \
            self.store.get(
                "_qr70_hyp_seq", 0) + 1
        return self.store["_qr70_hyp_seq"]

    async def save_hypothesis(
            self, hyp_id: int,
            record: dict) -> dict:
        """保存假设建议书(索引入册)"""
        record["hypothesisId"] = hyp_id
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("qr70", "hyp", hyp_id),
                json.dumps(record,
                           ensure_ascii=False))
            await client.sadd(
                _k("qr70", "hyp", "index"),
                str(hyp_id))
            return record
        self._ensure_store()
        self.store[self.TABLE_HYPOTHESES]\
            .setdefault(
                "by_id", {})[hyp_id] \
            = dict(record)
        return record

    async def get_hypothesis(
            self, hyp_id: int) -> dict | None:
        """按 ID 查假设建议书"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("qr70", "hyp", hyp_id))
            return json.loads(data) \
                if data else None
        self._ensure_store()
        rec = self.store[
            self.TABLE_HYPOTHESES]\
            .get("by_id", {}).get(hyp_id)
        return dict(rec) if rec else None

    async def list_hypotheses(
            self, status: str = None,
            limit: int = 50) -> list[dict]:
        """假设建议书列表(倒序; 状态过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.smembers(
                _k("qr70", "hyp", "index"))
            result = []
            for i in sorted(
                    (int(x) for x in ids),
                    reverse=True)[:limit * 3]:
                rec = await self.get_hypothesis(i)
                if not rec:
                    continue
                if status and rec.get(
                        "status") != status:
                    continue
                result.append(rec)
                if len(result) >= limit:
                    break
            return result
        self._ensure_store()
        recs = list(
            self.store[
                self.TABLE_HYPOTHESES]
            .get("by_id", {}).values())
        recs.sort(key=lambda r: r.get(
            "hypothesisId", 0),
            reverse=True)
        if status:
            recs = [r for r in recs
                    if r.get("status")
                    == status]
        return [dict(r)
                for r in recs[:limit]]

    # ============================================================
    # 参数版本基线(qr70_params——P7;
    # draft→shadow→active→retired)
    # ============================================================

    async def next_param_version(self) -> int:
        """参数版本号(全局递增)"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("qr70", "param", "ver"))
        self._ensure_store()
        self.store[
            "_qr70_param_ver"] = \
            self.store.get(
                "_qr70_param_ver", 0) + 1
        return self.store[
            "_qr70_param_ver"]

    async def save_param_version(
            self, version: int,
            record: dict) -> dict:
        """保存参数版本(索引入册)"""
        record["version"] = version
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("qr70", "param", "v",
                   version),
                json.dumps(record,
                           ensure_ascii=False))
            await client.sadd(
                _k("qr70", "param", "index"),
                str(version))
            return record
        self._ensure_store()
        self.store[self.TABLE_PARAMS]\
            .setdefault(
                "by_version", {})[version] \
            = dict(record)
        return record

    async def get_param_version(
            self, version: int) -> dict | None:
        """按版本号查参数"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("qr70", "param", "v",
                   version))
            return json.loads(data) \
                if data else None
        self._ensure_store()
        rec = self.store[
            self.TABLE_PARAMS]\
            .get("by_version", {}
                 ).get(version)
        return dict(rec) if rec else None

    async def list_param_versions(
            self, param_id: str = None,
            status: str = None,
            limit: int = 50) -> list[dict]:
        """参数版本列表(倒序; 参数域/
        状态过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            versions = await client.smembers(
                _k("qr70", "param", "index"))
            result = []
            for v in sorted(
                    (int(x) for x in versions),
                    reverse=True)[:limit * 3]:
                rec = await self\
                    .get_param_version(v)
                if not rec:
                    continue
                if param_id and rec.get(
                        "paramId") != param_id:
                    continue
                if status and rec.get(
                        "status") != status:
                    continue
                result.append(rec)
                if len(result) >= limit:
                    break
            return result
        self._ensure_store()
        recs = list(
            self.store[
                self.TABLE_PARAMS]
            .get("by_version",
                 {}).values())
        recs.sort(key=lambda r: r.get(
            "version", 0),
            reverse=True)
        if param_id:
            recs = [r for r in recs
                    if r.get("paramId")
                    == param_id]
        if status:
            recs = [r for r in recs
                    if r.get("status")
                    == status]
        return [dict(r)
                for r in recs[:limit]]
