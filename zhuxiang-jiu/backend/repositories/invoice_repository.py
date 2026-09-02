"""42号·AI无感开票模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 invoice42, 设计文档 §3):
    invoice_title_books   会员抬头簿(抬头列表/默认标记/使用计数)
    invoice_decisions     开票决策流水(订单维度, 评分快照/档位/幂等)
    invoice_auto_queue    待确认队列(manual_queue 档, 抬头快照)

设计对齐:
    - 双模式存储 + None/bool 序列化口径(38/41号实机修复惯例)
    - 发票本体不入新表: 直接落 finance 发票池(19号复用),
      decisions 经 invoiceNo 关联
    - Mock-first: 电子发票开具为 mock(FP 序列号沿用 finance)
"""

import json
import os

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 42号常量(设计文档 §5, 环境变量可覆盖便于测试)
# ============================================================

# 无感开票总开关(off 则纯手动回到 19号现状)
INVOICE_AUTO_MODE = os.environ.get("INVOICE_AUTO_MODE", "on")

# 电子发票通道三态(对齐 36/41号惯例)
INVOICE_CHANNEL_MODE = os.environ.get("INVOICE_CHANNEL_MODE", "mock")

# 频次因子窗口与阈值(24h 内 ≥5 次疑似拆分开票)
INVOICE_FREQ_WINDOW_HOURS = int(
    os.environ.get("INVOICE_FREQ_WINDOW_HOURS", "24"))
INVOICE_FREQ_THRESHOLD = int(
    os.environ.get("INVOICE_FREQ_THRESHOLD", "5"))

# 开票最低金额(低于不开, 0.01 兜底零元单)
INVOICE_MIN_AMOUNT = float(
    os.environ.get("INVOICE_MIN_AMOUNT", "0.01"))

# ============================================================
# 抬头簿常量
# ============================================================

TITLE_TYPE_PERSONAL = "personal"   # 个人抬头(姓名)
TITLE_TYPE_COMPANY = "company"     # 企业抬头(名称+税号)
TITLE_TYPES = (TITLE_TYPE_PERSONAL, TITLE_TYPE_COMPANY)

# ============================================================
# 决策档位(设计文档 §1.2)
# ============================================================

DECISION_AUTO_ISSUE = "auto_issue"      # 自动开具+存证
DECISION_MANUAL_QUEUE = "manual_queue"  # 待确认队列
DECISION_REJECT = "reject"              # 拦截留痕
DECISION_COLLECT = "collect"            # 无抬头, 偏好收集
DECISIONS = (DECISION_AUTO_ISSUE, DECISION_MANUAL_QUEUE,
             DECISION_REJECT, DECISION_COLLECT)

# 决策阈值(沿用 36/40/41号范式)
DECISION_AUTO_SCORE = 70.0
DECISION_MANUAL_SCORE = 50.0

# 队列条目状态
QUEUE_PENDING = "pending"   # 待用户确认
QUEUE_DONE = "done"         # 已确认开票
QUEUE_EXPIRED = "expired"    # 已过期(订单退款)

# ============================================================
# P1 申诉状态机(reject 拦截 → 申诉 → 裁决)
# ============================================================

APPEAL_STATUS_PENDING = "pending"    # 待裁决
APPEAL_STATUS_APPROVED = "approved"  # 误拦, 已恢复(通知补开)
APPEAL_STATUS_REJECTED = "rejected"  # 维持拦截, 归档
APPEAL_STATUSES = (APPEAL_STATUS_PENDING, APPEAL_STATUS_APPROVED,
                   APPEAL_STATUS_REJECTED)


class Invoice42Repository:
    """42号无感开票仓储(双模式, blogger/ride 四原语模式平移)"""

    TABLE_TITLES = "invoice_title_books"
    TABLE_DECISIONS = "invoice_decisions"
    TABLE_QUEUE = "invoice_auto_queue"
    TABLE_APPEALS = "invoice_appeals"

    _INT_FIELDS = ("titleId", "memberId", "useCount", "decisionId",
                   "reviewsToday", "appealId")
    _FLOAT_FIELDS = ("score", "amount", "consistency")
    _BOOL_FIELDS = ("isDefault", "decided", "queuedFed")

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        for key in (self.TABLE_TITLES, self.TABLE_DECISIONS,
                    self.TABLE_QUEUE, self.TABLE_APPEALS):
            self.store.setdefault(key, {})

    async def next_id(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("invoice42", kind, "seq"))
        self._ensure_store()
        seq_key = f"_invoice42_{kind}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

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
            if k in Invoice42Repository._INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in Invoice42Repository._FLOAT_FIELDS:
                if v == "" or v is None:
                    record[k] = None
                else:
                    try:
                        record[k] = float(v)
                    except (TypeError, ValueError):
                        record[k] = v
            elif k in Invoice42Repository._BOOL_FIELDS:
                record[k] = v in (1, "1", True, "True", "true")
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
            await client.hset(_k("invoice42", table, record_id),
                              mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("invoice42", table, record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("invoice42", table, "*"))
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

    # --------------------------------------------------------
    # 抬头簿(会员维度: 单 Hash 记录含 titles 列表)
    # --------------------------------------------------------

    async def get_book(self, member_id: int) -> dict | None:
        return await self._get(self.TABLE_TITLES, int(member_id))

    async def save_book(self, book: dict) -> dict:
        return await self._save(self.TABLE_TITLES,
                                book["memberId"], book)

    async def ensure_book(self, member_id: int) -> dict:
        """抬头簿惰性创建"""
        member_id = int(member_id)
        book = await self.get_book(member_id)
        if book is None:
            book = {"memberId": member_id, "titles": []}
            await self.save_book(book)
        return book

    # --------------------------------------------------------
    # 决策流水(订单维度幂等)
    # --------------------------------------------------------

    async def save_decision(self, decision: dict) -> dict:
        return await self._save(self.TABLE_DECISIONS,
                                decision["orderId"], decision)

    async def get_decision(self, order_id: str) -> dict | None:
        return await self._get(self.TABLE_DECISIONS, order_id)

    async def list_decisions(self, action: str = None,
                             member_id: int = None,
                             limit: int = 200) -> list[dict]:
        decisions = await self._list(self.TABLE_DECISIONS, limit=2000)
        if action:
            decisions = [d for d in decisions
                         if d.get("action") == action]
        if member_id is not None:
            decisions = [d for d in decisions
                         if int(d.get("memberId") or 0)
                         == int(member_id)]
        return decisions[:limit]

    # --------------------------------------------------------
    # 待确认队列
    # --------------------------------------------------------

    async def save_queue_item(self, item: dict) -> dict:
        return await self._save(self.TABLE_QUEUE,
                                item["decisionId"], item)

    async def get_queue_item(self, decision_id: str) -> dict | None:
        return await self._get(self.TABLE_QUEUE, decision_id)

    async def list_queue(self, member_id: int = None,
                         status: str = None,
                         limit: int = 200) -> list[dict]:
        items = await self._list(self.TABLE_QUEUE, limit=2000)
        if member_id is not None:
            items = [i for i in items
                     if int(i.get("memberId") or 0) == int(member_id)]
        if status:
            items = [i for i in items
                     if i.get("status") == status]
        return items[:limit]

    # --------------------------------------------------------
    # 申诉(P1)
    # --------------------------------------------------------

    async def save_appeal(self, appeal: dict) -> dict:
        return await self._save(self.TABLE_APPEALS,
                                appeal["appealId"], appeal)

    async def get_appeal(self, appeal_id: int) -> dict | None:
        return await self._get(self.TABLE_APPEALS, appeal_id)

    async def get_appeal_by_order(self, order_id: str) -> dict | None:
        """按订单号查申诉(幂等: 一拦截一申诉)"""
        appeals = await self._list(self.TABLE_APPEALS, limit=2000)
        for a in appeals:
            if a.get("orderId") == order_id:
                return a
        return None

    async def list_appeals(self, status: str = None,
                           member_id: int = None,
                           limit: int = 200) -> list[dict]:
        appeals = await self._list(self.TABLE_APPEALS, limit=2000)
        if status:
            appeals = [a for a in appeals
                       if a.get("status") == status]
        if member_id is not None:
            appeals = [a for a in appeals
                       if int(a.get("memberId") or 0)
                       == int(member_id)]
        return appeals[:limit]
