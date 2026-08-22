"""老酒兑换及回收模块数据访问层(双模式: 内存 + Redis)

表清单:
    recycle_applications:  回收申请表(用户提交的老酒兑换/回收申请)
    recycle_valuations:    回收估价表(AI智能估值结果)
    recycle_exchanges:      兑换记录表(兑换新酒/折现回收交易记录)
    recycle_negotiations:   新酒议价记录表(新酒回收议价全过程)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 申请单号: 内存计数器 / Redis INCR 生成 HS+时间戳
    - 状态流转: 申请→估价→审核→回收→兑换
    - 品质分级: A/B/C/D 四级(影响价值95%-100%)
    - 新酒议价: 当年/1年/2年/3年酒分类, 支持多轮议价(最多3轮)
"""

import asyncio
import json
from datetime import datetime
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 申请状态流转
# ============================================================

STATUS_PENDING = "pending"          # 待估价(已提交)
STATUS_VALUING = "valuing"          # 估价中
STATUS_VALUED = "valued"            # 已估价
STATUS_REVIEWING = "reviewing"      # 审核中
STATUS_APPROVED = "approved"        # 审核通过
STATUS_REJECTED = "rejected"        # 审核拒绝
STATUS_RECYCLING = "recycling"      # 回收中(寄回)
STATUS_EXCHANGING = "exchanging"    # 兑换中
STATUS_COMPLETED = "completed"      # 已完成
STATUS_CANCELLED = "cancelled"      # 已取消

# 业务类型
TYPE_EXCHANGE = "exchange"          # 兑换新酒
TYPE_RECYCLE = "recycle"            # 折现回收
TYPE_NEW_WINE_RECYCLE = "new_wine_recycle"  # 新酒议价回收

# ============================================================
# 新酒分类(未达到3年的酒)
# ============================================================

WINE_AGE_CURRENT = "current"          # 当年酒(0年)
WINE_AGE_ONE_YEAR = "one_year"        # 1年酒
WINE_AGE_TWO_YEARS = "two_years"      # 2年酒
WINE_AGE_THREE_YEARS = "three_years"  # 3年酒(边界)

# 新酒年份分类映射(酒龄 → 分类)
WINE_AGE_CATEGORY_MAP = {
    0: WINE_AGE_CURRENT,
    1: WINE_AGE_ONE_YEAR,
    2: WINE_AGE_TWO_YEARS,
    3: WINE_AGE_THREE_YEARS,
}

# 新酒回收折扣率(未满3年的酒折价回收)
NEW_WINE_DISCOUNT_RATES = {
    WINE_AGE_CURRENT: 0.90,       # 当年酒: 9折
    WINE_AGE_ONE_YEAR: 0.85,     # 1年酒: 85折
    WINE_AGE_TWO_YEARS: 0.80,    # 2年酒: 8折
    WINE_AGE_THREE_YEARS: 0.75,  # 3年酒: 75折(或走老酒增值路径)
}

# 新酒分类中文名
WINE_AGE_CATEGORY_NAMES = {
    WINE_AGE_CURRENT: "当年酒",
    WINE_AGE_ONE_YEAR: "1年酒",
    WINE_AGE_TWO_YEARS: "2年酒",
    WINE_AGE_THREE_YEARS: "3年酒",
}

# ============================================================
# 议价状态流转
# ============================================================

NEG_STATUS_PENDING = "pending"              # 待议价(AI已估价,等用户响应)
NEG_STATUS_USER_PROPOSED = "user_proposed"  # 用户已出价
NEG_STATUS_AI_COUNTER = "ai_counter"        # AI已反价
NEG_STATUS_ACCEPTED = "accepted"            # 已接受(议价成功)
NEG_STATUS_REJECTED = "rejected"            # 已拒绝(议价失败)
NEG_STATUS_EXPIRED = "expired"              # 已过期

# 议价状态流转图
NEG_STATUS_TRANSITIONS = {
    NEG_STATUS_PENDING: [NEG_STATUS_USER_PROPOSED, NEG_STATUS_ACCEPTED, NEG_STATUS_REJECTED, NEG_STATUS_EXPIRED],
    NEG_STATUS_USER_PROPOSED: [NEG_STATUS_AI_COUNTER, NEG_STATUS_ACCEPTED, NEG_STATUS_REJECTED],
    NEG_STATUS_AI_COUNTER: [NEG_STATUS_USER_PROPOSED, NEG_STATUS_ACCEPTED, NEG_STATUS_REJECTED],
    NEG_STATUS_ACCEPTED: [],
    NEG_STATUS_REJECTED: [],
    NEG_STATUS_EXPIRED: [],
}

# 议价最大轮次
MAX_NEGOTIATION_ROUNDS = 3
# 议价系数范围(0.9~1.1)
NEGOTIATION_COEFFICIENT_MIN = 0.90
NEGOTIATION_COEFFICIENT_MAX = 1.10

# 品质分级(影响价值系数)
GRADE_A = "A"   # 全新 100%
GRADE_B = "B"   # 良好 95%
GRADE_C = "C"   # 一般 90%
GRADE_D = "D"   # 较差 85%

# 品质价值系数
GRADE_COEFFICIENTS = {
    GRADE_A: 1.00,
    GRADE_B: 0.95,
    GRADE_C: 0.90,
    GRADE_D: 0.85,
}

# 状态流转图(允许的下一状态)
STATUS_TRANSITIONS = {
    STATUS_PENDING: [STATUS_VALUING, STATUS_CANCELLED],
    STATUS_VALUING: [STATUS_VALUED, STATUS_CANCELLED],
    STATUS_VALUED: [STATUS_REVIEWING, STATUS_CANCELLED],
    STATUS_REVIEWING: [STATUS_APPROVED, STATUS_REJECTED],
    STATUS_APPROVED: [STATUS_RECYCLING, STATUS_EXCHANGING],
    STATUS_REJECTED: [],
    STATUS_RECYCLING: [STATUS_COMPLETED],
    STATUS_EXCHANGING: [STATUS_COMPLETED],
    STATUS_COMPLETED: [],
    STATUS_CANCELLED: [],
}


class RecycleRepository:
    """老酒兑换回收数据访问层"""

    # 内存模式下的序列号生成锁(类级共享, 保证 ID 自增原子性)
    _seq_lock = asyncio.Lock()

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_application_id(self) -> int:
        """生成回收申请ID"""
        if is_redis_mode():
            return await self._redis_next_id("recycle_application")
        return await self._mem_next_id("_recycle_application_seq")

    async def next_valuation_id(self) -> int:
        """生成估价ID"""
        if is_redis_mode():
            return await self._redis_next_id("recycle_valuation")
        return await self._mem_next_id("_recycle_valuation_seq")

    async def next_exchange_id(self) -> int:
        """生成兑换记录ID"""
        if is_redis_mode():
            return await self._redis_next_id("recycle_exchange")
        return await self._mem_next_id("_recycle_exchange_seq")

    async def next_negotiation_id(self) -> int:
        """生成议价记录ID"""
        if is_redis_mode():
            return await self._redis_next_id("recycle_negotiation")
        return await self._mem_next_id("_recycle_negotiation_seq")

    async def _mem_next_id(self, seq_key: str) -> int:
        """内存模式序列号自增(原子操作)

        使用类级 asyncio.Lock 保护 read-modify-write, 避免并发
        申请时不同用户产生重复 ID(service 层按 user_id 加锁, 无法
        跨用户互斥 ID 生成)。
        """
        async with self._seq_lock:
            self._ensure_store()
            seq = self.store.get(seq_key, 0) + 1
            self.store[seq_key] = seq
            return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("recycle", entity, "seq"))

    # ============================================================
    # 回收申请表 CRUD
    # ============================================================

    async def create_application(self, application: dict) -> int:
        """新增回收申请(返回申请ID)"""
        app_id = await self.next_application_id()
        application["id"] = app_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in application:
            application["createdAt"] = now
        if "updatedAt" not in application:
            application["updatedAt"] = now
        if "status" not in application:
            application["status"] = STATUS_PENDING
        if "applicationNo" not in application:
            application["applicationNo"] = f"HS{now.replace('-', '').replace('T', '')[:14]}{app_id:04d}"
        if is_redis_mode():
            await self._redis_create_application(application)
        else:
            self._mem_create_application(application)
        return app_id

    async def get_application(self, app_id: int) -> Optional[dict]:
        """按ID查询回收申请"""
        if is_redis_mode():
            return await self._redis_get_application(app_id)
        return self._mem_get_application(app_id)

    async def get_application_by_no(self, app_no: str) -> Optional[dict]:
        """按申请单号查询"""
        if is_redis_mode():
            return await self._redis_get_application_by_no(app_no)
        return self._mem_get_application_by_no(app_no)

    async def update_application(self, app_id: int, updates: dict) -> None:
        """更新回收申请(部分字段)"""
        if is_redis_mode():
            await self._redis_update_application(app_id, updates)
        else:
            self._mem_update_application(app_id, updates)

    async def update_application_status(self, app_id: int, status: str) -> None:
        """更新申请状态"""
        await self.update_application(app_id, {
            "status": status,
            "updatedAt": datetime.utcnow().isoformat(),
        })

    async def list_applications(self, user_id: int = None, status: str = None,
                                 app_type: str = None, limit: int = 50) -> list[dict]:
        """查询回收申请列表(支持筛选)"""
        if is_redis_mode():
            return await self._redis_list_applications(user_id, status, app_type, limit)
        return self._mem_list_applications(user_id, status, app_type, limit)

    # ============================================================
    # 回收估价表 CRUD
    # ============================================================

    async def add_valuation(self, valuation: dict) -> int:
        """新增回收估价(返回估价ID)"""
        val_id = await self.next_valuation_id()
        valuation["id"] = val_id
        if "createdAt" not in valuation:
            valuation["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_valuation(valuation)
        else:
            self._mem_add_valuation(valuation)
        return val_id

    async def get_valuation(self, val_id: int) -> Optional[dict]:
        """按ID查询估价"""
        if is_redis_mode():
            return await self._redis_get_valuation(val_id)
        return self._mem_get_valuation(val_id)

    async def get_valuation_by_application(self, app_id: int) -> Optional[dict]:
        """按申请ID查询最新估价"""
        if is_redis_mode():
            return await self._redis_get_valuation_by_application(app_id)
        return self._mem_get_valuation_by_application(app_id)

    async def list_valuations(self, user_id: int = None, limit: int = 50) -> list[dict]:
        """查询估价列表"""
        if is_redis_mode():
            return await self._redis_list_valuations(user_id, limit)
        return self._mem_list_valuations(user_id, limit)

    # ============================================================
    # 兑换记录表 CRUD
    # ============================================================

    async def add_exchange(self, exchange: dict) -> int:
        """新增兑换记录(返回兑换ID)"""
        ex_id = await self.next_exchange_id()
        exchange["id"] = ex_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in exchange:
            exchange["createdAt"] = now
        if "exchangeNo" not in exchange:
            exchange["exchangeNo"] = f"DH{now.replace('-', '').replace('T', '')[:14]}{ex_id:04d}"
        if is_redis_mode():
            await self._redis_add_exchange(exchange)
        else:
            self._mem_add_exchange(exchange)
        return ex_id

    async def get_exchange(self, ex_id: int) -> Optional[dict]:
        """按ID查询兑换记录"""
        if is_redis_mode():
            return await self._redis_get_exchange(ex_id)
        return self._mem_get_exchange(ex_id)

    async def update_exchange(self, ex_id: int, updates: dict) -> None:
        """更新兑换记录(部分字段)"""
        if is_redis_mode():
            await self._redis_update_exchange(ex_id, updates)
        else:
            self._mem_update_exchange(ex_id, updates)

    async def list_exchanges(self, user_id: int = None, ex_type: str = None,
                              limit: int = 50) -> list[dict]:
        """查询兑换记录列表"""
        if is_redis_mode():
            return await self._redis_list_exchanges(user_id, ex_type, limit)
        return self._mem_list_exchanges(user_id, ex_type, limit)

    # ============================================================
    # 库存管理(回收老酒库存)
    # ============================================================

    async def get_inventory(self, product_id: str = None) -> dict:
        """查询回收库存"""
        if is_redis_mode():
            return await self._redis_get_inventory(product_id)
        return self._mem_get_inventory(product_id)

    async def update_inventory(self, product_id: str, delta: int) -> dict:
        """更新回收库存(增量)"""
        if is_redis_mode():
            return await self._redis_update_inventory(product_id, delta)
        return self._mem_update_inventory(product_id, delta)

    # ============================================================
    # 新酒议价记录表 CRUD
    # ============================================================

    async def create_negotiation(self, negotiation: dict) -> int:
        """新增议价记录(返回议价ID)"""
        neg_id = await self.next_negotiation_id()
        negotiation["id"] = neg_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in negotiation:
            negotiation["createdAt"] = now
        if "updatedAt" not in negotiation:
            negotiation["updatedAt"] = now
        if "status" not in negotiation:
            negotiation["status"] = NEG_STATUS_PENDING
        if "negotiationNo" not in negotiation:
            negotiation["negotiationNo"] = f"YJ{now.replace('-', '').replace('T', '')[:14]}{neg_id:04d}"
        if is_redis_mode():
            await self._redis_create_negotiation(negotiation)
        else:
            self._mem_create_negotiation(negotiation)
        return neg_id

    async def get_negotiation(self, neg_id: int) -> Optional[dict]:
        """按ID查询议价记录"""
        if is_redis_mode():
            return await self._redis_get_negotiation(neg_id)
        return self._mem_get_negotiation(neg_id)

    async def update_negotiation(self, neg_id: int, updates: dict) -> None:
        """更新议价记录(部分字段)"""
        if is_redis_mode():
            await self._redis_update_negotiation(neg_id, updates)
        else:
            self._mem_update_negotiation(neg_id, updates)

    async def list_negotiations(self, user_id: int = None, status: str = None,
                                 limit: int = 50) -> list[dict]:
        """查询议价记录列表(支持筛选)"""
        if is_redis_mode():
            return await self._redis_list_negotiations(user_id, status, limit)
        return self._mem_list_negotiations(user_id, status, limit)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含回收模块的键(懒初始化)"""
        if "recycle_applications" not in self.store:
            self.store["recycle_applications"] = {}            # id → application
            self.store["recycle_applications_by_user"] = {}     # userId → [id, ...]
            self.store["recycle_applications_by_no"] = {}       # applicationNo → id
            self.store["recycle_valuations"] = {}                # id → valuation
            self.store["recycle_valuations_by_app"] = {}        # appId → valuationId
            self.store["recycle_valuations_by_user"] = {}       # userId → [valId, ...]
            self.store["recycle_exchanges"] = {}                # id → exchange
            self.store["recycle_exchanges_by_user"] = {}        # userId → [exId, ...]
            self.store["recycle_inventory"] = {}                 # productId → {stock, ...}
            self.store["recycle_negotiations"] = {}             # id → negotiation
            self.store["recycle_negotiations_by_user"] = {}     # userId → [negId, ...]
            self.store["_recycle_application_seq"] = 0
            self.store["_recycle_valuation_seq"] = 0
            self.store["_recycle_exchange_seq"] = 0
            self.store["_recycle_negotiation_seq"] = 0

    # --- 回收申请 ---

    def _mem_create_application(self, application: dict) -> None:
        self._ensure_store()
        app_id = application["id"]
        user_id = application.get("userId")
        app_no = application.get("applicationNo")
        self.store["recycle_applications"][app_id] = application
        if user_id is not None:
            self.store["recycle_applications_by_user"].setdefault(user_id, []).append(app_id)
        if app_no:
            self.store["recycle_applications_by_no"][app_no] = app_id

    def _mem_get_application(self, app_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["recycle_applications"].get(app_id)

    def _mem_get_application_by_no(self, app_no: str) -> Optional[dict]:
        self._ensure_store()
        app_id = self.store["recycle_applications_by_no"].get(app_no)
        if app_id is None:
            return None
        return self.store["recycle_applications"].get(app_id)

    def _mem_update_application(self, app_id: int, updates: dict) -> None:
        self._ensure_store()
        app = self.store["recycle_applications"].get(app_id)
        if app:
            app.update(updates)

    def _mem_list_applications(self, user_id: int = None, status: str = None,
                                app_type: str = None, limit: int = 50) -> list[dict]:
        self._ensure_store()
        if user_id is not None:
            ids = self.store["recycle_applications_by_user"].get(user_id, [])
            apps = [self.store["recycle_applications"][aid] for aid in ids
                    if aid in self.store["recycle_applications"]]
        else:
            apps = list(self.store["recycle_applications"].values())
        if status:
            apps = [a for a in apps if a.get("status") == status]
        if app_type:
            apps = [a for a in apps if a.get("type") == app_type]
        apps.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        return apps[:limit]

    # --- 估价 ---

    def _mem_add_valuation(self, valuation: dict) -> None:
        self._ensure_store()
        val_id = valuation["id"]
        app_id = valuation.get("applicationId")
        user_id = valuation.get("userId")
        self.store["recycle_valuations"][val_id] = valuation
        if app_id is not None:
            self.store["recycle_valuations_by_app"][app_id] = val_id
        if user_id is not None:
            self.store["recycle_valuations_by_user"].setdefault(user_id, []).append(val_id)

    def _mem_get_valuation(self, val_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["recycle_valuations"].get(val_id)

    def _mem_get_valuation_by_application(self, app_id: int) -> Optional[dict]:
        self._ensure_store()
        val_id = self.store["recycle_valuations_by_app"].get(app_id)
        if val_id is None:
            return None
        return self.store["recycle_valuations"].get(val_id)

    def _mem_list_valuations(self, user_id: int = None, limit: int = 50) -> list[dict]:
        self._ensure_store()
        if user_id is not None:
            ids = self.store["recycle_valuations_by_user"].get(user_id, [])
            vals = [self.store["recycle_valuations"][vid] for vid in ids
                    if vid in self.store["recycle_valuations"]]
        else:
            vals = list(self.store["recycle_valuations"].values())
        vals.sort(key=lambda v: v.get("createdAt", ""), reverse=True)
        return vals[:limit]

    # --- 兑换记录 ---

    def _mem_add_exchange(self, exchange: dict) -> None:
        self._ensure_store()
        ex_id = exchange["id"]
        user_id = exchange.get("userId")
        self.store["recycle_exchanges"][ex_id] = exchange
        if user_id is not None:
            self.store["recycle_exchanges_by_user"].setdefault(user_id, []).append(ex_id)

    def _mem_get_exchange(self, ex_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["recycle_exchanges"].get(ex_id)

    def _mem_update_exchange(self, ex_id: int, updates: dict) -> None:
        self._ensure_store()
        ex = self.store["recycle_exchanges"].get(ex_id)
        if ex:
            ex.update(updates)

    def _mem_list_exchanges(self, user_id: int = None, ex_type: str = None,
                             limit: int = 50) -> list[dict]:
        self._ensure_store()
        if user_id is not None:
            ids = self.store["recycle_exchanges_by_user"].get(user_id, [])
            exs = [self.store["recycle_exchanges"][eid] for eid in ids
                   if eid in self.store["recycle_exchanges"]]
        else:
            exs = list(self.store["recycle_exchanges"].values())
        if ex_type:
            exs = [e for e in exs if e.get("type") == ex_type]
        exs.sort(key=lambda e: e.get("createdAt", ""), reverse=True)
        return exs[:limit]

    # --- 库存 ---

    def _mem_get_inventory(self, product_id: str = None) -> dict:
        self._ensure_store()
        if product_id:
            return {product_id: self.store["recycle_inventory"].get(product_id, {"stock": 0})}
        return {pid: data for pid, data in self.store["recycle_inventory"].items()}

    def _mem_update_inventory(self, product_id: str, delta: int) -> dict:
        self._ensure_store()
        current = self.store["recycle_inventory"].get(product_id, {"stock": 0, "reserved": 0})
        current["stock"] = current.get("stock", 0) + delta
        self.store["recycle_inventory"][product_id] = current
        return {product_id: current}

    # --- 议价记录(内存) ---

    def _mem_create_negotiation(self, negotiation: dict) -> None:
        self._ensure_store()
        neg_id = negotiation["id"]
        user_id = negotiation.get("userId")
        self.store["recycle_negotiations"][neg_id] = negotiation
        if user_id is not None:
            self.store["recycle_negotiations_by_user"].setdefault(user_id, []).append(neg_id)

    def _mem_get_negotiation(self, neg_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["recycle_negotiations"].get(neg_id)

    def _mem_update_negotiation(self, neg_id: int, updates: dict) -> None:
        self._ensure_store()
        neg = self.store["recycle_negotiations"].get(neg_id)
        if neg:
            neg.update(updates)

    def _mem_list_negotiations(self, user_id: int = None, status: str = None,
                                 limit: int = 50) -> list[dict]:
        self._ensure_store()
        if user_id is not None:
            ids = self.store["recycle_negotiations_by_user"].get(user_id, [])
            negs = [self.store["recycle_negotiations"][nid] for nid in ids
                    if nid in self.store["recycle_negotiations"]]
        else:
            negs = list(self.store["recycle_negotiations"].values())
        if status:
            negs = [n for n in negs if n.get("status") == status]
        negs.sort(key=lambda n: n.get("createdAt", ""), reverse=True)
        return negs[:limit]

    # ============================================================
    # Redis 模式实现
    # ============================================================

    async def _redis_create_application(self, application: dict) -> None:
        client = await get_redis_client()
        app_id = application["id"]
        user_id = application.get("userId")
        app_no = application.get("applicationNo")
        await client.set(_k("recycle", "application", app_id),
                         json.dumps(application, ensure_ascii=False))
        if user_id is not None:
            await client.lpush(_k("recycle", "applications_by_user", user_id), app_id)
        if app_no:
            await client.set(_k("recycle", "application_no", app_no), app_id)

    async def _redis_get_application(self, app_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("recycle", "application", app_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_get_application_by_no(self, app_no: str) -> Optional[dict]:
        client = await get_redis_client()
        app_id = await client.get(_k("recycle", "application_no", app_no))
        if not app_id:
            return None
        return await self._redis_get_application(int(app_id))

    async def _redis_update_application(self, app_id: int, updates: dict) -> None:
        client = await get_redis_client()
        data = await client.get(_k("recycle", "application", app_id))
        if data:
            app = json.loads(data)
            app.update(updates)
            await client.set(_k("recycle", "application", app_id),
                             json.dumps(app, ensure_ascii=False))

    async def _redis_list_applications(self, user_id: int = None, status: str = None,
                                        app_type: str = None, limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        if user_id is not None:
            ids = await client.lrange(_k("recycle", "applications_by_user", user_id), 0, -1)
            apps = []
            for aid in ids:
                data = await client.get(_k("recycle", "application", aid))
                if data:
                    apps.append(json.loads(data))
        else:
            apps = []
            keys = await client.keys(_k("recycle", "application", "*"))
            for key in keys:
                # 排除 application_no 前缀的键
                if "application_no" in key:
                    continue
                data = await client.get(key)
                if data:
                    apps.append(json.loads(data))
        if status:
            apps = [a for a in apps if a.get("status") == status]
        if app_type:
            apps = [a for a in apps if a.get("type") == app_type]
        apps.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        return apps[:limit]

    # --- 估价 ---

    async def _redis_add_valuation(self, valuation: dict) -> None:
        client = await get_redis_client()
        val_id = valuation["id"]
        app_id = valuation.get("applicationId")
        user_id = valuation.get("userId")
        await client.set(_k("recycle", "valuation", val_id),
                         json.dumps(valuation, ensure_ascii=False))
        if app_id is not None:
            await client.set(_k("recycle", "valuation_by_app", app_id), val_id)
        if user_id is not None:
            await client.lpush(_k("recycle", "valuations_by_user", user_id), val_id)

    async def _redis_get_valuation(self, val_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("recycle", "valuation", val_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_get_valuation_by_application(self, app_id: int) -> Optional[dict]:
        client = await get_redis_client()
        val_id = await client.get(_k("recycle", "valuation_by_app", app_id))
        if not val_id:
            return None
        return await self._redis_get_valuation(int(val_id))

    async def _redis_list_valuations(self, user_id: int = None, limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        if user_id is not None:
            ids = await client.lrange(_k("recycle", "valuations_by_user", user_id), 0, limit - 1)
            vals = []
            for vid in ids:
                data = await client.get(_k("recycle", "valuation", vid))
                if data:
                    vals.append(json.loads(data))
        else:
            vals = []
            keys = await client.keys(_k("recycle", "valuation", "*"))
            for key in keys:
                if "valuation_by_app" in key:
                    continue
                data = await client.get(key)
                if data:
                    vals.append(json.loads(data))
            vals.sort(key=lambda v: v.get("createdAt", ""), reverse=True)
            vals = vals[:limit]
        return vals

    # --- 兑换记录 ---

    async def _redis_add_exchange(self, exchange: dict) -> None:
        client = await get_redis_client()
        ex_id = exchange["id"]
        user_id = exchange.get("userId")
        await client.set(_k("recycle", "exchange", ex_id),
                         json.dumps(exchange, ensure_ascii=False))
        if user_id is not None:
            await client.lpush(_k("recycle", "exchanges_by_user", user_id), ex_id)

    async def _redis_get_exchange(self, ex_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("recycle", "exchange", ex_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_update_exchange(self, ex_id: int, updates: dict) -> None:
        client = await get_redis_client()
        data = await client.get(_k("recycle", "exchange", ex_id))
        if data:
            ex = json.loads(data)
            ex.update(updates)
            await client.set(_k("recycle", "exchange", ex_id),
                             json.dumps(ex, ensure_ascii=False))

    async def _redis_list_exchanges(self, user_id: int = None, ex_type: str = None,
                                     limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        if user_id is not None:
            ids = await client.lrange(_k("recycle", "exchanges_by_user", user_id), 0, -1)
            exs = []
            for eid in ids:
                data = await client.get(_k("recycle", "exchange", eid))
                if data:
                    exs.append(json.loads(data))
        else:
            exs = []
            keys = await client.keys(_k("recycle", "exchange", "*"))
            for key in keys:
                if "exchanges_by_user" in key:
                    continue
                data = await client.get(key)
                if data:
                    exs.append(json.loads(data))
        if ex_type:
            exs = [e for e in exs if e.get("type") == ex_type]
        exs.sort(key=lambda e: e.get("createdAt", ""), reverse=True)
        return exs[:limit]

    # --- 库存 ---

    async def _redis_get_inventory(self, product_id: str = None) -> dict:
        client = await get_redis_client()
        if product_id:
            data = await client.hget(_k("recycle", "inventory"), product_id)
            if data:
                return {product_id: json.loads(data)}
            return {product_id: {"stock": 0}}
        all_data = await client.hgetall(_k("recycle", "inventory"))
        return {pid: json.loads(v) for pid, v in all_data.items()}

    async def _redis_update_inventory(self, product_id: str, delta: int) -> dict:
        client = await get_redis_client()
        data = await client.hget(_k("recycle", "inventory"), product_id)
        if data:
            current = json.loads(data)
        else:
            current = {"stock": 0, "reserved": 0}
        current["stock"] = current.get("stock", 0) + delta
        await client.hset(_k("recycle", "inventory"), product_id,
                          json.dumps(current, ensure_ascii=False))
        return {product_id: current}

    # --- 议价记录(Redis) ---

    async def _redis_create_negotiation(self, negotiation: dict) -> None:
        client = await get_redis_client()
        neg_id = negotiation["id"]
        user_id = negotiation.get("userId")
        await client.set(_k("recycle", "negotiation", neg_id),
                         json.dumps(negotiation, ensure_ascii=False))
        if user_id is not None:
            await client.lpush(_k("recycle", "negotiations_by_user", user_id), neg_id)

    async def _redis_get_negotiation(self, neg_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("recycle", "negotiation", neg_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_update_negotiation(self, neg_id: int, updates: dict) -> None:
        client = await get_redis_client()
        data = await client.get(_k("recycle", "negotiation", neg_id))
        if data:
            neg = json.loads(data)
            neg.update(updates)
            await client.set(_k("recycle", "negotiation", neg_id),
                             json.dumps(neg, ensure_ascii=False))

    async def _redis_list_negotiations(self, user_id: int = None, status: str = None,
                                         limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        if user_id is not None:
            ids = await client.lrange(_k("recycle", "negotiations_by_user", user_id), 0, -1)
            negs = []
            for nid in ids:
                data = await client.get(_k("recycle", "negotiation", nid))
                if data:
                    negs.append(json.loads(data))
        else:
            negs = []
            keys = await client.keys(_k("recycle", "negotiation", "*"))
            for key in keys:
                if "negotiations_by_user" in key:
                    continue
                data = await client.get(key)
                if data:
                    negs.append(json.loads(data))
        if status:
            negs = [n for n in negs if n.get("status") == status]
        negs.sort(key=lambda n: n.get("createdAt", ""), reverse=True)
        return negs[:limit]
