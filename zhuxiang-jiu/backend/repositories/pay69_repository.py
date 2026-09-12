"""69号·AI智能支付大模型 仓储
(pay69_repository, P0)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§七 P0):
    3 表(前缀 pay69):
        pay69_channels    通道健康度观测
                          (滚动窗口统计基线)
        pay69_intents     意图标签留痕
                          (60号归因链消费)
        pay69_events      全链事件埋点
                          (P1 路由/P2 熵共享)

60号仓储范式平移:
    - 通用读写基元(_save/_get/_list)
    - 五清单显式序列化(新增字段必须同步)
    - 69号永不写 60号表(叠加铁律)
"""

import contextlib
import json
import time

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)

# 通道流滚动窗口截断倍数(容量×2——
# 窗口统计上限之外的余量)
_WINDOW = 100


def _now_ts() -> float:
    """当前 Unix 秒(内存态 TTL 模拟)"""
    return time.time()


class Pay69Repository:
    """69号十二表仓储(双模式——asyncio/Redis)"""

    TABLE_CHANNELS = "pay69_channels"
    TABLE_INTENTS = "pay69_intents"
    TABLE_EVENTS = "pay69_events"
    TABLE_FLOWS = "pay69_flows"
    TABLE_HABITS = "pay69_habits"
    TABLE_ENTROPY = "pay69_entropy"
    TABLE_BEHAVIOR = "pay69_behavior"
    TABLE_CREDIT = "pay69_credit"
    TABLE_REPAY = "pay69_repay"
    TABLE_BIO = "pay69_bio"
    TABLE_TEMPLATES = "pay69_templates"
    TABLE_SMARTCODE = "pay69_smartcode"

    _ALL_TABLES = (
        TABLE_CHANNELS, TABLE_INTENTS,
        TABLE_EVENTS, TABLE_FLOWS,
        TABLE_HABITS, TABLE_ENTROPY,
        TABLE_BEHAVIOR, TABLE_CREDIT,
        TABLE_REPAY, TABLE_BIO,
        TABLE_TEMPLATES,
        TABLE_SMARTCODE)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "channelSeq", "intentSeq", "eventSeq",
        "memberId", "intentId", "sessionId",
        "attemptCount", "successCount",
        "failureCount", "timeoutCount")
    _FLOAT_FIELDS = (
        "successRate", "avgLatencyMs",
        "feeRate", "singleLimit",
        "dailyLimit", "amount")
    _BOOL_FIELDS = ("frozen",)
    _JSON_DICT_FIELDS = (
        "windows", "traits", "meta",
        "attribution", "context", "detail")
    _JSON_LIST_FIELDS = ("tags", "candidates")

    def __init__(self, store: dict = None):
        self.store = (store if store is not None
                      else get_in_memory_store())

    # ============================================================
    # 通用基元(60号范式)
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
                out[k] = (json.dumps(v, ensure_ascii=False)
                          if isinstance(v, dict) else v)
            elif k in self._JSON_LIST_FIELDS:
                out[k] = (json.dumps(v, ensure_ascii=False)
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
    # 通道健康度观测(pay69_channels)
    # ============================================================

    async def get_channel(self, channel_id: str) -> dict | None:
        """按通道 ID 查健康度记录(内存态
        存取同形——60号 _get 范式, 不走
        deserialize 防 bool 陷阱)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay69", "channel", channel_id))
            if not data:
                return None
            return self._deserialize(data)
        self._ensure_store()
        rec = self.store[self.TABLE_CHANNELS]\
            .get(channel_id)
        return dict(rec) if rec else None

    async def save_channel(self, channel_id: str,
                          record: dict) -> dict:
        """保存/覆盖通道健康度记录"""
        record["channelId"] = channel_id
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay69", "channel", channel_id)
            await client.hset(
                key, mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_CHANNELS][channel_id] \
            = dict(record)
        return record

    async def list_channels(self) -> list[dict]:
        """列出全部通道健康度记录"""
        from services.pay69_registry import (
            CHANNEL_IDS,
        )
        result = []
        for cid in CHANNEL_IDS:
            rec = await self.get_channel(cid)
            result.append(rec or {
                "channelId": cid,
                "state": "healthy",  # 未观测=默认健康
                "frozen": False,
            })
        return result

    # ============================================================
    # 意图标签留痕(pay69_intents)
    # ============================================================

    async def next_intent_seq(self) -> int:
        """意图标签序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay69", "intent", "seq"))
        self._ensure_store()
        self.store["_pay69_intent_seq"] = \
            self.store.get("_pay69_intent_seq", 0) + 1
        return self.store["_pay69_intent_seq"]

    async def save_intent(self, record: dict) -> dict:
        """保存意图标签留痕"""
        seq = await self.next_intent_seq()
        record["intentSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("pay69", "intent", seq),
                json.dumps(record, ensure_ascii=False))
            await client.sadd(
                _k("pay69", "intent", "index"),
                str(seq))
            return record
        self._ensure_store()
        self.store[self.TABLE_INTENTS][seq] \
            = dict(record)
        return record

    async def get_intent(self, seq: int) -> dict | None:
        """按序列查意图留痕"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("pay69", "intent", seq))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store[self.TABLE_INTENTS].get(seq)

    async def list_intents(
            self, limit: int = 50) -> list[dict]:
        """意图留痕列表(倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            seqs = await client.smembers(
                _k("pay69", "intent", "index"))
            result = []
            for s in sorted(
                    (int(x) for x in seqs),
                    reverse=True)[:limit]:
                data = await client.get(
                    _k("pay69", "intent", s))
                if data:
                    result.append(json.loads(data))
            return result
        self._ensure_store()
        recs = sorted(
            self.store[self.TABLE_INTENTS].values(),
            key=lambda r: r.get("intentSeq", 0),
            reverse=True)[:limit]
        return [dict(r) for r in recs]

    # ============================================================
    # 全链事件埋点(pay69_events)
    # ============================================================

    async def next_event_seq(self) -> int:
        """事件序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay69", "event", "seq"))
        self._ensure_store()
        self.store["_pay69_event_seq"] = \
            self.store.get("_pay69_event_seq", 0) + 1
        return self.store["_pay69_event_seq"]

    async def save_event(self, record: dict) -> dict:
        """保存事件埋点"""
        seq = await self.next_event_seq()
        record["eventSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("pay69", "event", seq),
                json.dumps(record, ensure_ascii=False))
            await client.sadd(
                _k("pay69", "event", "index"),
                str(seq))
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][seq] = dict(record)
        return record

    async def list_events(
            self, limit: int = 50) -> list[dict]:
        """事件列表(倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            seqs = await client.smembers(
                _k("pay69", "event", "index"))
            result = []
            for s in sorted(
                    (int(x) for x in seqs),
                    reverse=True)[:limit]:
                data = await client.get(
                    _k("pay69", "event", s))
                if data:
                    result.append(json.loads(data))
            return result
        self._ensure_store()
        recs = sorted(
            self.store[self.TABLE_EVENTS].values(),
            key=lambda r: r.get("eventSeq", 0),
            reverse=True)[:limit]
        return [dict(r) for r in recs]

    # ============================================================
    # 路由执行留痕(pay69_flows——P1)
    # LIST 结构: 全局流 + 通道流(滚动
    # 窗口口径), JSON 字符串存取双模式
    # 同形(免五清单陷阱)
    # ============================================================

    async def next_flow_seq(self) -> int:
        """路由执行序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay69", "flow", "seq"))
        self._ensure_store()
        self.store["_pay69_flow_seq"] = \
            self.store.get("_pay69_flow_seq", 0) + 1
        return self.store["_pay69_flow_seq"]

    async def save_flow(self, record: dict) -> dict:
        """保存路由执行留痕(通道流滚动
        窗口 2×容量截断, 全局流 1000 条)"""
        seq = await self.next_flow_seq()
        record["flowSeq"] = seq
        payload = json.dumps(
            record, ensure_ascii=False)
        cid = str(record.get("channelId")
                  or "unknown")
        if is_redis_mode():
            client = await get_redis_client()
            ch_key = _k("pay69", "flow",
                        "ch", cid)
            await client.lpush(ch_key, payload)
            await client.ltrim(
                ch_key, 0, 2 * _WINDOW - 1)
            all_key = _k("pay69", "flow", "all")
            await client.lpush(all_key, payload)
            await client.ltrim(all_key, 0, 999)
            return record
        self._ensure_store()
        flows = self.store[self.TABLE_FLOWS]
        flows.setdefault("all", [])\
            .insert(0, dict(record))
        del flows["all"][1000:]
        by_ch = flows.setdefault(
            "by_channel", {})
        by_ch.setdefault(cid, [])\
            .insert(0, dict(record))
        del by_ch[cid][2 * _WINDOW:]
        return record

    async def list_flows(
            self, channel_id: str = None,
            limit: int = 50) -> list[dict]:
        """路由执行留痕列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            if channel_id:
                data = await client.lrange(
                    _k("pay69", "flow", "ch",
                       channel_id), 0, limit - 1)
            else:
                data = await client.lrange(
                    _k("pay69", "flow", "all"),
                    0, limit - 1)
            return [json.loads(x) for x in data]
        self._ensure_store()
        flows = self.store[self.TABLE_FLOWS]
        recs = (flows.get("by_channel", {})
                .get(channel_id, []) if channel_id
                else flows.get("all", []))
        return [dict(r) for r in recs[:limit]]

    async def window_of(
            self, channel_id: str,
            size: int = None) -> dict:
        """通道滚动窗口统计(最近 N 次
        执行——快环确定性基线; 无留痕
        successRate=None)"""
        from services.pay69_registry import (
            ROUTE_WINDOW_SIZE,
        )
        n = size or ROUTE_WINDOW_SIZE
        flows = await self.list_flows(
            channel_id, limit=n)
        attempt = len(flows)
        success = sum(
            1 for f in flows if f.get("success"))
        return {
            "channelId": channel_id,
            "attemptCount": attempt,
            "successCount": success,
            "successRate": (round(
                success / attempt, 4)
                if attempt else None),
        }

    # ============================================================
    # 会员通道习惯(pay69_habits——P1 快环)
    # ============================================================

    async def bump_habit(
            self, member_id: int,
            channel_id: str,
            count: int = 1) -> dict:
        """会员通道使用计数累加(快环观测
        ——HINCRBY 原子)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hincrby(
                _k("pay69", "habit", member_id),
                channel_id, int(count))
            return await self.get_habits(member_id)
        self._ensure_store()
        habits = self.store[self.TABLE_HABITS]\
            .setdefault(member_id, {})
        habits[channel_id] = \
            habits.get(channel_id, 0) + int(count)
        return dict(habits)

    async def get_habits(
            self, member_id: int) -> dict:
        """会员通道使用计数视图"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay69", "habit", member_id))
            return {k: int(v)
                    for k, v in data.items()}
        self._ensure_store()
        return dict(
            self.store[self.TABLE_HABITS]
            .get(member_id, {}))

    # ============================================================
    # 熵评估留痕(pay69_entropy——P2)
    # ============================================================

    async def next_entropy_seq(self) -> int:
        """熵评估序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay69", "entropy", "seq"))
        self._ensure_store()
        self.store["_pay69_entropy_seq"] = \
            self.store.get("_pay69_entropy_seq", 0) + 1
        return self.store["_pay69_entropy_seq"]

    async def save_entropy(self, record: dict) -> dict:
        """保存熵评估留痕(全局流+会员流)"""
        seq = await self.next_entropy_seq()
        record["entropySeq"] = seq
        payload = json.dumps(
            record, ensure_ascii=False)
        mid = str(record.get("memberId") or 0)
        if is_redis_mode():
            client = await get_redis_client()
            await client.lpush(
                _k("pay69", "entropy", "all"),
                payload)
            await client.ltrim(
                _k("pay69", "entropy", "all"),
                0, 999)
            await client.lpush(
                _k("pay69", "entropy", "member",
                   mid), payload)
            await client.ltrim(
                _k("pay69", "entropy", "member",
                   mid), 0, 199)
            return record
        self._ensure_store()
        table = self.store[self.TABLE_ENTROPY]
        table.setdefault("all", [])\
            .insert(0, dict(record))
        del table["all"][1000:]
        table.setdefault(
            "by_member", {}
        ).setdefault(mid, [])\
            .insert(0, dict(record))
        del table["by_member"][mid][200:]
        return record

    async def list_entropy(
            self, member_id: int = None,
            limit: int = 50) -> list[dict]:
        """熵评估留痕列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            if member_id:
                data = await client.lrange(
                    _k("pay69", "entropy", "member",
                       member_id), 0, limit - 1)
            else:
                data = await client.lrange(
                    _k("pay69", "entropy", "all"),
                    0, limit - 1)
            return [json.loads(x) for x in data]
        self._ensure_store()
        table = self.store[self.TABLE_ENTROPY]
        recs = (table.get("by_member", {})
                .get(str(member_id), [])
                if member_id is not None
                else table.get("all", []))
        return [dict(r) for r in recs[:limit]]

    # ============================================================
    # 行为基线(pay69_behavior——P2 快环)
    # ============================================================

    async def get_baseline(
            self, member_id: int) -> dict | None:
        """会员行为基线(无则 None)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay69", "behavior", member_id))
            if not data:
                return None
            out = {}
            for k, v in data.items():
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    out[k] = v
            if "samples" in out:
                with contextlib.suppress(
                        TypeError, ValueError):
                    out["samples"] = int(
                        float(out["samples"]))
            return out
        self._ensure_store()
        rec = self.store[self.TABLE_BEHAVIOR]\
            .get(member_id)
        return dict(rec) if rec else None

    # ============================================================
    # 授信留痕+调额建议书(pay69_credit——P3)
    # ============================================================

    async def next_credit_seq(self) -> int:
        """授信/调额序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay69", "credit", "seq"))
        self._ensure_store()
        self.store["_pay69_credit_seq"] = \
            self.store.get("_pay69_credit_seq", 0) + 1
        return self.store["_pay69_credit_seq"]

    async def save_credit(self, record: dict) -> dict:
        """保存授信评估留痕(全局+会员)"""
        seq = await self.next_credit_seq()
        record["creditSeq"] = seq
        payload = json.dumps(
            record, ensure_ascii=False)
        mid = str(record.get("memberId") or 0)
        if is_redis_mode():
            client = await get_redis_client()
            await client.lpush(
                _k("pay69", "credit", "all"),
                payload)
            await client.ltrim(
                _k("pay69", "credit", "all"),
                0, 999)
            await client.lpush(
                _k("pay69", "credit", "member",
                   mid), payload)
            await client.ltrim(
                _k("pay69", "credit", "member",
                   mid), 0, 199)
            return record
        self._ensure_store()
        table = self.store[self.TABLE_CREDIT]
        table.setdefault("all", [])\
            .insert(0, dict(record))
        del table["all"][1000:]
        table.setdefault(
            "by_member", {}
        ).setdefault(mid, [])\
            .insert(0, dict(record))
        del table["by_member"][mid][200:]
        return record

    async def list_credit(
            self, member_id: int = None,
            limit: int = 50) -> list[dict]:
        """授信留痕列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            if member_id:
                data = await client.lrange(
                    _k("pay69", "credit", "member",
                       member_id), 0, limit - 1)
            else:
                data = await client.lrange(
                    _k("pay69", "credit", "all"),
                    0, limit - 1)
            return [json.loads(x) for x in data]
        self._ensure_store()
        table = self.store[self.TABLE_CREDIT]
        recs = (table.get("by_member", {})
                .get(str(member_id), [])
                if member_id is not None
                else table.get("all", []))
        return [dict(r) for r in recs[:limit]]

    # ---------- 调额建议书(Hash 单键) ----------

    async def next_adjust_seq(self) -> int:
        """调额建议书独立序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay69", "adjust", "seq"))
        self._ensure_store()
        self.store["_pay69_adjust_seq"] = \
            self.store.get("_pay69_adjust_seq", 0) + 1
        return self.store["_pay69_adjust_seq"]

    async def save_adjustment(
            self, adj_id: int,
            record: dict) -> dict:
        """保存调额建议书(索引入册)"""
        record["adjustmentId"] = adj_id
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("pay69", "adjust", adj_id),
                json.dumps(record,
                           ensure_ascii=False))
            await client.sadd(
                _k("pay69", "adjust", "index"),
                str(adj_id))
            return record
        self._ensure_store()
        self.store[self.TABLE_CREDIT]\
            .setdefault("adjustments", {})[adj_id] \
            = dict(record)
        return record

    async def get_adjustment(
            self, adj_id: int) -> dict | None:
        """按 ID 查调额建议书"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("pay69", "adjust", adj_id))
            return json.loads(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_CREDIT]\
            .get("adjustments", {}).get(adj_id)
        return dict(rec) if rec else None

    async def list_adjustments(
            self, status: str = None,
            member_id: int = None,
            limit: int = 50) -> list[dict]:
        """调额建议书列表(倒序; 可按
        状态/会员过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.smembers(
                _k("pay69", "adjust", "index"))
            result = []
            for i in sorted(
                    (int(x) for x in ids),
                    reverse=True)[:limit * 5]:
                rec = await self.get_adjustment(i)
                if not rec:
                    continue
                if status and rec.get(
                        "status") != status:
                    continue
                if member_id is not None and rec.get(
                        "memberId") != member_id:
                    continue
                result.append(rec)
                if len(result) >= limit:
                    break
            return result
        self._ensure_store()
        recs = list(
            self.store[self.TABLE_CREDIT]
            .get("adjustments", {}).values())
        recs.sort(key=lambda r: r.get(
            "adjustmentId", 0), reverse=True)
        if status:
            recs = [r for r in recs
                    if r.get("status") == status]
        if member_id is not None:
            recs = [r for r in recs
                    if r.get("memberId")
                    == member_id]
        return [dict(r) for r in recs[:limit]]

    # ============================================================
    # 还款回流统计(pay69_repay——P3 快环)
    # ============================================================

    async def report_repayment(
            self, member_id: int,
            event_type: str) -> dict:
        """还款事件回流计数(ontime/late/
        early——HINCRBY 原子, 快环)

        Returns: 更新后的统计视图
        """
        if is_redis_mode():
            client = await get_redis_client()
            await client.hincrby(
                _k("pay69", "repay", member_id),
                event_type, 1)
            return await self.get_repay_stats(
                member_id)
        self._ensure_store()
        stats = self.store[self.TABLE_REPAY]\
            .setdefault(member_id,
                        {"ontime": 0, "late": 0,
                         "early": 0})
        stats[event_type] = \
            stats.get(event_type, 0) + 1
        return dict(stats)

    async def get_repay_stats(
            self, member_id: int) -> dict:
        """会员还款统计视图(ontime/late/
        early; 无记录全 0)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay69", "repay", member_id))
            return {k: int(v)
                    for k, v in data.items()}
        self._ensure_store()
        return dict(
            self.store[self.TABLE_REPAY]
            .get(member_id, {"ontime": 0,
                             "late": 0,
                             "early": 0}))

    # ============================================================
    # 生物验证审计(pay69_bio——P4)
    # ============================================================

    async def next_bio_seq(self) -> int:
        """生物验证事件序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay69", "bio", "seq"))
        self._ensure_store()
        self.store["_pay69_bio_seq"] = \
            self.store.get("_pay69_bio_seq", 0) + 1
        return self.store["_pay69_bio_seq"]

    async def save_bio_event(
            self, record: dict) -> dict:
        """保存生物验证事件(全局+会员)"""
        seq = await self.next_bio_seq()
        record["bioSeq"] = seq
        payload = json.dumps(
            record, ensure_ascii=False)
        mid = str(record.get("memberId") or 0)
        if is_redis_mode():
            client = await get_redis_client()
            await client.lpush(
                _k("pay69", "bio", "all"),
                payload)
            await client.ltrim(
                _k("pay69", "bio", "all"),
                0, 999)
            await client.lpush(
                _k("pay69", "bio", "member", mid),
                payload)
            await client.ltrim(
                _k("pay69", "bio", "member", mid),
                0, 199)
            return record
        self._ensure_store()
        table = self.store[self.TABLE_BIO]
        table.setdefault("all", [])\
            .insert(0, dict(record))
        del table["all"][1000:]
        table.setdefault(
            "by_member", {}
        ).setdefault(mid, [])\
            .insert(0, dict(record))
        del table["by_member"][mid][200:]
        return record

    async def list_bio_events(
            self, member_id: int = None,
            limit: int = 50) -> list[dict]:
        """生物验证事件列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            if member_id:
                data = await client.lrange(
                    _k("pay69", "bio", "member",
                       member_id), 0, limit - 1)
            else:
                data = await client.lrange(
                    _k("pay69", "bio", "all"),
                    0, limit - 1)
            return [json.loads(x) for x in data]
        self._ensure_store()
        table = self.store[self.TABLE_BIO]
        recs = (table.get("by_member", {})
                .get(str(member_id), [])
                if member_id is not None
                else table.get("all", []))
        return [dict(r) for r in recs[:limit]]

    # ---------- FIDO 挑战(短期 String) ----------

    async def save_challenge(
            self, challenge: str,
            record: dict, ttl: int = 120) -> dict:
        """保存 FIDO 挑战(TTL 秒——48号
        confirmToken 语义)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("pay69", "challenge",
                   challenge),
                json.dumps(record,
                           ensure_ascii=False),
                ex=ttl)
            return record
        self._ensure_store()
        record["expiresAt"] = (
            _now_ts() + ttl)
        self.store[self.TABLE_BIO]\
            .setdefault("challenges", {})[
            challenge] = dict(record)
        return record

    async def pop_challenge(
            self, challenge: str) -> dict | None:
        """取回并消费挑战(一次性——GETDEL
        语义防重放)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.getdel(
                _k("pay69", "challenge",
                   challenge))
            return json.loads(data) if data \
                else None
        self._ensure_store()
        store = self.store[self.TABLE_BIO]\
            .get("challenges", {})
        rec = store.pop(challenge, None)
        if not rec:
            return None
        # 内存态 TTL 模拟(过期即失效)
        if rec.get("expiresAt", 0) \
                < _now_ts():
            return None
        return rec

    # ============================================================
    # 端侧模板版本账本(pay69_templates
    # ——P4; 原始特征永不上传, 仅置信度
    # 哈希+版本计数)
    # ============================================================

    async def get_template(
            self, member_id: int,
            method: str) -> dict | None:
        """模板版本账本(无则 None)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay69", "template",
                   member_id, method))
            if not data:
                return None
            out = dict(data)
            if "version" in out:
                with contextlib.suppress(
                        TypeError, ValueError):
                    out["version"] = int(
                        float(out["version"]))
            if "confidence" in out:
                with contextlib.suppress(
                        TypeError, ValueError):
                    out["confidence"] = float(
                        out["confidence"])
            return out
        self._ensure_store()
        rec = self.store[self.TABLE_TEMPLATES]\
            .get((member_id, method))
        return dict(rec) if rec else None

    async def save_template(
            self, member_id: int,
            method: str,
            record: dict) -> dict:
        """保存模板版本账本"""
        if is_redis_mode():
            client = await get_redis_client()
            mapping = {}
            for k, v in record.items():
                if isinstance(v, bool):
                    mapping[k] = int(v)
                elif isinstance(
                        v, (int, float)):
                    mapping[k] = v
                else:
                    mapping[k] = str(v)
            await client.hset(
                _k("pay69", "template",
                   member_id, method),
                mapping=mapping)
            return record
        self._ensure_store()
        self.store[self.TABLE_TEMPLATES][
            (member_id, method)] = dict(record)
        return record

    # ============================================================
    # 情境智能码事件(pay69_smartcode——P5;
    # LIST 结构双模式同形, 商户对账
    # 聚合消费)
    # ============================================================

    async def next_code_seq(self) -> int:
        """情境码事件序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay69", "smartcode", "seq"))
        self._ensure_store()
        self.store["_pay69_code_seq"] = \
            self.store.get("_pay69_code_seq", 0) + 1
        return self.store["_pay69_code_seq"]

    async def save_smartcode(
            self, record: dict) -> dict:
        """保存情境码事件(全局+商户流)"""
        seq = await self.next_code_seq()
        record["codeSeq"] = seq
        payload = json.dumps(
            record, ensure_ascii=False)
        mch = str(record.get("merchantId")
                  or 0)
        if is_redis_mode():
            client = await get_redis_client()
            await client.lpush(
                _k("pay69", "smartcode", "all"),
                payload)
            await client.ltrim(
                _k("pay69", "smartcode", "all"),
                0, 999)
            await client.lpush(
                _k("pay69", "smartcode",
                   "mch", mch), payload)
            await client.ltrim(
                _k("pay69", "smartcode",
                   "mch", mch), 0, 499)
            return record
        self._ensure_store()
        table = self.store[self.TABLE_SMARTCODE]
        table.setdefault("all", [])\
            .insert(0, dict(record))
        del table["all"][1000:]
        table.setdefault(
            "by_merchant", {}
        ).setdefault(mch, [])\
            .insert(0, dict(record))
        del table["by_merchant"][mch][500:]
        return record

    async def list_smartcodes(
            self, merchant_id: int = None,
            limit: int = 50) -> list[dict]:
        """情境码事件列表(最新在前; nonce
        去重——状态更新重插后保留最新态)"""
        if is_redis_mode():
            client = await get_redis_client()
            if merchant_id is not None:
                data = await client.lrange(
                    _k("pay69", "smartcode",
                       "mch", merchant_id),
                    0, max(limit * 3, 200))
            else:
                data = await client.lrange(
                    _k("pay69", "smartcode",
                       "all"),
                    0, max(limit * 3, 200))
            records = [json.loads(x)
                       for x in data]
        else:
            self._ensure_store()
            table = self.store[
                self.TABLE_SMARTCODE]
            records = (
                table.get("by_merchant", {})
                .get(str(merchant_id), [])
                if merchant_id is not None
                else table.get("all", []))
            records = [dict(r)
                       for r in records]
        seen = set()
        result = []
        for r in records:
            nonce = r.get("nonce")
            if nonce:
                if nonce in seen:
                    continue
                seen.add(nonce)
            result.append(r)
            if len(result) >= limit:
                break
        return result

    async def find_smartcode_by_nonce(
            self, nonce: str) -> dict | None:
        """按 55号 nonce 查码事件(核销
        定位——全表线性扫描, 事件域≤1000)"""
        for rec in await self.list_smartcodes(
                limit=1000):
            if rec.get("nonce") == nonce:
                return rec
        return None

    async def save_baseline(
            self, member_id: int,
            record: dict) -> dict:
        """保存会员行为基线"""
        if is_redis_mode():
            client = await get_redis_client()
            mapping = {}
            for k, v in record.items():
                if isinstance(v, (int, float)):
                    mapping[k] = v
                else:
                    mapping[k] = json.dumps(
                        v, ensure_ascii=False)
            await client.hset(
                _k("pay69", "behavior", member_id),
                mapping=mapping)
            return record
        self._ensure_store()
        self.store[self.TABLE_BEHAVIOR][member_id] \
            = dict(record)
        return record

    async def update_baseline(
            self, member_id: int,
            interval_ms: float,
            typing_speed_ms: float) -> dict:
        """行为基线增量更新(快环——EMA 指数
        移动平均, 确定性; 首样本直建基线)

        EMA α=0.3(新样本权重——平滑适应
        自然漂移, 60号惯例口径)
        """
        alpha = 0.3
        existing = await self.get_baseline(
            member_id)
        if not existing \
                or not existing.get("samples"):
            record = {
                "memberId": member_id,
                "avgIntervalMs": round(
                    float(interval_ms), 1),
                "avgTypingMs": round(
                    float(typing_speed_ms), 1),
                "samples": 1,
            }
        else:
            n = int(existing.get("samples", 1))
            record = {
                "memberId": member_id,
                "avgIntervalMs": round(
                    existing.get(
                        "avgIntervalMs", 0)
                    * (1 - alpha)
                    + float(interval_ms) * alpha, 1),
                "avgTypingMs": round(
                    existing.get(
                        "avgTypingMs", 0)
                    * (1 - alpha)
                    + float(typing_speed_ms)
                    * alpha, 1),
                "samples": n + 1,
            }
        await self.save_baseline(
            member_id, record)
        return record
