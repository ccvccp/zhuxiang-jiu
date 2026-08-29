"""会员积分管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    P0: points_account(积分账户) + points_logs(积分流水)
        + points_signin(签到记录) + points_expire(积分过期)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 积分账户: 按 user_id 主键, version 乐观锁
    - 流水自增ID: 内存计数器 / Redis INCR
    - 签到幂等: (user_id, sign_date) 唯一索引
    - FIFO 过期: 按 expire_at ASC 消耗
"""

import json
from datetime import datetime, date

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 积分流水类型
# ============================================================

LOG_TYPE_EARN = "earn"          # 获取
LOG_TYPE_SPEND = "spend"        # 消耗
LOG_TYPE_FREEZE = "freeze"      # 冻结
LOG_TYPE_UNFREEZE = "unfreeze"  # 解冻

# 积分来源
SOURCE_CHECKIN = "checkin"        # 签到
SOURCE_ORDER = "order"            # 消费返分
SOURCE_REFERRAL = "referral"      # 推荐
SOURCE_CREDIT = "credit"          # 信用
SOURCE_REVIEW = "review"          # 评价
SOURCE_TRANSACTION = "transaction"  # 交易
SOURCE_PROMO = "promo"            # 推广
SOURCE_ACTIVATION = "activation"  # 生命码激活奖励
SOURCE_REGISTER = "register"      # 注册赠送
SOURCE_LOGIN = "login"            # 每日登录奖励
SOURCE_REFUND = "refund"          # 退款返还
SOURCE_EXPIRE = "expire"           # 过期
SOURCE_DEDUCT = "deduct"          # 抵扣

# 流水状态
LOG_STATUS_FROZEN = 0       # 冻结
LOG_STATUS_AVAILABLE = 1     # 可用
LOG_STATUS_EXPIRED = 2       # 已过期
LOG_STATUS_CONSUMED = 3      # 已消耗

# 过期批次状态
EXPIRE_STATUS_ACTIVE = 0     # 未过期
EXPIRE_STATUS_EXPIRED = 1    # 已过期
EXPIRE_STATUS_CONSUMED = 2   # 已消耗


class PointsRepository:
    """会员积分数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_log_id(self) -> int:
        """生成积分流水ID"""
        if is_redis_mode():
            return await self._redis_next_id("points_log")
        return self._mem_next_id("_points_log_seq")

    async def next_expire_id(self) -> int:
        """生成过期批次ID"""
        if is_redis_mode():
            return await self._redis_next_id("points_expire")
        return self._mem_next_id("_points_expire_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("points", entity, "seq"))

    # ============================================================
    # 积分账户 CRUD
    # ============================================================

    async def get_account(self, user_id: int) -> dict | None:
        """查询积分账户(不存在返回None)"""
        if is_redis_mode():
            return await self._redis_get_account(user_id)
        return self._mem_get_account(user_id)

    async def save_account(self, account: dict) -> None:
        """保存积分账户(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_account(account)
        else:
            self._mem_save_account(account)

    async def create_account(self, user_id: int) -> dict:
        """创建积分账户(初始余额0)"""
        now = datetime.utcnow().isoformat()
        account = {
            "userId": user_id,
            "totalPoints": 0,
            "frozenPoints": 0,
            "totalEarned": 0,
            "totalSpent": 0,
            "expiringPoints": 0,
            "version": 0,
            "updatedAt": now,
        }
        await self.save_account(account)
        return account

    async def get_or_create_account(self, user_id: int) -> dict:
        """获取或创建积分账户"""
        account = await self.get_account(user_id)
        if account is None:
            account = await self.create_account(user_id)
        return account

    # ============================================================
    # 积分流水 CRUD
    # ============================================================

    async def add_log(self, log: dict) -> int:
        """新增积分流水(返回流水ID)"""
        log_id = await self.next_log_id()
        log["id"] = log_id
        if "createdAt" not in log:
            log["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_log(log)
        else:
            self._mem_add_log(log)
        return log_id

    async def get_log(self, log_id: int) -> dict | None:
        """按ID查询流水"""
        if is_redis_mode():
            return await self._redis_get_log(log_id)
        return self._mem_get_log(log_id)

    async def list_logs(self, user_id: int, source: str = None,
                        log_type: str = None, limit: int = 50) -> list[dict]:
        """查询用户积分流水(支持按来源/类型筛选)"""
        if is_redis_mode():
            return await self._redis_list_logs(user_id, source, log_type, limit)
        return self._mem_list_logs(user_id, source, log_type, limit)

    async def update_log_status(self, log_id: int, status: int) -> None:
        """更新流水状态"""
        if is_redis_mode():
            await self._redis_update_log_status(log_id, status)
        else:
            self._mem_update_log_status(log_id, status)

    # ============================================================
    # 签到记录 CRUD
    # ============================================================

    async def add_signin(self, signin: dict) -> int:
        """新增签到记录(返回ID)"""
        if is_redis_mode():
            return await self._redis_add_signin(signin)
        return self._mem_add_signin(signin)

    async def get_signin(self, user_id: int, sign_date: date) -> dict | None:
        """按用户+日期查询签到记录(幂等校验)"""
        date_str = sign_date.isoformat() if isinstance(sign_date, date) else sign_date
        if is_redis_mode():
            return await self._redis_get_signin(user_id, date_str)
        return self._mem_get_signin(user_id, date_str)

    async def get_last_signin(self, user_id: int) -> dict | None:
        """查询用户最近一次签到记录"""
        if is_redis_mode():
            return await self._redis_get_last_signin(user_id)
        return self._mem_get_last_signin(user_id)

    async def list_signins(self, user_id: int, limit: int = 30) -> list[dict]:
        """查询用户签到记录(按日期倒序)"""
        if is_redis_mode():
            return await self._redis_list_signins(user_id, limit)
        return self._mem_list_signins(user_id, limit)

    # ============================================================
    # 积分过期批次 CRUD
    # ============================================================

    async def add_expire_batch(self, batch: dict) -> int:
        """新增积分过期批次(返回批次ID)"""
        batch_id = await self.next_expire_id()
        batch["id"] = batch_id
        if "createdAt" not in batch:
            batch["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_expire_batch(batch)
        else:
            self._mem_add_expire_batch(batch)
        return batch_id

    async def list_expiring_batches(self, user_id: int) -> list[dict]:
        """查询用户未过期的积分批次(按过期时间升序, FIFO消耗)"""
        if is_redis_mode():
            return await self._redis_list_expiring_batches(user_id)
        return self._mem_list_expiring_batches(user_id)

    async def list_expiring_soon(self, user_id: int, days: int = 30) -> list[dict]:
        """查询用户N天内将过期的批次"""
        if is_redis_mode():
            return await self._redis_list_expiring_soon(user_id, days)
        return self._mem_list_expiring_soon(user_id, days)

    async def update_expire_batch(self, batch_id: int, consumed_points: int,
                                   status: int) -> None:
        """更新过期批次(已消耗数量+状态)"""
        if is_redis_mode():
            await self._redis_update_expire_batch(batch_id, consumed_points, status)
        else:
            self._mem_update_expire_batch(batch_id, consumed_points, status)

    async def list_expired_batches(self, before_date: datetime) -> list[dict]:
        """查询已过期但状态未更新的批次(过期扫描用)"""
        if is_redis_mode():
            return await self._redis_list_expired_batches(before_date)
        return self._mem_list_expired_batches(before_date)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含积分模块的键(懒初始化)"""
        if "points_accounts" not in self.store:
            self.store["points_accounts"] = {}              # userId → account
            self.store["points_logs"] = {}                  # logId → log
            self.store["points_logs_by_user"] = {}          # userId → [logId, ...]
            self.store["points_signins"] = {}               # userId → {dateStr → signin}
            self.store["points_expire_batches"] = {}        # batchId → batch
            self.store["points_expire_by_user"] = {}        # userId → [batchId, ...]
            self.store["_points_log_seq"] = 0
            self.store["_points_expire_seq"] = 0

    # --- 账户 ---

    def _mem_get_account(self, user_id: int) -> dict | None:
        self._ensure_store()
        return self.store["points_accounts"].get(user_id)

    def _mem_save_account(self, account: dict) -> None:
        self._ensure_store()
        user_id = account["userId"]
        account["version"] = account.get("version", 0) + 1
        account["updatedAt"] = datetime.utcnow().isoformat()
        self.store["points_accounts"][user_id] = account

    # --- 流水 ---

    def _mem_add_log(self, log: dict) -> None:
        self._ensure_store()
        log_id = log["id"]
        user_id = log["userId"]
        self.store["points_logs"][log_id] = log
        if user_id not in self.store["points_logs_by_user"]:
            self.store["points_logs_by_user"][user_id] = []
        self.store["points_logs_by_user"][user_id].append(log_id)

    def _mem_get_log(self, log_id: int) -> dict | None:
        self._ensure_store()
        return self.store["points_logs"].get(log_id)

    def _mem_list_logs(self, user_id: int, source: str = None,
                       log_type: str = None, limit: int = 50) -> list[dict]:
        self._ensure_store()
        log_ids = self.store["points_logs_by_user"].get(user_id, [])
        logs = [self.store["points_logs"][lid] for lid in log_ids
                if lid in self.store["points_logs"]]
        if source:
            logs = [l for l in logs if l.get("source") == source]
        if log_type:
            logs = [l for l in logs if l.get("type") == log_type]
        logs.sort(key=lambda l: l.get("createdAt", ""), reverse=True)
        return logs[:limit]

    def _mem_update_log_status(self, log_id: int, status: int) -> None:
        self._ensure_store()
        log = self.store["points_logs"].get(log_id)
        if log:
            log["status"] = status

    # --- 签到 ---

    def _mem_add_signin(self, signin: dict) -> int:
        self._ensure_store()
        user_id = signin["userId"]
        date_str = signin["signDate"]
        if user_id not in self.store["points_signins"]:
            self.store["points_signins"][user_id] = {}
        # 生成ID
        seq = self.store.get("_points_signin_seq", 0) + 1
        self.store["_points_signin_seq"] = seq
        signin["id"] = seq
        signin["createdAt"] = datetime.utcnow().isoformat()
        self.store["points_signins"][user_id][date_str] = signin
        return seq

    def _mem_get_signin(self, user_id: int, date_str: str) -> dict | None:
        self._ensure_store()
        return self.store["points_signins"].get(user_id, {}).get(date_str)

    def _mem_get_last_signin(self, user_id: int) -> dict | None:
        self._ensure_store()
        signins = self.store["points_signins"].get(user_id, {})
        if not signins:
            return None
        # 按日期倒序取最新
        sorted_dates = sorted(signins.keys(), reverse=True)
        return signins[sorted_dates[0]]

    def _mem_list_signins(self, user_id: int, limit: int = 30) -> list[dict]:
        self._ensure_store()
        signins = self.store["points_signins"].get(user_id, {})
        result = list(signins.values())
        result.sort(key=lambda s: s.get("signDate", ""), reverse=True)
        return result[:limit]

    # --- 过期批次 ---

    def _mem_add_expire_batch(self, batch: dict) -> None:
        self._ensure_store()
        batch_id = batch["id"]
        user_id = batch["userId"]
        self.store["points_expire_batches"][batch_id] = batch
        if user_id not in self.store["points_expire_by_user"]:
            self.store["points_expire_by_user"][user_id] = []
        self.store["points_expire_by_user"][user_id].append(batch_id)

    def _mem_list_expiring_batches(self, user_id: int) -> list[dict]:
        self._ensure_store()
        batch_ids = self.store["points_expire_by_user"].get(user_id, [])
        batches = [self.store["points_expire_batches"][bid] for bid in batch_ids
                   if bid in self.store["points_expire_batches"]]
        # 仅未过期且未消耗完
        active = [b for b in batches if b.get("status") == EXPIRE_STATUS_ACTIVE
                  and b.get("consumedPoints", 0) < b.get("points", 0)]
        # 按过期时间升序(FIFO)
        active.sort(key=lambda b: b.get("expireAt", ""))
        return active

    def _mem_list_expiring_soon(self, user_id: int, days: int = 30) -> list[dict]:
        self._ensure_store()
        batches = self._mem_list_expiring_batches(user_id)
        now = datetime.utcnow()
        threshold = datetime(now.year, now.month, now.day).isoformat()
        from datetime import timedelta
        deadline = (now + timedelta(days=days)).isoformat()
        return [b for b in batches if b.get("expireAt", "") <= deadline
                and b.get("expireAt", "") >= threshold]

    def _mem_update_expire_batch(self, batch_id: int, consumed_points: int,
                                  status: int) -> None:
        self._ensure_store()
        batch = self.store["points_expire_batches"].get(batch_id)
        if batch:
            batch["consumedPoints"] = consumed_points
            batch["status"] = status

    def _mem_list_expired_batches(self, before_date: datetime) -> list[dict]:
        self._ensure_store()
        threshold = before_date.isoformat()
        result = []
        for batch in self.store["points_expire_batches"].values():
            if (batch.get("status") == EXPIRE_STATUS_ACTIVE
                    and batch.get("expireAt", "") < threshold
                    and batch.get("consumedPoints", 0) < batch.get("points", 0)):
                result.append(batch)
        return result

    # ============================================================
    # Redis 模式实现
    # ============================================================

    # --- 账户 ---

    async def _redis_get_account(self, user_id: int) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("points", "account", user_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_account(self, account: dict) -> None:
        client = await get_redis_client()
        user_id = account["userId"]
        account["version"] = account.get("version", 0) + 1
        account["updatedAt"] = datetime.utcnow().isoformat()
        await client.set(_k("points", "account", user_id),
                         json.dumps(account, ensure_ascii=False))

    # --- 流水 ---

    async def _redis_add_log(self, log: dict) -> None:
        client = await get_redis_client()
        log_id = log["id"]
        user_id = log["userId"]
        await client.set(_k("points", "log", log_id),
                         json.dumps(log, ensure_ascii=False))
        await client.lpush(_k("points", "logs_by_user", user_id), log_id)

    async def _redis_get_log(self, log_id: int) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("points", "log", log_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_logs(self, user_id: int, source: str = None,
                                log_type: str = None, limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        log_ids = await client.lrange(_k("points", "logs_by_user", user_id), 0, -1)
        logs = []
        for lid in log_ids:
            data = await client.get(_k("points", "log", lid))
            if data:
                log = json.loads(data)
                if source and log.get("source") != source:
                    continue
                if log_type and log.get("type") != log_type:
                    continue
                logs.append(log)
        return logs[:limit]

    async def _redis_update_log_status(self, log_id: int, status: int) -> None:
        client = await get_redis_client()
        data = await client.get(_k("points", "log", log_id))
        if data:
            log = json.loads(data)
            log["status"] = status
            await client.set(_k("points", "log", log_id),
                             json.dumps(log, ensure_ascii=False))

    # --- 签到 ---

    async def _redis_add_signin(self, signin: dict) -> int:
        client = await get_redis_client()
        user_id = signin["userId"]
        date_str = signin["signDate"]
        seq = await client.incr(_k("points", "signin", "seq"))
        signin["id"] = seq
        signin["createdAt"] = datetime.utcnow().isoformat()
        await client.hset(_k("points", "signins", user_id), date_str,
                          json.dumps(signin, ensure_ascii=False))
        await client.lpush(_k("points", "signin_list", user_id), date_str)
        return seq

    async def _redis_get_signin(self, user_id: int, date_str: str) -> dict | None:
        client = await get_redis_client()
        data = await client.hget(_k("points", "signins", user_id), date_str)
        if not data:
            return None
        return json.loads(data)

    async def _redis_get_last_signin(self, user_id: int) -> dict | None:
        client = await get_redis_client()
        date_strs = await client.lrange(_k("points", "signin_list", user_id), 0, 0)
        if not date_strs:
            return None
        return await self._redis_get_signin(user_id, date_strs[0])

    async def _redis_list_signins(self, user_id: int, limit: int = 30) -> list[dict]:
        client = await get_redis_client()
        date_strs = await client.lrange(_k("points", "signin_list", user_id), 0, limit - 1)
        result = []
        for ds in date_strs:
            data = await client.hget(_k("points", "signins", user_id), ds)
            if data:
                result.append(json.loads(data))
        return result

    # --- 过期批次 ---

    async def _redis_add_expire_batch(self, batch: dict) -> None:
        client = await get_redis_client()
        batch_id = batch["id"]
        user_id = batch["userId"]
        await client.set(_k("points", "expire_batch", batch_id),
                         json.dumps(batch, ensure_ascii=False))
        await client.lpush(_k("points", "expire_by_user", user_id), batch_id)

    async def _redis_list_expiring_batches(self, user_id: int) -> list[dict]:
        client = await get_redis_client()
        batch_ids = await client.lrange(_k("points", "expire_by_user", user_id), 0, -1)
        batches = []
        for bid in batch_ids:
            data = await client.get(_k("points", "expire_batch", bid))
            if data:
                b = json.loads(data)
                if (b.get("status") == EXPIRE_STATUS_ACTIVE
                        and b.get("consumedPoints", 0) < b.get("points", 0)):
                    batches.append(b)
        batches.sort(key=lambda b: b.get("expireAt", ""))
        return batches

    async def _redis_list_expiring_soon(self, user_id: int, days: int = 30) -> list[dict]:
        batches = await self._redis_list_expiring_batches(user_id)
        from datetime import timedelta
        now = datetime.utcnow()
        threshold = datetime(now.year, now.month, now.day).isoformat()
        deadline = (now + timedelta(days=days)).isoformat()
        return [b for b in batches if b.get("expireAt", "") <= deadline
                and b.get("expireAt", "") >= threshold]

    async def _redis_update_expire_batch(self, batch_id: int, consumed_points: int,
                                          status: int) -> None:
        client = await get_redis_client()
        data = await client.get(_k("points", "expire_batch", batch_id))
        if data:
            batch = json.loads(data)
            batch["consumedPoints"] = consumed_points
            batch["status"] = status
            await client.set(_k("points", "expire_batch", batch_id),
                             json.dumps(batch, ensure_ascii=False))

    async def _redis_list_expired_batches(self, before_date: datetime) -> list[dict]:
        client = await get_redis_client()
        threshold = before_date.isoformat()
        result = []
        # 扫描所有过期批次(生产环境应用 SCAN)
        keys = await client.keys(_k("points", "expire_batch", "*"))
        for key in keys:
            data = await client.get(key)
            if data:
                batch = json.loads(data)
                if (batch.get("status") == EXPIRE_STATUS_ACTIVE
                        and batch.get("expireAt", "") < threshold
                        and batch.get("consumedPoints", 0) < batch.get("points", 0)):
                    result.append(batch)
        return result
