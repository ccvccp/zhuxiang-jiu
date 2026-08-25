"""活动管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    activities            - 活动表(8类活动/状态机: 草稿→报名中→进行中→已结束)
    activity_registrations - 报名表(报名/取消/幂等防重)
    activity_leaderboards  - 擂台赛排名表(8类擂台赛/排名)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 活动: 按 id 主键, activity_no 唯一
    - 报名幂等: (activity_id, user_id) 唯一索引
    - 擂台赛排名: 按 activity_id + rank 索引
"""

import json
from datetime import datetime

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 活动类型(8类)
# ============================================================

TYPE_PROMOTION = "promotion"    # 促销活动
TYPE_LOTTERY = "lottery"        # 抽奖活动
TYPE_COMPETITION = "competition"  # 竞赛活动
TYPE_ARENA = "arena"            # 擂台赛
TYPE_INTERACTIVE = "interactive"  # 互动活动
TYPE_GROUPBUY = "groupbuy"      # 拼团
TYPE_SECKILL = "seckill"        # 秒杀
TYPE_PRESALE = "presale"        # 预售

# 8类擂台赛(对应 TYPE_ARENA 的 sub_type)
ARENA_L01 = "L01"  # 引流擂台赛
ARENA_L02 = "L02"  # 体验擂台赛
ARENA_L03 = "L03"  # 销售擂台赛
ARENA_L04 = "L04"  # 金点子擂台赛
ARENA_L05 = "L05"  # 内容擂台赛
ARENA_L06 = "L06"  # 品鉴擂台赛
ARENA_L07 = "L07"  # 服务擂台赛
ARENA_L08 = "L08"  # 传承擂台赛

# ============================================================
# 活动状态机
# ============================================================

STATUS_DRAFT = "draft"            # 草稿
STATUS_REGISTERING = "registering"  # 报名中
STATUS_ONGOING = "ongoing"          # 进行中
STATUS_ENDED = "ended"             # 已结束
STATUS_CANCELLED = "cancelled"      # 已取消

# 状态流转规则(允许的下一状态)
STATUS_TRANSITIONS = {
    STATUS_DRAFT: {STATUS_REGISTERING, STATUS_CANCELLED},
    STATUS_REGISTERING: {STATUS_ONGOING, STATUS_CANCELLED},
    STATUS_ONGOING: {STATUS_ENDED, STATUS_CANCELLED},
    STATUS_ENDED: set(),       # 终态
    STATUS_CANCELLED: set(),   # 终态
}

# 报名状态
REG_STATUS_REGISTERED = "registered"  # 已报名
REG_STATUS_CANCELLED = "cancelled"    # 已取消

# 奖品类型
PRIZE_COUPON = "coupon"     # 优惠券
PRIZE_POINTS = "points"     # 积分
PRIZE_PRODUCT = "product"   # 实物
PRIZE_CASH = "cash"         # 现金
PRIZE_BENEFIT = "benefit"   # 权益
PRIZE_BANQUET_WINE = "banquet_wine"  # 喜宴用酒
PRIZE_MASCOT = "mascot"     # 吉祥物


def can_transition(from_status: str, to_status: str) -> bool:
    """状态机校验: 是否允许从 from_status 流转到 to_status"""
    allowed = STATUS_TRANSITIONS.get(from_status, set())
    return to_status in allowed


class ActivityRepository:
    """活动管理数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_activity_id(self) -> int:
        """生成活动ID"""
        if is_redis_mode():
            return await self._redis_next_id("activity")
        return self._mem_next_id("_activity_seq")

    async def next_registration_id(self) -> int:
        """生成报名ID"""
        if is_redis_mode():
            return await self._redis_next_id("activity_reg")
        return self._mem_next_id("_activity_reg_seq")

    async def next_leaderboard_id(self) -> int:
        """生成排行榜ID"""
        if is_redis_mode():
            return await self._redis_next_id("activity_leaderboard")
        return self._mem_next_id("_activity_leaderboard_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("activity", entity, "seq"))

    # ============================================================
    # 活动 CRUD
    # ============================================================

    async def get_activity(self, activity_id: int) -> dict | None:
        """按ID查询活动"""
        if is_redis_mode():
            return await self._redis_get_activity(activity_id)
        return self._mem_get_activity(activity_id)

    async def save_activity(self, activity: dict) -> None:
        """保存活动(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_activity(activity)
        else:
            self._mem_save_activity(activity)

    async def list_activities(self, status: str = None, type_: str = None,
                               limit: int = 50) -> list[dict]:
        """查询活动列表(支持按状态/类型筛选)"""
        if is_redis_mode():
            return await self._redis_list_activities(status, type_, limit)
        return self._mem_list_activities(status, type_, limit)

    async def list_admin_activities(self, status: str = None, limit: int = 50) -> list[dict]:
        """管理端查询活动列表(含草稿)"""
        return await self.list_activities(status=status, limit=limit)

    # ============================================================
    # 报名 CRUD
    # ============================================================

    async def add_registration(self, reg: dict) -> int:
        """新增报名(返回报名ID)"""
        reg_id = await self.next_registration_id()
        reg["id"] = reg_id
        if "createdAt" not in reg:
            reg["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_registration(reg)
        else:
            self._mem_add_registration(reg)
        return reg_id

    async def get_registration(self, activity_id: int, user_id: int) -> dict | None:
        """按 (activity_id, user_id) 查询报名记录(幂等防重)"""
        if is_redis_mode():
            return await self._redis_get_registration(activity_id, user_id)
        return self._mem_get_registration(activity_id, user_id)

    async def update_registration_status(self, reg_id: int, status: str) -> None:
        """更新报名状态"""
        if is_redis_mode():
            await self._redis_update_registration_status(reg_id, status)
        else:
            self._mem_update_registration_status(reg_id, status)

    async def list_registrations(self, activity_id: int,
                                 limit: int = 100) -> list[dict]:
        """查询活动报名列表"""
        if is_redis_mode():
            return await self._redis_list_registrations(activity_id, limit)
        return self._mem_list_registrations(activity_id, limit)

    async def count_registrations(self, activity_id: int) -> int:
        """查询活动报名人数(仅有效报名)"""
        if is_redis_mode():
            return await self._redis_count_registrations(activity_id)
        return self._mem_count_registrations(activity_id)

    # ============================================================
    # 擂台赛排名 CRUD
    # ============================================================

    async def add_leaderboard_entry(self, entry: dict) -> int:
        """新增擂台赛排名记录(返回ID)"""
        entry_id = await self.next_leaderboard_id()
        entry["id"] = entry_id
        if "createdAt" not in entry:
            entry["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_leaderboard_entry(entry)
        else:
            self._mem_add_leaderboard_entry(entry)
        return entry_id

    async def update_leaderboard_entry(self, entry_id: int,
                                        score: float, rank: int) -> None:
        """更新擂台赛排名"""
        if is_redis_mode():
            await self._redis_update_leaderboard_entry(entry_id, score, rank)
        else:
            self._mem_update_leaderboard_entry(entry_id, score, rank)

    async def list_leaderboard(self, activity_id: int,
                               limit: int = 100) -> list[dict]:
        """查询擂台赛排名(按 rank 升序)"""
        if is_redis_mode():
            return await self._redis_list_leaderboard(activity_id, limit)
        return self._mem_list_leaderboard(activity_id, limit)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含活动模块的键(懒初始化)"""
        if "activities" not in self.store:
            self.store["activities"] = {}                    # id → activity
            self.store["activity_registrations"] = {}          # regId → registration
            self.store["activity_registrations_by_activity"] = {}  # activityId → {userId: regId}
            self.store["activity_leaderboards"] = {}          # entryId → entry
            self.store["activity_leaderboards_by_activity"] = {}  # activityId → [entryId, ...]
            self.store["_activity_seq"] = 0
            self.store["_activity_reg_seq"] = 0
            self.store["_activity_leaderboard_seq"] = 0

    # --- 活动 ---

    def _mem_get_activity(self, activity_id: int) -> dict | None:
        self._ensure_store()
        return self.store["activities"].get(activity_id)

    def _mem_save_activity(self, activity: dict) -> None:
        self._ensure_store()
        activity_id = activity["id"]
        activity["updatedAt"] = datetime.utcnow().isoformat()
        self.store["activities"][activity_id] = activity

    def _mem_list_activities(self, status: str = None, type_: str = None,
                             limit: int = 50) -> list[dict]:
        self._ensure_store()
        activities = list(self.store["activities"].values())
        # 默认排除草稿(管理端调用时显式传 status)
        if status:
            activities = [a for a in activities if a.get("status") == status]
        if type_:
            activities = [a for a in activities if a.get("type") == type_]
        activities.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        return activities[:limit]

    # --- 报名 ---

    def _mem_add_registration(self, reg: dict) -> None:
        self._ensure_store()
        reg_id = reg["id"]
        activity_id = reg["activityId"]
        user_id = reg["userId"]
        self.store["activity_registrations"][reg_id] = reg
        if activity_id not in self.store["activity_registrations_by_activity"]:
            self.store["activity_registrations_by_activity"][activity_id] = {}
        self.store["activity_registrations_by_activity"][activity_id][user_id] = reg_id

    def _mem_get_registration(self, activity_id: int, user_id: int) -> dict | None:
        self._ensure_store()
        reg_map = self.store["activity_registrations_by_activity"].get(activity_id, {})
        reg_id = reg_map.get(user_id)
        if reg_id is None:
            return None
        return self.store["activity_registrations"].get(reg_id)

    def _mem_update_registration_status(self, reg_id: int, status: str) -> None:
        self._ensure_store()
        reg = self.store["activity_registrations"].get(reg_id)
        if reg:
            reg["status"] = status
            reg["updatedAt"] = datetime.utcnow().isoformat()

    def _mem_list_registrations(self, activity_id: int, limit: int = 100) -> list[dict]:
        self._ensure_store()
        reg_map = self.store["activity_registrations_by_activity"].get(activity_id, {})
        regs = [self.store["activity_registrations"][rid] for rid in reg_map.values()
                if rid in self.store["activity_registrations"]]
        regs.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return regs[:limit]

    def _mem_count_registrations(self, activity_id: int) -> int:
        self._ensure_store()
        reg_map = self.store["activity_registrations_by_activity"].get(activity_id, {})
        return sum(1 for rid in reg_map.values()
                   if rid in self.store["activity_registrations"]
                   and self.store["activity_registrations"][rid].get("status") == REG_STATUS_REGISTERED)

    # --- 擂台赛排名 ---

    def _mem_add_leaderboard_entry(self, entry: dict) -> None:
        self._ensure_store()
        entry_id = entry["id"]
        activity_id = entry["activityId"]
        self.store["activity_leaderboards"][entry_id] = entry
        if activity_id not in self.store["activity_leaderboards_by_activity"]:
            self.store["activity_leaderboards_by_activity"][activity_id] = []
        self.store["activity_leaderboards_by_activity"][activity_id].append(entry_id)

    def _mem_update_leaderboard_entry(self, entry_id: int, score: float, rank: int) -> None:
        self._ensure_store()
        entry = self.store["activity_leaderboards"].get(entry_id)
        if entry:
            entry["score"] = score
            entry["rank"] = rank
            entry["updatedAt"] = datetime.utcnow().isoformat()

    def _mem_list_leaderboard(self, activity_id: int, limit: int = 100) -> list[dict]:
        self._ensure_store()
        entry_ids = self.store["activity_leaderboards_by_activity"].get(activity_id, [])
        entries = [self.store["activity_leaderboards"][eid] for eid in entry_ids
                   if eid in self.store["activity_leaderboards"]]
        entries.sort(key=lambda e: e.get("rank", 9999))
        return entries[:limit]

    # ============================================================
    # Redis 模式实现
    # ============================================================

    async def _redis_get_activity(self, activity_id: int) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("activity", "item", activity_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_activity(self, activity: dict) -> None:
        client = await get_redis_client()
        activity_id = activity["id"]
        activity["updatedAt"] = datetime.utcnow().isoformat()
        await client.set(_k("activity", "item", activity_id),
                         json.dumps(activity, ensure_ascii=False))

    async def _redis_list_activities(self, status: str = None, type_: str = None,
                                     limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("activity", "item", "*"))
        activities = []
        for key in keys:
            data = await client.get(key)
            if data:
                a = json.loads(data)
                if status and a.get("status") != status:
                    continue
                if type_ and a.get("type") != type_:
                    continue
                activities.append(a)
        activities.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        return activities[:limit]

    async def _redis_add_registration(self, reg: dict) -> None:
        client = await get_redis_client()
        reg_id = reg["id"]
        activity_id = reg["activityId"]
        user_id = reg["userId"]
        await client.set(_k("activity", "reg", reg_id),
                         json.dumps(reg, ensure_ascii=False))
        await client.hset(_k("activity", "regs_by_activity", activity_id),
                          user_id, reg_id)
        await client.lpush(_k("activity", "reg_list", activity_id), reg_id)

    async def _redis_get_registration(self, activity_id: int, user_id: int) -> dict | None:
        client = await get_redis_client()
        reg_id = await client.hget(_k("activity", "regs_by_activity", activity_id), user_id)
        if not reg_id:
            return None
        data = await client.get(_k("activity", "reg", reg_id))
        return json.loads(data) if data else None

    async def _redis_update_registration_status(self, reg_id: int, status: str) -> None:
        client = await get_redis_client()
        data = await client.get(_k("activity", "reg", reg_id))
        if data:
            reg = json.loads(data)
            reg["status"] = status
            reg["updatedAt"] = datetime.utcnow().isoformat()
            await client.set(_k("activity", "reg", reg_id),
                             json.dumps(reg, ensure_ascii=False))

    async def _redis_list_registrations(self, activity_id: int, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        reg_ids = await client.lrange(_k("activity", "reg_list", activity_id), 0, limit - 1)
        result = []
        for rid in reg_ids:
            data = await client.get(_k("activity", "reg", rid))
            if data:
                result.append(json.loads(data))
        return result

    async def _redis_count_registrations(self, activity_id: int) -> int:
        regs = await self._redis_list_registrations(activity_id, limit=10000)
        return sum(1 for r in regs if r.get("status") == REG_STATUS_REGISTERED)

    async def _redis_add_leaderboard_entry(self, entry: dict) -> None:
        client = await get_redis_client()
        entry_id = entry["id"]
        activity_id = entry["activityId"]
        await client.set(_k("activity", "leaderboard", entry_id),
                         json.dumps(entry, ensure_ascii=False))
        await client.lpush(_k("activity", "leaderboard_list", activity_id), entry_id)

    async def _redis_update_leaderboard_entry(self, entry_id: int, score: float, rank: int) -> None:
        client = await get_redis_client()
        data = await client.get(_k("activity", "leaderboard", entry_id))
        if data:
            entry = json.loads(data)
            entry["score"] = score
            entry["rank"] = rank
            entry["updatedAt"] = datetime.utcnow().isoformat()
            await client.set(_k("activity", "leaderboard", entry_id),
                             json.dumps(entry, ensure_ascii=False))

    async def _redis_list_leaderboard(self, activity_id: int, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        entry_ids = await client.lrange(_k("activity", "leaderboard_list", activity_id), 0, -1)
        entries = []
        for eid in entry_ids:
            data = await client.get(_k("activity", "leaderboard", eid))
            if data:
                entries.append(json.loads(data))
        entries.sort(key=lambda e: e.get("rank", 9999))
        return entries[:limit]
