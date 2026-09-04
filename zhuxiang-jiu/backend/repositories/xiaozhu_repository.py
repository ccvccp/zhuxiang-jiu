"""48号·小竹智能语音中枢数据访问层(双模式: 内存 + Redis)

表清单(前缀 voice48, 计划 §四 P0/§五 P1/§七 P3/§八 P4/
49号 §六 P0):
    voice48_sessions   会话(sessionId 键; memberId 归属)
    voice48_turns      轮次(sessionId 下的 seq 键; 时间正序)
    voice48_bindings   会员↔信值档案绑定(P1)
    voice48_points     语音积分账本(P3; ledgerId 键, 只追加)
    voice48_points_acc 会员积分余额(P3; memberId 自然键)
    voice48_failures   失败案例池(P3; caseId 键, 只追加)
    voice48_custom_cmds 共创指令(P3; cmdId 键, 审核流)
    voice48_proactive  主动关怀任务(P3; taskId 键)
    voice48_fc_audit   FC 调用审计流水(49号P0; fcId 键,
                      只追加——六字段铁律)
    voice48_privacy_budget 会员隐私预算(49号P2;
                      memberId 自然键——日预算/偏好/日切重置)

积分账本结构(P3, 计划 §七 ①——独立账本不直改信值):
    {ledgerId, memberId, kind(command_done|feedback_ad
     opted|custom_accepted|redeem), points(±), balance
     After, refId, note, ts}

失败案例(P3, 计划 §七 ③):
    {caseId, sessionId, memberId, rawText(脱敏), kind(
     fallback|repeat|negative), ts}

共创指令(P3, 计划 §七 ④):
    {cmdId, memberId, phrase, action(白名单), status(
     pending|approved|rejected), reviewedAt, note, ts}

关怀任务(P3, 计划 §七 ②):
    {taskId, memberId, kind(repair_window), payload(
     JSON), status(pending|sent), sentAt, ts}

设计对齐(43-47号惯例):
    - 双模式存储 + 显式序列化口径(bool→0/1, dict/list→
      JSON 字符串, None→"")
    - 积分账本只追加; 会话清除级联轮次(隐私红线)
"""

import json

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)


class Xiaozhu48Repository:
    """小竹会话仓储(双模式, 45号仓储范式平移)"""

    TABLE_SESSIONS = "voice48_sessions"
    TABLE_TURNS = "voice48_turns"
    TABLE_BINDINGS = "voice48_bindings"
    TABLE_POINTS = "voice48_points"
    TABLE_POINTS_ACC = "voice48_points_acc"
    TABLE_FAILURES = "voice48_failures"
    TABLE_CUSTOM = "voice48_custom_cmds"
    TABLE_PROACTIVE = "voice48_proactive"
    TABLE_FC_AUDIT = "voice48_fc_audit"
    TABLE_PRIVACY = "voice48_privacy_budget"

    _INT_FIELDS = ("memberId", "seq", "trustId", "ledgerId",
                   "caseId", "cmdId", "taskId", "fcId",
                   "sessionId")
    _FLOAT_FIELDS = ("latencyMs", "points", "balance",
                     "privacyCost", "dailyBudget",
                     "usedToday")

    def __init__(self):
        self.store = get_in_memory_store()

    def _ensure_store(self):
        for t in (self.TABLE_SESSIONS, self.TABLE_TURNS,
                  self.TABLE_BINDINGS, self.TABLE_POINTS,
                  self.TABLE_POINTS_ACC,
                  self.TABLE_FAILURES, self.TABLE_CUSTOM,
                  self.TABLE_PROACTIVE,
                  self.TABLE_FC_AUDIT,
                  self.TABLE_PRIVACY):
            self.store.setdefault(t, {})

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                out[k] = ""
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    @staticmethod
    def _deserialize(data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in Xiaozhu48Repository._INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in Xiaozhu48Repository._FLOAT_FIELDS:
                try:
                    record[k] = float(v) if v != "" else 0.0
                except (TypeError, ValueError):
                    record[k] = 0.0
            elif k in ("audioMeta", "card", "payload"):
                try:
                    record[k] = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    record[k] = {}
            elif k == "history":
                # 49号P2: 隐私预算近 7 日消耗(list)
                try:
                    parsed = json.loads(v) if v else []
                    record[k] = (parsed
                                 if isinstance(parsed, list)
                                 else [])
                except (TypeError, ValueError):
                    record[k] = []
            elif k == "wake":
                record[k] = v in (1, "1", True, "True", "true")
            else:
                record[k] = v
        return record

    # --------------------------------------------------------
    # 绑定(P1: 会员 ↔ 信值档案, 两套 ID 体系衔接)
    # --------------------------------------------------------

    async def get_binding(self,
                          member_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("voice48", self.TABLE_BINDINGS, member_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_BINDINGS].get(member_id)
        return dict(rec) if rec else None

    async def save_binding(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("voice48", self.TABLE_BINDINGS,
                   record["memberId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_BINDINGS][
            record["memberId"]] = dict(record)
        return record

    async def delete_binding(self,
                             member_id: int) -> bool:
        if is_redis_mode():
            client = await get_redis_client()
            return bool(await client.delete(_k(
                "voice48", self.TABLE_BINDINGS, member_id)))
        self._ensure_store()
        return self.store[
            self.TABLE_BINDINGS].pop(member_id,
                                     None) is not None

    # --------------------------------------------------------
    # 49号P2 隐私预算(memberId 自然键——日切重置)
    # --------------------------------------------------------

    async def get_privacy_budget(self,
                                  member_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k(
                "voice48", self.TABLE_PRIVACY, member_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_PRIVACY].get(member_id)
        return dict(rec) if rec else None

    async def save_privacy_budget(self,
                                  record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("voice48", self.TABLE_PRIVACY,
                   record["memberId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_PRIVACY][
            record["memberId"]] = dict(record)
        return record

    # --------------------------------------------------------
    # 会话
    # --------------------------------------------------------

    async def next_session_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("voice48", "sessions", "seq"))
        self._ensure_store()
        seq = self.store.get("_voice48_sessions_seq", 0) + 1
        self.store["_voice48_sessions_seq"] = seq
        return seq

    async def save_session(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("voice48", self.TABLE_SESSIONS,
                   record["sessionId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_SESSIONS][
            record["sessionId"]] = dict(record)
        return record

    async def get_session(self,
                          session_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("voice48", self.TABLE_SESSIONS, session_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_SESSIONS].get(session_id)
        return dict(rec) if rec else None

    async def delete_session(self,
                             session_id: int) -> int:
        """删除会话并级联清除全部轮次(隐私一键清除红线)"""
        removed = 0
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "voice48", self.TABLE_TURNS, session_id, "*"))
            pipe = client.pipeline(transaction=False)
            for k in keys:
                pipe.delete(k)
            if keys:
                await pipe.execute()
            removed = len(keys) + int(
                await client.delete(_k(
                    "voice48", self.TABLE_SESSIONS, session_id)))
            return removed
        self._ensure_store()
        turns = self.store[self.TABLE_TURNS]
        for k in [k for k in list(turns)
                  if k[0] == session_id]:
            del turns[k]
            removed += 1
        if self.store[self.TABLE_SESSIONS].pop(
                session_id, None) is not None:
            removed += 1
        return removed

    # --------------------------------------------------------
    # 轮次
    # --------------------------------------------------------

    async def next_turn_seq(self, session_id: int) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k(
                "voice48", "turns", session_id, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_voice48_turns_{session_id}_seq", 0) + 1
        self.store[f"_voice48_turns_{session_id}_seq"] = seq
        return seq

    async def save_turn(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("voice48", self.TABLE_TURNS,
                   record["sessionId"], record["seq"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_TURNS][
            (record["sessionId"], record["seq"])] = dict(record)
        return record

    async def list_turns(self, session_id: int,
                         limit: int = 50) -> list[dict]:
        """会话轮次(按 seq 正序——指代消解上下文窗口)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "voice48", self.TABLE_TURNS, session_id, "*"))
            keys = [k for k in keys
                    if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        result.append(self._deserialize(data))
            result.sort(key=lambda t: (
                t.get("seq") or 0))
            return result[:limit]
        self._ensure_store()
        result = [dict(t) for t in
                  self.store[self.TABLE_TURNS].values()
                  if t.get("sessionId") == session_id]
        result.sort(key=lambda t: (t.get("seq") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # P4 看板聚合扫描(全表只读——六区块数据源)
    # --------------------------------------------------------

    async def scan_sessions(self,
                            limit: int = 2000) -> list[dict]:
        """全量会话(时间正序——使用总览/公平性桥接取
        memberId 维度)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "voice48", self.TABLE_SESSIONS, "*"))
            keys = [k for k in keys
                    if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
            result.sort(key=lambda s: (
                s.get("sessionId") or 0))
            return result[:limit]
        self._ensure_store()
        result = [dict(s) for s in
                  self.store[self.TABLE_SESSIONS].values()]
        result.sort(key=lambda s: (
            s.get("sessionId") or 0))
        return result[:limit]

    async def scan_turns(self,
                         limit: int = 10000) -> list[dict]:
        """全量轮次(跨会话——指令命中排行/直达率/通道比;
        rawText 已 PII 脱敏, 看板仅取聚合字段)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "voice48", self.TABLE_TURNS, "*", "*"))
            keys = [k for k in keys
                    if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
            result.sort(key=lambda t: (
                t.get("sessionId") or 0,
                t.get("seq") or 0))
            return result[:limit]
        self._ensure_store()
        result = [dict(t) for t in
                  self.store[self.TABLE_TURNS].values()]
        result.sort(key=lambda t: (
            t.get("sessionId") or 0,
            t.get("seq") or 0))
        return result[:limit]

    async def points_balances_total(self) -> dict:
        """会员积分余额合计(发放-兑换净额口径)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "voice48", self.TABLE_POINTS_ACC, "*"))
            total = 0.0
            holders = 0
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.get(k)
                for v in await pipe.execute():
                    if v is None or v == "":
                        continue
                    try:
                        total += float(v)
                        holders += 1
                    except (TypeError, ValueError):
                        continue
            return {"balanceTotal": round(total, 1),
                    "holders": holders}
        self._ensure_store()
        values = [v for v in
                  self.store[self.TABLE_POINTS_ACC].values()
                  if v not in ("", None)]
        total = 0.0
        for v in values:
            try:
                total += float(v)
            except (TypeError, ValueError):
                continue
        return {"balanceTotal": round(total, 1),
                "holders": len(values)}

    # --------------------------------------------------------
    # P3 积分账本(独立于信值——入信值走 deposit 验真通道)
    # --------------------------------------------------------

    async def next_ledger_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("voice48", "points", "seq"))
        self._ensure_store()
        seq = self.store.get("_voice48_points_seq", 0) + 1
        self.store["_voice48_points_seq"] = seq
        return seq

    async def points_balance(self,
                             member_id: int) -> float:
        if is_redis_mode():
            client = await get_redis_client()
            v = await client.get(_k(
                "voice48", self.TABLE_POINTS_ACC, member_id))
            return float(v or 0.0)
        self._ensure_store()
        return float(self.store[
            self.TABLE_POINTS_ACC].get(member_id, 0.0))

    async def add_points(self, record: dict) -> dict:
        """计分入账(账本只追加 + 余额原子更新)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("voice48", self.TABLE_POINTS,
                   record["ledgerId"]),
                mapping=self._serialize(record))
            await client.set(
                _k("voice48", self.TABLE_POINTS_ACC,
                   record["memberId"]),
                str(record["balanceAfter"]))
            return record
        self._ensure_store()
        self.store[self.TABLE_POINTS][
            record["ledgerId"]] = dict(record)
        self.store[self.TABLE_POINTS_ACC][
            record["memberId"]] = record["balanceAfter"]
        return record

    async def list_points(self, member_id: int,
                          limit: int = 50) -> list[dict]:
        """会员积分流水(时间倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "voice48", self.TABLE_POINTS, "*"))
            keys = [k for k in keys
                    if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        ev = self._deserialize(data)
                        if ev.get("memberId") == member_id:
                            result.append(ev)
            result.sort(key=lambda e: -(
                e.get("ledgerId") or 0))
            return result[:limit]
        self._ensure_store()
        result = [dict(r) for r in
                  self.store[self.TABLE_POINTS].values()
                  if r.get("memberId") == member_id]
        result.sort(key=lambda e: -(
            e.get("ledgerId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # P3 失败案例池 / 共创指令 / 关怀任务
    # --------------------------------------------------------

    async def _next_id(self, table: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("voice48", table, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_voice48_{table}_seq", 0) + 1
        self.store[f"_voice48_{table}_seq"] = seq
        return seq

    async def save_record(self, table: str,
                          record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("voice48", table,
                   list(record.values())[0]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        key = list(record.values())[0]
        self.store[table][key] = dict(record)
        return record

    async def list_records(self, table: str,
                           field: str = None,
                           value=None,
                           limit: int = 100) -> list[dict]:
        """表记录列表(按 id 键正序; 可选字段过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "voice48", table, "*"))
            keys = [k for k in keys
                    if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        rec = self._deserialize(data)
                        if field is None \
                                or rec.get(field) == value:
                            result.append(rec)
            result.sort(key=lambda r: next(
                (v for k, v in r.items()
                 if k.endswith("Id")), 0))
            return result[:limit]
        self._ensure_store()
        result = [dict(r) for r in
                  self.store[table].values()
                  if field is None
                  or r.get(field) == value]
        result.sort(key=lambda r: next(
            (v for k, v in r.items()
             if k.endswith("Id")), 0))
        return result[:limit]

    async def get_record(self, table: str,
                         record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("voice48", table, record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[table].get(record_id)
        return dict(rec) if rec else None
