"""39号·AI智能网站入口管理模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 entry, 设计文档 §3):
    entry_qr           扫码登录会话(pending→scanned→confirmed→expired/cancelled)
    entry_devices      可信设备清单(memberId 维度, field=deviceId)
    entry_fingerprints 设备指纹简档(deviceId → UA摘要/首末见)
    entry_decisions    风控决策留痕(因子快照/动作/结果/反馈态)
    entry_events       登录事件流水(通道/耗时/成败/风险分)
    entry_bio          生物凭证(P1: credentialId → 公钥式摘要)
    entry_login_streak 连续登录天数(P1)
    entry_landing      角色落地页配置(P1)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - None/bool 序列化口径对齐 38号实机修复(Redis hset 不接受)
"""

import json

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 扫码会话状态机(设计文档 §2.2)
# ============================================================

QR_PENDING = "pending"        # 已创建待扫码
QR_SCANNED = "scanned"        # 已扫待确认
QR_CONFIRMED = "confirmed"    # 已确认(携带一次性 loginTicket)
QR_EXPIRED = "expired"        # 超时
QR_CANCELLED = "cancelled"    # 取消
QR_STATUSES = (QR_PENDING, QR_SCANNED, QR_CONFIRMED,
               QR_EXPIRED, QR_CANCELLED)

QR_TTL_SECONDS = 180          # 会话有效期(3 分钟)
LOGIN_TICKET_TTL = 60         # 一次性换令牌票据有效期(60s)

# 设备指纹/可信设备
TRUST_DAYS_DEFAULT = 30       # 可信设备免登录天数
RISK_WINDOW_MINUTES = 10      # 登录失败计数窗口

# 风控动作(对齐 auth_risk 评分器四级动作)
GUARD_ALLOW = "allow"
GUARD_STEP_UP = "step_up"
GUARD_CHALLENGE = "challenge"
GUARD_BLOCK = "block"

# 登录通道(设计文档 §1.2 六通道)
MODE_PASSWORD = "password"
MODE_SMS = "sms"
MODE_QR = "qr"
MODE_FINGERPRINT = "fingerprint"
MODE_FACE = "face"
MODE_OAUTH = "oauth"
MODES = (MODE_PASSWORD, MODE_SMS, MODE_QR, MODE_FINGERPRINT,
         MODE_FACE, MODE_OAUTH)

# 生物凭证状态(P1)
BIO_STATUS_ACTIVE = "active"
BIO_STATUS_REVOKED = "revoked"

_INT_FIELDS = ("decisionId", "eventId", "seq", "memberId",
               "failedAttempts", "riskScore")
_FLOAT_FIELDS = ("riskAvg",)


def _now_ts() -> float:
    import time
    return time.time()


class EntryRepository:
    """39号·AI智能网站入口管理模块数据访问层"""

    TABLE_QR = "entry_qr"
    TABLE_DEVICES = "entry_devices"
    TABLE_FINGERPRINTS = "entry_fingerprints"
    TABLE_DECISIONS = "entry_decisions"
    TABLE_EVENTS = "entry_events"
    TABLE_BIO = "entry_bio"

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号 / 序列化(Redis 口径对齐 38号修复)
    # ============================================================

    async def next_id(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("entry", kind, "seq"))
        self._ensure_store()
        seq_key = f"_entry_{kind}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    def _ensure_store(self):
        for key in ("entry_qr", "entry_devices", "entry_fingerprints",
                    "entry_decisions", "entry_events", "entry_bio"):
            self.store.setdefault(key, {})

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
            if k in _INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in _FLOAT_FIELDS:
                try:
                    record[k] = float(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif isinstance(v, str) and v.startswith(("{", "[")):
                try:
                    record[k] = json.loads(v)
                except ValueError:
                    record[k] = v
            else:
                record[k] = v
        return record

    async def _save(self, table: str, record_id, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("entry", table, record_id),
                              mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("entry", table, record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("entry", table, "*"))
            result = []
            for key in keys:
                if key.endswith(":seq"):
                    continue
                data = await client.hgetall(key)
                if data:
                    result.append(self._deserialize(data))
        else:
            self._ensure_store()
            result = list(self.store[table].values())
        return result[:limit]

    async def _update(self, table: str, record_id, fields: dict) -> dict:
        record = await self._get(table, record_id)
        if record is None:
            raise KeyError(record_id)
        record.update(fields)
        return await self._save(table, record_id, record)

    async def _delete(self, table: str, record_id) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.delete(_k("entry", table, record_id))
        else:
            self._ensure_store()
            self.store[table].pop(record_id, None)

    # ============================================================
    # 扫码登录会话
    # ============================================================

    async def save_qr(self, record: dict) -> dict:
        return await self._save(self.TABLE_QR, record["qrId"], record)

    async def get_qr(self, qr_id: str) -> dict | None:
        return await self._get(self.TABLE_QR, qr_id)

    async def update_qr(self, qr_id: str, fields: dict) -> dict:
        return await self._update(self.TABLE_QR, qr_id, fields)

    # ============================================================
    # 可信设备(field=deviceId 的 Hash 语义: 记录内含 deviceId)
    # ============================================================

    async def save_device(self, member_id: int, record: dict) -> dict:
        record_id = f"{member_id}:{record['deviceId']}"
        # memberId 落记录体内(list_devices 过滤依据)
        record = {**record, "memberId": int(member_id)}
        return await self._save(self.TABLE_DEVICES, record_id, record)

    async def get_device(self, member_id: int,
                         device_id: str) -> dict | None:
        return await self._get(self.TABLE_DEVICES,
                               f"{member_id}:{device_id}")

    async def delete_device(self, member_id: int, device_id: str) -> None:
        await self._delete(self.TABLE_DEVICES, f"{member_id}:{device_id}")

    async def list_devices(self, member_id: int) -> list[dict]:
        records = await self._list(self.TABLE_DEVICES, limit=1000)
        return [r for r in records if r.get("memberId") == member_id]

    # ============================================================
    # 设备指纹简档
    # ============================================================

    async def save_fingerprint(self, device_id: str,
                               record: dict) -> dict:
        return await self._save(self.TABLE_FINGERPRINTS,
                                device_id, record)

    async def get_fingerprint(self, device_id: str) -> dict | None:
        return await self._get(self.TABLE_FINGERPRINTS, device_id)

    # ============================================================
    # 风控决策留痕 / 登录事件流水
    # ============================================================

    async def save_decision(self, record: dict) -> dict:
        return await self._save(self.TABLE_DECISIONS,
                                record["decisionId"], record)

    async def get_decision(self, decision_id: int) -> dict | None:
        return await self._get(self.TABLE_DECISIONS, decision_id)

    async def update_decision(self, decision_id: int,
                              fields: dict) -> dict:
        return await self._update(self.TABLE_DECISIONS, decision_id,
                                  fields)

    async def list_decisions(self, member_id: int = None,
                             action: str = None,
                             limit: int = 100) -> list[dict]:
        records = await self._list(self.TABLE_DECISIONS, limit=1000)
        result = []
        for r in records:
            if member_id is not None \
                    and r.get("memberId") != member_id:
                continue
            if action and r.get("action") != action:
                continue
            result.append(r)
        return sorted(result, key=lambda x: x.get("decisionId", 0),
                      reverse=True)[:limit]

    async def save_event(self, record: dict) -> dict:
        return await self._save(self.TABLE_EVENTS,
                                record["eventId"], record)

    async def list_events(self, member_id: int = None,
                          mode: str = None,
                          success: bool = None,
                          limit: int = 100) -> list[dict]:
        records = await self._list(self.TABLE_EVENTS, limit=1000)
        result = []
        for r in records:
            if member_id is not None \
                    and r.get("memberId") != member_id:
                continue
            if mode and r.get("mode") != mode:
                continue
            if success is not None \
                    and bool(r.get("success")) is not success:
                continue
            result.append(r)
        return sorted(result, key=lambda x: x.get("eventId", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 失败计数(风控 failed_attempts 因子, 窗口内存即可)
    # ============================================================

    def _fail_key(self, key: str) -> str:
        return f"_entry_fail_{key}"

    def get_failed_attempts(self, key: str) -> int:
        """窗口内失败次数(内存模式; Redis 模式由调用方经 client)"""
        self._ensure_store()
        box = self.store.get(self._fail_key(key))
        if not box:
            return 0
        if _now_ts() - box["ts"] > RISK_WINDOW_MINUTES * 60:
            return 0
        return int(box["count"])

    def bump_failed_attempts(self, key: str) -> int:
        self._ensure_store()
        box = self.store.get(self._fail_key(key))
        if not box or _now_ts() - box["ts"] > RISK_WINDOW_MINUTES * 60:
            box = {"ts": _now_ts(), "count": 0}
        box["count"] += 1
        self.store[self._fail_key(key)] = box
        return box["count"]

    def clear_failed_attempts(self, key: str) -> None:
        self._ensure_store()
        self.store.pop(self._fail_key(key), None)

    # ============================================================
    # 生物凭证(P1)
    # ============================================================

    async def save_bio(self, record: dict) -> dict:
        return await self._save(self.TABLE_BIO,
                                record["credentialId"], record)

    async def get_bio(self, credential_id: str) -> dict | None:
        return await self._get(self.TABLE_BIO, credential_id)

    async def delete_bio(self, credential_id: str) -> None:
        await self._delete(self.TABLE_BIO, credential_id)

    async def list_bio(self, member_id: int = None,
                       limit: int = 200) -> list[dict]:
        records = await self._list(self.TABLE_BIO, limit=limit)
        if member_id is not None:
            records = [r for r in records
                       if r.get("memberId") == member_id]
        return records
