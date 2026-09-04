"""48号·小竹智能语音中枢数据访问层(双模式: 内存 + Redis)

表清单(前缀 voice48, 计划 §四 P0):
    voice48_sessions   会话(sessionId 键; memberId 归属)
    voice48_turns      轮次(sessionId 下的 seq 键; 时间正序)

会话记录结构:
    {sessionId, memberId, channel(voice|text),
     status(open|closed), startedAt, lastActiveAt}

轮次记录结构(P0; 音频本体永不落库——仅元信息):
    {turnId, sessionId, seq, channel, audioMeta(JSON:
      {durationSec, sizeBytes} 或空——转写后即删),
     rawText(PII 脱敏后), wake(本轮是否经唤醒判定),
     intent, action, reply, card(JSON 或空), jump,
     latencyMs, ts}

设计对齐(43-47号惯例):
    - 双模式存储 + 显式序列化口径(bool→0/1, dict/list→
      JSON 字符串, None→"")
    - 会话清除级联轮次(隐私一键清除红线)
    - 轮次按会话 seq 排序返回(指代消解上下文窗口用)
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

    _INT_FIELDS = ("memberId", "seq")
    _FLOAT_FIELDS = ("latencyMs",)

    def __init__(self):
        self.store = get_in_memory_store()

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_SESSIONS, {})
        self.store.setdefault(self.TABLE_TURNS, {})

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
            if k in ("memberId", "seq"):
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k == "latencyMs":
                try:
                    record[k] = float(v) if v != "" else 0.0
                except (TypeError, ValueError):
                    record[k] = 0.0
            elif k in ("audioMeta", "card"):
                try:
                    record[k] = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    record[k] = {}
            elif k == "wake":
                record[k] = v in (1, "1", True, "True", "true")
            else:
                record[k] = v
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
