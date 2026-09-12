"""71号·AI智能支付端口大模型 仓储
(pay71_repository, P0-P2)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§七 P0/P1/P2):
    10 表(前缀 pay71):
        pay71_ports       端口态观测记录
                          (三态+前兆评分)
        pay71_signals     前兆信号留痕
                          (预警观测——快环)
        pay71_events      全链事件埋点
                          (P1 自愈/P3 调配
                          共享)
        pay71_probes      半开探测记录
                          (P1——恢复证据)
        pay71_shadows     影子冷启动账本
                          (P1——纳管生命周期)
        pay71_proposals   转正/摘牌建议书
                          (P1——人工终审)
        pay71_traces      自愈编排轨迹
                          (P1——触发窗口/
                          动作/恢复证据)
        pay71_predictions 预判留痕+预载
                          建议(P2——可撤销
                          观测域)
        pay71_splits      大额拆分建议书
                          (P2——令牌单次
                          消费确认流)
        pay71_retries     重试策略快环
                          统计(P2——端口
                          失败计数)
        pay71_allocations 帕累托调配留痕
                          (P3——四维向量
                          +支配分析)
        pay71_external    外部信号建议书
                          (P3——费率/汇率/
                          政策→admin 终审)
        pay71_overrides   情境权重覆盖
                          (P3——外部信号
                          批准后激活)
        pay71_verifies    T+0 三向核验
                          留痕(P4)
        pay71_recons      差错补单
                          账本(P4——幂等
                          域自动+留痕)
        pay71_reconbase  渠道对账延迟
                          差错基线统计
                          (P4——快环)
        pay71_narratives  风控叙事留痕
                          (P5——归因字段
                          +模板插值)
        pay71_incidents   欺诈手法案例库
                          (P5——脱敏归档)
        pay71_misjudges   误拦归因留痕
                          (P5——回流观测)

69号仓储范式平移:
    - 通用读写基元(_save/_get/_list)
    - 五清单显式序列化(新增字段必须同步)
    - 71号永不写 69号表(叠加铁律)
"""

import contextlib
import json
import time

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)

# 信号留痕截断倍数(容量×2——观测上限
# 之外的余量)
_SIGNAL_WINDOW = 100


def _now_ts() -> float:
    """当前 Unix 秒(内存态 TTL 模拟)"""
    return time.time()


class Pay71Repository:
    """71号仓储(双模式——asyncio/Redis)"""

    TABLE_PORTS = "pay71_ports"
    TABLE_SIGNALS = "pay71_signals"
    TABLE_EVENTS = "pay71_events"
    TABLE_PROBES = "pay71_probes"
    TABLE_SHADOWS = "pay71_shadows"
    TABLE_PROPOSALS = "pay71_proposals"
    TABLE_TRACES = "pay71_traces"
    TABLE_PREDICTIONS = "pay71_predictions"
    TABLE_SPLITS = "pay71_splits"
    TABLE_RETRIES = "pay71_retries"
    TABLE_ALLOCATIONS = "pay71_allocations"
    TABLE_EXTERNAL = "pay71_external"
    TABLE_OVERRIDES = "pay71_overrides"
    TABLE_VERIFIES = "pay71_verifies"
    TABLE_RECONS = "pay71_recons"
    TABLE_RECONBASE = "pay71_reconbase"
    TABLE_NARRATIVES = "pay71_narratives"
    TABLE_INCIDENTS = "pay71_incidents"
    TABLE_MISJUDGES = "pay71_misjudges"

    _ALL_TABLES = (
        TABLE_PORTS, TABLE_SIGNALS,
        TABLE_EVENTS, TABLE_PROBES,
        TABLE_SHADOWS, TABLE_PROPOSALS,
        TABLE_TRACES, TABLE_PREDICTIONS,
        TABLE_SPLITS, TABLE_RETRIES,
        TABLE_ALLOCATIONS, TABLE_EXTERNAL,
        TABLE_OVERRIDES, TABLE_VERIFIES,
        TABLE_RECONS, TABLE_RECONBASE,
        TABLE_NARRATIVES, TABLE_INCIDENTS,
        TABLE_MISJUDGES)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "portSeq", "signalSeq", "eventSeq",
        "probeSeq", "shadowSeq",
        "proposalSeq", "traceSeq",
        "predictionSeq", "splitSeq",
        "allocationSeq", "externalSeq",
        "verifySeq", "reconSeq",
        "narrativeSeq", "incidentSeq",
        "misjudgeSeq",
        "sampleCount", "probeStreak",
        "probeRequired", "memberId",
        "retryCount", "retryAttempt",
        "healCount", "diffCount",
        "totalCount", "entropySeq")
    _FLOAT_FIELDS = (
        "avgLatencyMs", "errorRate",
        "successRate", "precursorScore",
        "latencyDelta", "errorRateDelta",
        "successRateDelta", "callbackMs",
        "windowScore", "daysElapsed",
        "amount", "totalAmount", "entropy",
        "weightedScore", "delta",
        "orderAmount", "flowAmount",
        "receiptAmount", "diffSeconds",
        "avgCallbackSeconds", "deviation")
    _BOOL_FIELDS = (
        "alerted", "observed", "recovered",
        "probeSuccess", "eligible",
        "revoked", "freeTier", "executed",
        "amountWithinLimit", "advisoryOnly",
        "onFront", "amountMatch",
        "orderIdMatch", "timeMatch",
        "idempotentKey", "newDevice",
        "oddHour", "newLocation",
        "redacted")
    _JSON_DICT_FIELDS = (
        "windows", "meta", "detail",
        "signals", "trigger", "evidence",
        "fromTo", "shadowCriteria",
        "preloadCheck", "reason",
        "disposition", "scores",
        "weightsUsed", "impact",
        "suggestedWeights", "front",
        "weights", "mismatch",
        "attribution", "axes",
        "entropyAxes", "deviceClues")
    _JSON_LIST_FIELDS = (
        "traits", "parts", "candidates",
        "paretoFront", "affectedPorts")

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
    # 端口态观测(pay71_ports)
    # ============================================================

    async def get_port(self, port_id: str) -> dict | None:
        """按端口 ID 查端口态记录(内存态
        存取同形——69号 get_channel 范式)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay71", "port", port_id))
            if not data:
                return None
            return self._deserialize(data)
        self._ensure_store()
        rec = self.store[self.TABLE_PORTS]\
            .get(port_id)
        return dict(rec) if rec else None

    async def save_port(self, port_id: str,
                        record: dict) -> dict:
        """保存/覆盖端口态记录"""
        record["portId"] = port_id
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "port", port_id)
            await client.hset(
                key, mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_PORTS][port_id] \
            = dict(record)
        return record

    async def list_ports(self) -> list[dict]:
        """列出全部端口态记录(未观测
        端口默认 healthy——观测面零影响)"""
        from services.pay71_registry import (
            _port_ids,
        )
        result = []
        for pid in _port_ids():
            rec = await self.get_port(pid)
            result.append(rec or {
                "portId": pid,
                "portState": "healthy",
                "alerted": False,
                "observed": False,
            })
        return result

    # ============================================================
    # 前兆信号留痕(pay71_signals)
    # ============================================================

    async def next_signal_seq(self) -> int:
        """前兆信号序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "signal", "seq"))
        self._ensure_store()
        self.store["_pay71_signal_seq"] = \
            self.store.get("_pay71_signal_seq", 0) + 1
        return self.store["_pay71_signal_seq"]

    async def save_signal(self, record: dict) -> dict:
        """保存前兆信号留痕"""
        seq = await self.next_signal_seq()
        record["signalSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k(
                "pay71", "signal", str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            await client.expire(
                key, 7 * 24 * 3600)
            return record
        self._ensure_store()
        self.store[self.TABLE_SIGNALS][str(seq)] \
            = dict(record)
        return record

    async def list_signals(
            self, limit: int = 50,
            port_id: str = None) -> list[dict]:
        """列出前兆信号留痕(近 limit 条——
        可按端口过滤, P1 滚动窗口消费)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "signal", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "signal", str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if port_id is None \
                        or rec.get(
                            "portId") == port_id:
                    result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[self.TABLE_SIGNALS]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_SIGNALS][str(s)])
            for s in seqs
        ]
        if port_id is not None:
            records = [
                r for r in records
                if r.get("portId") == port_id]
        return records[:limit]

    # ============================================================
    # 全链事件埋点(pay71_events)
    # ============================================================

    async def next_event_seq(self) -> int:
        """事件序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "event", "seq"))
        self._ensure_store()
        self.store["_pay71_event_seq"] = \
            self.store.get("_pay71_event_seq", 0) + 1
        return self.store["_pay71_event_seq"]

    async def save_event(self, event: dict) -> dict:
        """保存事件埋点(归因可审计)"""
        seq = await self.next_event_seq()
        event["eventSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k(
                "pay71", "event", str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(event),
                    ensure_ascii=False))
            return event
        self._ensure_store()
        self.store[self.TABLE_EVENTS][str(seq)] \
            = dict(event)
        return event

    async def list_events(
            self, limit: int = 50) -> list[dict]:
        """列出事件埋痕(近 limit 条)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "event", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs[:limit]:
                raw = await client.get(
                    _k("pay71", "event", str(seq)))
                if raw:
                    with contextlib.suppress(
                            ValueError, TypeError):
                        result.append(
                            self._deserialize(
                                json.loads(raw)))
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[self.TABLE_EVENTS]),
            reverse=True)
        return [
            dict(self.store[
                self.TABLE_EVENTS][str(s)])
            for s in seqs[:limit]
        ]

    # ============================================================
    # 半开探测记录(pay71_probes——P1)
    # ============================================================

    async def next_probe_seq(self) -> int:
        """探针序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "probe", "seq"))
        self._ensure_store()
        self.store["_pay71_probe_seq"] = \
            self.store.get("_pay71_probe_seq", 0) + 1
        return self.store["_pay71_probe_seq"]

    async def save_probe(self, record: dict) -> dict:
        """保存探针记录(恢复证据链)"""
        seq = await self.next_probe_seq()
        record["probeSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "probe", str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_PROBES][str(seq)] \
            = dict(record)
        return record

    async def list_probes(
            self, port_id: str = None,
            limit: int = 50) -> list[dict]:
        """列出探针记录(可按端口过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "probe", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "probe", str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if port_id is None \
                        or rec.get(
                            "portId") == port_id:
                    result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[self.TABLE_PROBES]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_PROBES][str(s)])
            for s in seqs
        ]
        if port_id is not None:
            records = [
                r for r in records
                if r.get("portId") == port_id]
        return records[:limit]

    # ============================================================
    # 影子冷启动账本(pay71_shadows——P1)
    # ============================================================

    async def get_shadow(self, port_id: str) -> dict | None:
        """按端口查影子账本(内存态存取同形)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay71", "shadow", port_id))
            if not data:
                return None
            return self._deserialize(data)
        self._ensure_store()
        rec = self.store[self.TABLE_SHADOWS]\
            .get(port_id)
        return dict(rec) if rec else None

    async def save_shadow(self, port_id: str,
                         record: dict) -> dict:
        """保存/覆盖影子账本"""
        record["portId"] = port_id
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "shadow", port_id)
            await client.hset(
                key, mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_SHADOWS][port_id] \
            = dict(record)
        return record

    async def list_shadows(self) -> list[dict]:
        """列出全部影子账本(纳管端口)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "shadow", "*"))
            result = []
            for key in keys:
                data = await client.hgetall(key)
                if data:
                    result.append(
                        self._deserialize(data))
            return result
        self._ensure_store()
        return [
            dict(rec) for rec in
            self.store[self.TABLE_SHADOWS].values()
        ]

    # ============================================================
    # 转正/摘牌建议书(pay71_proposals——P1)
    # ============================================================

    async def next_proposal_seq(self) -> int:
        """建议书序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "proposal", "seq"))
        self._ensure_store()
        self.store["_pay71_proposal_seq"] = \
            self.store.get("_pay71_proposal_seq", 0) + 1
        return self.store["_pay71_proposal_seq"]

    async def save_proposal(self,
                            record: dict) -> dict:
        """保存建议书"""
        seq = await self.next_proposal_seq()
        record["proposalSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "proposal",
                     str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_PROPOSALS][str(seq)] \
            = dict(record)
        return record

    async def get_proposal(
            self, proposal_seq: int) -> dict | None:
        """按序号查建议书"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                _k("pay71", "proposal",
                    str(proposal_seq)))
            if not raw:
                return None
            with contextlib.suppress(
                    ValueError, TypeError):
                return self._deserialize(
                    json.loads(raw))
            return None
        self._ensure_store()
        rec = self.store[self.TABLE_PROPOSALS]\
            .get(str(proposal_seq))
        return dict(rec) if rec else None

    async def update_proposal(
            self, proposal_seq: int,
            record: dict) -> dict:
        """更新建议书(终审留痕)"""
        record["proposalSeq"] = int(proposal_seq)
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "proposal",
                     str(proposal_seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_PROPOSALS][
            str(proposal_seq)] = dict(record)
        return record

    async def list_proposals(
            self, port_id: str = None,
            limit: int = 50) -> list[dict]:
        """列出建议书(可按端口过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "proposal", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "proposal",
                        str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if port_id is None \
                        or rec.get(
                            "portId") == port_id:
                    result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[self.TABLE_PROPOSALS]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_PROPOSALS][str(s)])
            for s in seqs
        ]
        if port_id is not None:
            records = [
                r for r in records
                if r.get("portId") == port_id]
        return records[:limit]

    # ============================================================
    # 自愈编排轨迹(pay71_traces——P1 全留痕)
    # ============================================================

    async def next_trace_seq(self) -> int:
        """轨迹序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "trace", "seq"))
        self._ensure_store()
        self.store["_pay71_trace_seq"] = \
            self.store.get("_pay71_trace_seq", 0) + 1
        return self.store["_pay71_trace_seq"]

    async def save_trace(self, record: dict) -> dict:
        """保存自愈轨迹(触发窗口/动作/
        恢复证据三要素全留痕)"""
        seq = await self.next_trace_seq()
        record["traceSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "trace", str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_TRACES][str(seq)] \
            = dict(record)
        return record

    async def list_traces(
            self, port_id: str = None,
            limit: int = 50) -> list[dict]:
        """列出自愈轨迹(可按端口过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "trace", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "trace", str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if port_id is None \
                        or rec.get(
                            "portId") == port_id:
                    result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[self.TABLE_TRACES]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_TRACES][str(s)])
            for s in seqs
        ]
        if port_id is not None:
            records = [
                r for r in records
                if r.get("portId") == port_id]
        return records[:limit]

    # ============================================================
    # 预判留痕+预载建议(pay71_predictions——P2)
    # ============================================================

    async def next_prediction_seq(self) -> int:
        """预判序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "prediction", "seq"))
        self._ensure_store()
        self.store["_pay71_prediction_seq"] = \
            self.store.get(
                "_pay71_prediction_seq", 0) + 1
        return self.store[
            "_pay71_prediction_seq"]

    async def save_prediction(self,
                             record: dict) -> dict:
        """保存预判留痕(预载建议)"""
        seq = await self.next_prediction_seq()
        record["predictionSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "prediction",
                     str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_PREDICTIONS][
            str(seq)] = dict(record)
        return record

    async def get_prediction(
            self, prediction_seq: int) -> dict | None:
        """按序号查预判记录"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                _k("pay71", "prediction",
                    str(prediction_seq)))
            if not raw:
                return None
            with contextlib.suppress(
                    ValueError, TypeError):
                return self._deserialize(
                    json.loads(raw))
            return None
        self._ensure_store()
        rec = self.store[
            self.TABLE_PREDICTIONS].get(
            str(prediction_seq))
        return dict(rec) if rec else None

    async def update_prediction(
            self, prediction_seq: int,
            record: dict) -> dict:
        """更新预判记录(撤销留痕)"""
        record["predictionSeq"] = \
            int(prediction_seq)
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "prediction",
                     str(prediction_seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_PREDICTIONS][
            str(prediction_seq)] = \
            dict(record)
        return record

    async def list_predictions(
            self, member_id: int = None,
            limit: int = 50) -> list[dict]:
        """列出预判留痕(可按会员过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "prediction", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "prediction",
                        str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if member_id is None \
                        or rec.get(
                            "memberId") \
                        == int(member_id):
                    result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[
                 self.TABLE_PREDICTIONS]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_PREDICTIONS][str(s)])
            for s in seqs
        ]
        if member_id is not None:
            records = [
                r for r in records
                if r.get("memberId")
                == int(member_id)]
        return records[:limit]

    # ============================================================
    # 大额拆分建议书(pay71_splits——P2)
    # ============================================================

    async def next_split_seq(self) -> int:
        """拆分建议书序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "split", "seq"))
        self._ensure_store()
        self.store["_pay71_split_seq"] = \
            self.store.get(
                "_pay71_split_seq", 0) + 1
        return self.store["_pay71_split_seq"]

    async def save_split(self,
                         record: dict) -> dict:
        """保存拆分建议书"""
        seq = await self.next_split_seq()
        record["splitSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "split", str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_SPLITS][
            str(seq)] = dict(record)
        return record

    async def get_split(
            self, split_seq: int) -> dict | None:
        """按序号查拆分建议书"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                _k("pay71", "split",
                    str(split_seq)))
            if not raw:
                return None
            with contextlib.suppress(
                    ValueError, TypeError):
                return self._deserialize(
                    json.loads(raw))
            return None
        self._ensure_store()
        rec = self.store[self.TABLE_SPLITS]\
            .get(str(split_seq))
        return dict(rec) if rec else None

    async def update_split(self, split_seq: int,
                          record: dict) -> dict:
        """更新拆分建议书(确认/拒绝留痕)"""
        record["splitSeq"] = int(split_seq)
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "split",
                     str(split_seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_SPLITS][
            str(split_seq)] = dict(record)
        return record

    async def list_splits(
            self, member_id: int = None,
            limit: int = 50) -> list[dict]:
        """列出拆分建议书(可按会员过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "split", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "split",
                        str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if member_id is None \
                        or rec.get(
                            "memberId") \
                        == int(member_id):
                    result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[self.TABLE_SPLITS]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_SPLITS][str(s)])
            for s in seqs
        ]
        if member_id is not None:
            records = [
                r for r in records
                if r.get("memberId")
                == int(member_id)]
        return records[:limit]

    # ============================================================
    # 重试策略快环统计(pay71_retries——P2)
    # ============================================================

    async def bump_retry(
            self, port_id: str,
            fail_count: int = 1) -> int:
        """端口失败计数累加(快环观测——
        HINCRBY 原子)"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.hincrby(
                _k("pay71", "retries"),
                port_id, int(fail_count))
        self._ensure_store()
        self.store[self.TABLE_RETRIES][
            port_id] = \
            self.store[self.TABLE_RETRIES].get(
                port_id, 0) + int(fail_count)
        return self.store[
            self.TABLE_RETRIES][port_id]

    async def get_retries(self) -> dict:
        """端口失败计数视图(快环统计)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay71", "retries"))
            return {k: int(v)
                    for k, v in data.items()}
        self._ensure_store()
        return {
            k: int(v) for k, v in
            dict(self.store[
                self.TABLE_RETRIES]).items()
        }

    # ============================================================
    # 帕累托调配留痕(pay71_allocations——P3)
    # ============================================================

    async def next_allocation_seq(self) -> int:
        """调配序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "allocation", "seq"))
        self._ensure_store()
        self.store["_pay71_allocation_seq"] = \
            self.store.get(
                "_pay71_allocation_seq", 0) + 1
        return self.store[
            "_pay71_allocation_seq"]

    async def save_allocation(self,
                              record: dict) -> dict:
        """保存调配留痕(四维向量+支配
        分析+情境选解全留痕)"""
        seq = await self.next_allocation_seq()
        record["allocationSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "allocation",
                     str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_ALLOCATIONS][
            str(seq)] = dict(record)
        return record

    async def list_allocations(
            self, context: str = None,
            limit: int = 50) -> list[dict]:
        """列出调配留痕(可按情境过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "allocation", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "allocation",
                        str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if context is None \
                        or rec.get(
                            "context") == context:
                    result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[
                 self.TABLE_ALLOCATIONS]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_ALLOCATIONS][str(s)])
            for s in seqs
        ]
        if context is not None:
            records = [
                r for r in records
                if r.get("context") == context]
        return records[:limit]

    # ============================================================
    # 外部信号建议书(pay71_external——P3)
    # ============================================================

    async def next_external_seq(self) -> int:
        """外部信号序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "external", "seq"))
        self._ensure_store()
        self.store["_pay71_external_seq"] = \
            self.store.get(
                "_pay71_external_seq", 0) + 1
        return self.store["_pay71_external_seq"]

    async def save_external(self,
                            record: dict) -> dict:
        """保存外部信号建议书"""
        seq = await self.next_external_seq()
        record["externalSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "external",
                     str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_EXTERNAL][
            str(seq)] = dict(record)
        return record

    async def get_external(
            self, external_seq: int) -> dict | None:
        """按序号查外部信号建议书"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                _k("pay71", "external",
                    str(external_seq)))
            if not raw:
                return None
            with contextlib.suppress(
                    ValueError, TypeError):
                return self._deserialize(
                    json.loads(raw))
            return None
        self._ensure_store()
        rec = self.store[self.TABLE_EXTERNAL]\
            .get(str(external_seq))
        return dict(rec) if rec else None

    async def update_external(
            self, external_seq: int,
            record: dict) -> dict:
        """更新外部信号建议书(终审留痕)"""
        record["externalSeq"] = \
            int(external_seq)
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "external",
                     str(external_seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_EXTERNAL][
            str(external_seq)] = dict(record)
        return record

    async def list_external(
            self, context: str = None,
            limit: int = 50) -> list[dict]:
        """列出外部信号建议书(可按情境过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "external", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "external",
                        str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if context is None \
                        or rec.get(
                            "context") == context:
                    result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[self.TABLE_EXTERNAL]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_EXTERNAL][str(s)])
            for s in seqs
        ]
        if context is not None:
            records = [
                r for r in records
                if r.get("context") == context]
        return records[:limit]

    # ============================================================
    # 情境权重覆盖(pay71_overrides——P3)
    # ============================================================

    async def get_override(
            self, context: str) -> dict | None:
        """按情境查权重覆盖(内存态存取
        同形)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay71", "override", context))
            if not data:
                return None
            return self._deserialize(data)
        self._ensure_store()
        rec = self.store[self.TABLE_OVERRIDES]\
            .get(context)
        return dict(rec) if rec else None

    async def save_override(self, context: str,
                           record: dict) -> dict:
        """保存/覆盖情境权重(外部信号
        批准后激活; 回滚=删除覆盖回出厂)"""
        record["context"] = context
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "override",
                     context)
            await client.hset(
                key, mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_OVERRIDES][
            context] = dict(record)
        return record

    async def delete_override(
            self, context: str) -> bool:
        """删除情境权重覆盖(回滚至出厂)"""
        if is_redis_mode():
            client = await get_redis_client()
            deleted = await client.delete(
                _k("pay71", "override", context))
            return bool(deleted)
        self._ensure_store()
        existed = context in \
            self.store[self.TABLE_OVERRIDES]
        self.store[self.TABLE_OVERRIDES].pop(
            context, None)
        return existed

    async def list_overrides(self) -> list[dict]:
        """列出全部激活的权重覆盖"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "override", "*"))
            result = []
            for key in keys:
                data = await client.hgetall(key)
                if data:
                    result.append(
                        self._deserialize(data))
            return result
        self._ensure_store()
        return [
            dict(rec) for rec in
            self.store[
                self.TABLE_OVERRIDES].values()
        ]

    # ============================================================
    # T+0 三向核验留痕(pay71_verifies——P4)
    # ============================================================

    async def next_verify_seq(self) -> int:
        """核验序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "verify", "seq"))
        self._ensure_store()
        self.store["_pay71_verify_seq"] = \
            self.store.get(
                "_pay71_verify_seq", 0) + 1
        return self.store["_pay71_verify_seq"]

    async def save_verify(self,
                          record: dict) -> dict:
        """保存核验留痕(T+0 三向)"""
        seq = await self.next_verify_seq()
        record["verifySeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "verify", str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_VERIFIES][
            str(seq)] = dict(record)
        return record

    async def get_verify(
            self, verify_seq: int) -> dict | None:
        """按序号查核验留痕"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                _k("pay71", "verify",
                    str(verify_seq)))
            if not raw:
                return None
            with contextlib.suppress(
                    ValueError, TypeError):
                return self._deserialize(
                    json.loads(raw))
            return None
        self._ensure_store()
        rec = self.store[self.TABLE_VERIFIES]\
            .get(str(verify_seq))
        return dict(rec) if rec else None

    async def list_verifies(
            self, state: str = None,
            limit: int = 50) -> list[dict]:
        """列出核验留痕(可按结果过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "verify", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "verify", str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if state is None \
                        or rec.get("state") == state:
                    result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[self.TABLE_VERIFIES]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_VERIFIES][str(s)])
            for s in seqs
        ]
        if state is not None:
            records = [
                r for r in records
                if r.get("state") == state]
        return records[:limit]

    # ============================================================
    # 差错补单账本(pay71_recons——P4)
    # ============================================================

    async def next_recon_seq(self) -> int:
        """补单序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "recon", "seq"))
        self._ensure_store()
        self.store["_pay71_recon_seq"] = \
            self.store.get(
                "_pay71_recon_seq", 0) + 1
        return self.store["_pay71_recon_seq"]

    async def save_recon(self,
                         record: dict) -> dict:
        """保存补单记录(幂等域自动+留痕)"""
        seq = await self.next_recon_seq()
        record["reconSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "recon", str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_RECONS][
            str(seq)] = dict(record)
        return record

    async def get_recon(
            self, recon_seq: int) -> dict | None:
        """按序号查补单记录"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                _k("pay71", "recon",
                    str(recon_seq)))
            if not raw:
                return None
            with contextlib.suppress(
                    ValueError, TypeError):
                return self._deserialize(
                    json.loads(raw))
            return None
        self._ensure_store()
        rec = self.store[self.TABLE_RECONS]\
            .get(str(recon_seq))
        return dict(rec) if rec else None

    async def update_recon(self, recon_seq: int,
                          record: dict) -> dict:
        """更新补单记录(重试/转人工留痕)"""
        record["reconSeq"] = int(recon_seq)
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "recon",
                     str(recon_seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_RECONS][
            str(recon_seq)] = dict(record)
        return record

    async def list_recons(
            self, state: str = None,
            limit: int = 50) -> list[dict]:
        """列出补单记录(可按状态过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "recon", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "recon", str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if state is None \
                        or rec.get("state") == state:
                    result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[self.TABLE_RECONS]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_RECONS][str(s)])
            for s in seqs
        ]
        if state is not None:
            records = [
                r for r in records
                if r.get("state") == state]
        return records[:limit]

    # ============================================================
    # 渠道对账延迟差错基线(pay71_reconbase
    # ——P4 快环统计)
    # ============================================================

    async def bump_recon_stat(
            self, port_id: str,
            field: str,
            value: float = 1) -> float:
        """渠道核验统计累加(healCount/
        diffCount/totalCount/回调秒
        ——HINCRBYFLOAT 原子)"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.hincrbyfloat(
                _k("pay71", "reconbase",
                   port_id),
                field, float(value))
        self._ensure_store()
        stats = self.store[
            self.TABLE_RECONBASE]\
            .setdefault(port_id, {})
        stats[field] = round(
            stats.get(field, 0)
            + float(value), 4)
        return stats[field]

    async def get_recon_stats(
            self, port_id: str) -> dict:
        """渠道核验统计视图(单端口)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay71", "reconbase",
                   port_id))
            out = {}
            for k, v in data.items():
                with contextlib.suppress(
                        ValueError, TypeError):
                    out[k] = float(v)
            return out
        self._ensure_store()
        return dict(
            self.store[
                self.TABLE_RECONBASE].get(
                port_id, {}))

    async def list_recon_stats(self) -> dict:
        """全部渠道核验统计视图"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "reconbase", "*"))
            result = {}
            for key in keys:
                pid = key.split(":")[-1]
                data = await client.hgetall(key)
                stats = {}
                for k, v in data.items():
                    with contextlib.suppress(
                            ValueError, TypeError):
                        stats[k] = float(v)
                result[pid] = stats
            return result
        self._ensure_store()
        return {
            pid: dict(stats) for pid, stats
            in self.store[
                self.TABLE_RECONBASE].items()
        }

    # ============================================================
    # 风控叙事留痕(pay71_narratives——P5)
    # ============================================================

    async def next_narrative_seq(self) -> int:
        """叙事序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "narrative", "seq"))
        self._ensure_store()
        self.store["_pay71_narrative_seq"] = \
            self.store.get(
                "_pay71_narrative_seq", 0) + 1
        return self.store[
            "_pay71_narrative_seq"]

    async def save_narrative(self,
                            record: dict) -> dict:
        """保存叙事留痕(归因字段+模板
        插值文本)"""
        seq = await self.next_narrative_seq()
        record["narrativeSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "narrative",
                     str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_NARRATIVES][
            str(seq)] = dict(record)
        return record

    async def list_narratives(
            self, member_id: int = None,
            limit: int = 50) -> list[dict]:
        """列出叙事留痕(可按会员过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "narrative", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "narrative",
                        str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if member_id is None \
                        or rec.get(
                            "memberId") \
                        == int(member_id):
                    result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[
                 self.TABLE_NARRATIVES]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_NARRATIVES][str(s)])
            for s in seqs
        ]
        if member_id is not None:
            records = [
                r for r in records
                if r.get("memberId")
                == int(member_id)]
        return records[:limit]

    # ============================================================
    # 欺诈手法案例库(pay71_incidents——P5)
    # ============================================================

    async def next_incident_seq(self) -> int:
        """案例序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "incident", "seq"))
        self._ensure_store()
        self.store["_pay71_incident_seq"] = \
            self.store.get(
                "_pay71_incident_seq", 0) + 1
        return self.store[
            "_pay71_incident_seq"]

    async def save_incident(self,
                            record: dict) -> dict:
        """保存欺诈案例(脱敏归档)"""
        seq = await self.next_incident_seq()
        record["incidentSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "incident",
                     str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_INCIDENTS][
            str(seq)] = dict(record)
        return record

    async def get_incident(
            self, incident_seq: int) -> dict | None:
        """按序号查案例"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                _k("pay71", "incident",
                    str(incident_seq)))
            if not raw:
                return None
            with contextlib.suppress(
                    ValueError, TypeError):
                return self._deserialize(
                    json.loads(raw))
            return None
        self._ensure_store()
        rec = self.store[
            self.TABLE_INCIDENTS].get(
            str(incident_seq))
        return dict(rec) if rec else None

    async def update_incident(
            self, incident_seq: int,
            record: dict) -> dict:
        """更新案例(核实状态流转留痕)"""
        record["incidentSeq"] = \
            int(incident_seq)
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "incident",
                     str(incident_seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_INCIDENTS][
            str(incident_seq)] = \
            dict(record)
        return record

    async def list_incidents(
            self, pattern: str = None,
            state: str = None,
            limit: int = 50) -> list[dict]:
        """列出案例(可按手法/状态过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "incident", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "incident",
                        str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if pattern is not None \
                        and rec.get(
                            "pattern") != pattern:
                    continue
                if state is not None \
                        and rec.get(
                            "state") != state:
                    continue
                result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[
                 self.TABLE_INCIDENTS]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_INCIDENTS][str(s)])
            for s in seqs
        ]
        if pattern is not None:
            records = [
                r for r in records
                if r.get("pattern") == pattern]
        if state is not None:
            records = [
                r for r in records
                if r.get("state") == state]
        return records[:limit]

    # ============================================================
    # 误拦归因留痕(pay71_misjudges——P5)
    # ============================================================

    async def next_misjudge_seq(self) -> int:
        """误拦序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "misjudge", "seq"))
        self._ensure_store()
        self.store["_pay71_misjudge_seq"] = \
            self.store.get(
                "_pay71_misjudge_seq", 0) + 1
        return self.store[
            "_pay71_misjudge_seq"]

    async def save_misjudge(self,
                            record: dict) -> dict:
        """保存误拦归因留痕(回流观测)"""
        seq = await self.next_misjudge_seq()
        record["misjudgeSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "misjudge",
                     str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_MISJUDGES][
            str(seq)] = dict(record)
        return record

    async def list_misjudges(
            self, kind: str = None,
            limit: int = 50) -> list[dict]:
        """列出误拦留痕(可按归因类型过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "misjudge", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs:
                if len(result) >= limit:
                    break
                raw = await client.get(
                    _k("pay71", "misjudge",
                        str(seq)))
                if not raw:
                    continue
                try:
                    rec = self._deserialize(
                        json.loads(raw))
                except (ValueError, TypeError):
                    continue
                if kind is None \
                        or rec.get("kind") == kind:
                    result.append(rec)
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[
                 self.TABLE_MISJUDGES]),
            reverse=True)
        records = [
            dict(self.store[
                self.TABLE_MISJUDGES][str(s)])
            for s in seqs
        ]
        if kind is not None:
            records = [
                r for r in records
                if r.get("kind") == kind]
        return records[:limit]
