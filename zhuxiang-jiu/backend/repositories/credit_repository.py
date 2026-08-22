"""信用管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    credit_scores   - 信用分账户(竹信分0-1000/5级信用等级/先享后付额度)
    credit_logs     - 信用流水(行为加减分/升降级/黑名单/恢复)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 信用分账户: 按 user_id 主键, version 乐观锁
    - 流水自增ID: 内存计数器 / Redis INCR
    - 黑名单状态: status 字段(normal/frozen/blacklist)
"""

import json
from datetime import datetime
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 信用等级(5级)
# ============================================================

LEVEL_L1 = "L1"   # 极差(0-399)
LEVEL_L2 = "L2"   # 较差(400-549)
LEVEL_L3 = "L3"   # 中等(550-699)
LEVEL_L4 = "L4"   # 良好(700-799)
LEVEL_L5 = "L5"   # 优秀(800-1000)

# 等级对应分数区间(从高到低匹配)
LEVEL_THRESHOLDS = [
    (800, LEVEL_L5),
    (700, LEVEL_L4),
    (550, LEVEL_L3),
    (400, LEVEL_L2),
    (0,   LEVEL_L1),
]

# 等级对应先享后付额度(会员)
LEVEL_PAYLATER_QUOTA = {
    LEVEL_L1: 0,
    LEVEL_L2: 0,
    LEVEL_L3: 2000,
    LEVEL_L4: 5000,
    LEVEL_L5: 10000,
}

# 等级对应免息期(天)
LEVEL_PAYLATER_INTEREST_FREE_DAYS = {
    LEVEL_L1: 0,
    LEVEL_L2: 0,
    LEVEL_L3: 15,
    LEVEL_L4: 30,
    LEVEL_L5: 45,
}

# 等级对应季度奖励倍数
LEVEL_REWARD_MULTIPLIER = {
    LEVEL_L1: 0.0,
    LEVEL_L2: 0.8,
    LEVEL_L3: 1.0,
    LEVEL_L4: 1.2,
    LEVEL_L5: 1.5,
}


# ============================================================
# 流水类型
# ============================================================

LOG_TYPE_EARN = "earn"           # 加分
LOG_TYPE_DEDUCT = "deduct"       # 扣分
LOG_TYPE_ADJUST = "adjust"       # 人工调整
LOG_TYPE_UPGRADE = "upgrade"     # 升级
LOG_TYPE_DOWNGRADE = "downgrade"  # 降级
LOG_TYPE_BLACKLIST = "blacklist"  # 黑名单
LOG_TYPE_RESTORE = "restore"      # 恢复

# 账户状态
STATUS_NORMAL = "normal"         # 正常
STATUS_FROZEN = "frozen"         # 冻结
STATUS_BLACKLIST = "blacklist"   # 黑名单

# 角色类型
ROLE_MEMBER = "member"           # 会员
ROLE_AGENT = "agent"             # 代理商
ROLE_PARTNER = "partner"         # 合作方
ROLE_DISTRIBUTOR = "distributor"  # 分销商
ROLE_CUSTOM = "custom"           # 定制客户

# 各角色起始分
ROLE_INITIAL_SCORE = {
    ROLE_MEMBER: 350,
    ROLE_AGENT: 500,
    ROLE_PARTNER: 500,
    ROLE_DISTRIBUTOR: 450,
    ROLE_CUSTOM: 400,
}


def level_from_score(score: int) -> str:
    """根据竹信分返回信用等级"""
    for threshold, level in LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return LEVEL_L1


def clamp_score(score: int) -> int:
    """竹信分上下限: 0 ≤ 竹信分 ≤ 1000"""
    return max(0, min(1000, score))


class CreditRepository:
    """信用管理数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_log_id(self) -> int:
        """生成信用流水ID"""
        if is_redis_mode():
            return await self._redis_next_id("credit_log")
        return self._mem_next_id("_credit_log_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("credit", entity, "seq"))

    # ============================================================
    # 信用分账户 CRUD
    # ============================================================

    async def get_score(self, user_id: int) -> Optional[dict]:
        """查询信用分账户(不存在返回None)"""
        if is_redis_mode():
            return await self._redis_get_score(user_id)
        return self._mem_get_score(user_id)

    async def save_score(self, account: dict) -> None:
        """保存信用分账户(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_score(account)
        else:
            self._mem_save_score(account)

    async def create_score(self, user_id: int, role_type: str = ROLE_MEMBER) -> dict:
        """创建信用分账户(按角色起始分)"""
        now = datetime.utcnow().isoformat()
        initial = ROLE_INITIAL_SCORE.get(role_type, 350)
        level = level_from_score(initial)
        account = {
            "userId": user_id,
            "roleType": role_type,
            "bambooScore": initial,
            "creditLevel": level,
            "creditPoints": 0,
            "totalEarned": 0,
            "totalRewarded": 0.0,
            "paylaterQuota": LEVEL_PAYLATER_QUOTA.get(level, 0),
            "paylaterUsed": 0.0,
            "status": STATUS_NORMAL,
            "version": 0,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.save_score(account)
        return account

    async def get_or_create_score(self, user_id: int, role_type: str = ROLE_MEMBER) -> dict:
        """获取或创建信用分账户"""
        account = await self.get_score(user_id)
        if account is None:
            account = await self.create_score(user_id, role_type)
        return account

    async def list_scores(self, status: str = None, role_type: str = None,
                          limit: int = 100) -> list[dict]:
        """查询信用分账户列表(支持按状态/角色筛选)"""
        if is_redis_mode():
            return await self._redis_list_scores(status, role_type, limit)
        return self._mem_list_scores(status, role_type, limit)

    # ============================================================
    # 信用流水 CRUD
    # ============================================================

    async def add_log(self, log: dict) -> int:
        """新增信用流水(返回流水ID)"""
        log_id = await self.next_log_id()
        log["id"] = log_id
        if "createdAt" not in log:
            log["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_log(log)
        else:
            self._mem_add_log(log)
        return log_id

    async def get_log(self, log_id: int) -> Optional[dict]:
        """按ID查询流水"""
        if is_redis_mode():
            return await self._redis_get_log(log_id)
        return self._mem_get_log(log_id)

    async def list_logs(self, user_id: int, log_type: str = None,
                        limit: int = 50) -> list[dict]:
        """查询用户信用流水(支持按类型筛选)"""
        if is_redis_mode():
            return await self._redis_list_logs(user_id, log_type, limit)
        return self._mem_list_logs(user_id, log_type, limit)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含信用模块的键(懒初始化)"""
        if "credit_scores" not in self.store:
            self.store["credit_scores"] = {}          # userId → account
            self.store["credit_logs"] = {}            # logId → log
            self.store["credit_logs_by_user"] = {}    # userId → [logId, ...]
            self.store["_credit_log_seq"] = 0

    # --- 账户 ---

    def _mem_get_score(self, user_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["credit_scores"].get(user_id)

    def _mem_save_score(self, account: dict) -> None:
        self._ensure_store()
        user_id = account["userId"]
        account["version"] = account.get("version", 0) + 1
        account["updatedAt"] = datetime.utcnow().isoformat()
        self.store["credit_scores"][user_id] = account

    def _mem_list_scores(self, status: str = None, role_type: str = None,
                         limit: int = 100) -> list[dict]:
        self._ensure_store()
        accounts = list(self.store["credit_scores"].values())
        if status:
            accounts = [a for a in accounts if a.get("status") == status]
        if role_type:
            accounts = [a for a in accounts if a.get("roleType") == role_type]
        accounts.sort(key=lambda a: a.get("bambooScore", 0), reverse=True)
        return accounts[:limit]

    # --- 流水 ---

    def _mem_add_log(self, log: dict) -> None:
        self._ensure_store()
        log_id = log["id"]
        user_id = log["userId"]
        self.store["credit_logs"][log_id] = log
        if user_id not in self.store["credit_logs_by_user"]:
            self.store["credit_logs_by_user"][user_id] = []
        self.store["credit_logs_by_user"][user_id].append(log_id)

    def _mem_get_log(self, log_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["credit_logs"].get(log_id)

    def _mem_list_logs(self, user_id: int, log_type: str = None,
                       limit: int = 50) -> list[dict]:
        self._ensure_store()
        log_ids = self.store["credit_logs_by_user"].get(user_id, [])
        logs = [self.store["credit_logs"][lid] for lid in log_ids
                if lid in self.store["credit_logs"]]
        if log_type:
            logs = [l for l in logs if l.get("type") == log_type]
        logs.sort(key=lambda l: l.get("createdAt", ""), reverse=True)
        return logs[:limit]

    # ============================================================
    # Redis 模式实现
    # ============================================================

    async def _redis_get_score(self, user_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("credit", "score", user_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_score(self, account: dict) -> None:
        client = await get_redis_client()
        user_id = account["userId"]
        account["version"] = account.get("version", 0) + 1
        account["updatedAt"] = datetime.utcnow().isoformat()
        await client.set(_k("credit", "score", user_id),
                         json.dumps(account, ensure_ascii=False))

    async def _redis_list_scores(self, status: str = None, role_type: str = None,
                                 limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("credit", "score", "*"))
        accounts = []
        for key in keys:
            data = await client.get(key)
            if data:
                account = json.loads(data)
                if status and account.get("status") != status:
                    continue
                if role_type and account.get("roleType") != role_type:
                    continue
                accounts.append(account)
        accounts.sort(key=lambda a: a.get("bambooScore", 0), reverse=True)
        return accounts[:limit]

    async def _redis_add_log(self, log: dict) -> None:
        client = await get_redis_client()
        log_id = log["id"]
        user_id = log["userId"]
        await client.set(_k("credit", "log", log_id),
                         json.dumps(log, ensure_ascii=False))
        await client.lpush(_k("credit", "logs_by_user", user_id), log_id)

    async def _redis_get_log(self, log_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("credit", "log", log_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_logs(self, user_id: int, log_type: str = None,
                               limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        log_ids = await client.lrange(_k("credit", "logs_by_user", user_id), 0, -1)
        logs = []
        for lid in log_ids:
            data = await client.get(_k("credit", "log", lid))
            if data:
                log = json.loads(data)
                if log_type and log.get("type") != log_type:
                    continue
                logs.append(log)
        return logs[:limit]
