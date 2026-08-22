"""钱包盈利模块 Repository

双模式(内存/Redis)透明切换,5 个数据实体:
    - accounts(钱包账户):       主信息, user_id 唯一, 按用户ID查询
    - transactions(交易记录):   主信息, 按用户ID索引, 按类型筛选
    - withdrawals(提现记录):     主信息, 按用户ID索引, 待审核集合
    - deposits(定期预付):        主信息, 按用户ID索引
    - rewards(奖品领取):         主信息, 按用户ID索引, 可领取集合

锁键: wallet:{userId} / wallet:withdraw:{withdrawNo} / wallet:deposit:{depositNo}
     (并发安全由 services 层负责)

Redis Key 设计:
    wallet:{userId}                 Hash(钱包账户主信息)
    wallet:index                    Set(全部钱包用户ID集合, list_all 用)
    wallet:tx:{txNo}                String(JSON)(交易记录详情)
    wallet:tx:index:{userId}        Set(用户交易编号集合)
    wallet:tx:seq                   String(INCR 交易序列)
    wallet:withdraw:{withdrawNo}    String(JSON)(提现记录详情)
    wallet:withdraw:index:{userId}  Set(用户提现编号集合)
    wallet:withdraw:pending         Set(待审核提现编号集合)
    wallet:withdraw:seq             String(INCR 提现序列)
    wallet:deposit:{depositNo}      String(JSON)(定期记录详情)
    wallet:deposit:index:{userId}   Set(用户定期编号集合)
    wallet:deposit:seq              String(INCR 定期序列)
    wallet:reward:{rewardNo}        String(JSON)(奖品记录详情)
    wallet:reward:index:{userId}    Set(用户奖品编号集合)
    wallet:reward:claimable         Set(可领取奖品编号集合)
    wallet:reward:seq               String(INCR 奖品序列)
"""

import json
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 序列号格式辅助(与 finance_repository 风格一致)
# ============================================================

def _tx_no_prefix() -> str:
    """交易编号前缀: WT + YYYYMMDD"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"WT{now.strftime('%Y%m%d')}"


def _withdraw_no_prefix() -> str:
    """提现编号前缀: WD + YYYYMMDD"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"WD{now.strftime('%Y%m%d')}"


def _deposit_no_prefix() -> str:
    """定期编号前缀: DP + YYYYMMDD"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"DP{now.strftime('%Y%m%d')}"


def _reward_no_prefix() -> str:
    """奖品编号前缀: RW + YYYYMMDD"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"RW{now.strftime('%Y%m%d')}"


# 钱包状态常量
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_FROZEN = "frozen"
STATUS_CLOSED = "closed"


class WalletRepository:
    """钱包数据访问(双模式)"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成(按前缀 INCR, 序号补 4 位)
    # ============================================================

    async def next_tx_no(self) -> str:
        """生成下一个交易编号: WT + YYYYMMDD + 4 位序号"""
        if is_redis_mode():
            return await self._redis_next_seq_no("tx:seq", _tx_no_prefix())
        return self._mem_next_seq_no("_wallet_tx_seq", _tx_no_prefix())

    async def next_withdraw_no(self) -> str:
        """生成下一个提现编号: WD + YYYYMMDD + 4 位序号"""
        if is_redis_mode():
            return await self._redis_next_seq_no("withdraw:seq", _withdraw_no_prefix())
        return self._mem_next_seq_no("_wallet_withdraw_seq", _withdraw_no_prefix())

    async def next_deposit_no(self) -> str:
        """生成下一个定期编号: DP + YYYYMMDD + 4 位序号"""
        if is_redis_mode():
            return await self._redis_next_seq_no("deposit:seq", _deposit_no_prefix())
        return self._mem_next_seq_no("_wallet_deposit_seq", _deposit_no_prefix())

    async def next_reward_no(self) -> str:
        """生成下一个奖品编号: RW + YYYYMMDD + 4 位序号"""
        if is_redis_mode():
            return await self._redis_next_seq_no("reward:seq", _reward_no_prefix())
        return self._mem_next_seq_no("_wallet_reward_seq", _reward_no_prefix())

    def _mem_next_seq_no(self, counter_key: str, prefix: str) -> str:
        """内存模式序列号生成(全局计数器, 按 prefix 复用)"""
        self._ensure_store()
        self.store[counter_key] = self.store.get(counter_key, 0) + 1
        return f"{prefix}{self.store[counter_key]:04d}"

    async def _redis_next_seq_no(self, seq_key: str, prefix: str) -> str:
        """Redis 模式序列号生成(INCR 原子自增)"""
        client = await get_redis_client()
        # 同一 prefix 共用一个计数器, 避免不同日期 prefix 复用导致重号
        full_key = _k("wallet", seq_key, prefix)
        n = await client.incr(full_key)
        return f"{prefix}{n:04d}"

    # ============================================================
    # 钱包账户(wallets)
    # ============================================================

    async def open_account(self, user_id, account_data: dict) -> dict:
        """开通钱包账户(user_id 唯一约束)

        Raises:
            ValueError: 该用户已开通钱包
        """
        if is_redis_mode():
            return await self._redis_open_account(user_id, account_data)
        return self._mem_open_account(user_id, account_data)

    async def get_account(self, user_id) -> Optional[dict]:
        """按 user_id 查询钱包账户, 不存在返回 None"""
        if is_redis_mode():
            return await self._redis_get_account(user_id)
        return self._mem_get_account(user_id)

    async def list_accounts(self, status: str = None) -> list[dict]:
        """列出所有钱包账户(可按 status 筛选)"""
        if is_redis_mode():
            return await self._redis_list_accounts(status)
        return self._mem_list_accounts(status)

    async def save_account(self, user_id, account_data: dict) -> dict:
        """新增/覆盖钱包账户(保留 user_id)"""
        if is_redis_mode():
            return await self._redis_save_account(user_id, account_data)
        return self._mem_save_account(user_id, account_data)

    async def update_account_fields(self, user_id, fields: dict) -> dict:
        """部分字段更新, 返回更新后的完整账户

        Raises:
            KeyError: 钱包账户不存在
        """
        if is_redis_mode():
            return await self._redis_update_account_fields(user_id, fields)
        return self._mem_update_account_fields(user_id, fields)

    async def add_balance(self, user_id, amount: float) -> float:
        """活期余额累加(amount 可正可负), 返回新余额

        Raises:
            KeyError: 钱包账户不存在
            ValueError: 余额不足(amount < 0 时)
        """
        if is_redis_mode():
            return await self._redis_add_balance(user_id, amount)
        return self._mem_add_balance(user_id, amount)

    async def get_balance(self, user_id) -> float:
        """查询活期余额

        Raises:
            KeyError: 钱包账户不存在
        """
        if is_redis_mode():
            return await self._redis_get_balance(user_id)
        return self._mem_get_balance(user_id)

    async def add_frozen(self, user_id, amount: float) -> float:
        """冻结金额累加(提现申请时冻结, amount >= 0), 返回新冻结金额

        Raises:
            KeyError: 钱包账户不存在
            ValueError: 冻结金额不足
        """
        if is_redis_mode():
            return await self._redis_add_frozen(user_id, amount)
        return self._mem_add_frozen(user_id, amount)

    async def add_reward_balance(self, user_id, amount: float) -> float:
        """奖励余额累加(推广等奖励入账, 只可购物不可提现, amount 可正可负)

        与活期 balance 完全隔离: 提现只操作 balance/frozenAmount。

        Raises:
            KeyError: 钱包账户不存在
            ValueError: 奖励余额不足(amount < 0 时)
        """
        if is_redis_mode():
            return await self._redis_add_reward_balance(user_id, amount)
        return self._mem_add_reward_balance(user_id, amount)

    async def get_reward_balance(self, user_id) -> float:
        """查询奖励余额(不可提现)

        Raises:
            KeyError: 钱包账户不存在
        """
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("wallet", user_id)
            if not await client.exists(key):
                raise KeyError(user_id)
            return float(await client.hget(key, "rewardBalance") or 0)
        self._ensure_store()
        account = self.store["wallets"].get(user_id)
        if not account:
            raise KeyError(user_id)
        return float(account.get("rewardBalance", 0))

    async def reduce_frozen(self, user_id, amount: float) -> float:
        """冻结金额扣减(提现完成/拒绝时释放, amount >= 0), 返回新冻结金额

        Raises:
            KeyError: 钱包账户不存在
            ValueError: 冻结金额不足
        """
        if is_redis_mode():
            return await self._redis_reduce_frozen(user_id, amount)
        return self._mem_reduce_frozen(user_id, amount)

    async def get_status(self, user_id) -> str:
        """查询钱包状态

        Raises:
            KeyError: 钱包账户不存在
        """
        if is_redis_mode():
            return await self._redis_get_status(user_id)
        return self._mem_get_status(user_id)

    async def update_status(self, user_id, new_status: str) -> str:
        """更新钱包状态, 返回旧状态

        Raises:
            KeyError: 钱包账户不存在
        """
        if is_redis_mode():
            return await self._redis_update_status(user_id, new_status)
        return self._mem_update_status(user_id, new_status)

    # ============================================================
    # 交易记录(wallet_transactions)
    # ============================================================

    async def save_transaction(self, tx: dict) -> dict:
        """新增交易记录(含 user_id 索引)"""
        if is_redis_mode():
            return await self._redis_save_transaction(tx)
        return self._mem_save_transaction(tx)

    async def get_transaction(self, tx_no: str) -> Optional[dict]:
        """按交易编号查询"""
        if is_redis_mode():
            return await self._redis_get_transaction(tx_no)
        return self._mem_get_transaction(tx_no)

    async def list_transactions(self, user_id, tx_type: str = None,
                                 status: str = None, limit: int = 50) -> list[dict]:
        """列出用户交易记录(可按 type/status 筛选, 默认最新 50 条)"""
        if is_redis_mode():
            return await self._redis_list_transactions(user_id, tx_type, status, limit)
        return self._mem_list_transactions(user_id, tx_type, status, limit)

    async def update_transaction_fields(self, tx_no: str, fields: dict) -> dict:
        """部分字段更新(如 status: processing → success)

        Raises:
            KeyError: 交易记录不存在
        """
        if is_redis_mode():
            return await self._redis_update_transaction_fields(tx_no, fields)
        return self._mem_update_transaction_fields(tx_no, fields)

    # ============================================================
    # 提现记录(wallet_withdrawals)
    # ============================================================

    async def save_withdrawal(self, withdraw: dict) -> dict:
        """新增提现记录(含 user_id 索引 + pending 集合)"""
        if is_redis_mode():
            return await self._redis_save_withdrawal(withdraw)
        return self._mem_save_withdrawal(withdraw)

    async def get_withdrawal(self, withdraw_no: str) -> Optional[dict]:
        """按提现编号查询"""
        if is_redis_mode():
            return await self._redis_get_withdrawal(withdraw_no)
        return self._mem_get_withdrawal(withdraw_no)

    async def list_withdrawals(self, user_id, status: str = None,
                                limit: int = 50) -> list[dict]:
        """列出用户提现记录(可按 status 筛选)"""
        if is_redis_mode():
            return await self._redis_list_withdrawals(user_id, status, limit)
        return self._mem_list_withdrawals(user_id, status, limit)

    async def list_pending_withdrawals(self, limit: int = 100) -> list[dict]:
        """列出待审核提现(管理端审批用)"""
        if is_redis_mode():
            return await self._redis_list_pending_withdrawals(limit)
        return self._mem_list_pending_withdrawals(limit)

    async def update_withdrawal_fields(self, withdraw_no: str, fields: dict) -> dict:
        """部分字段更新(如 status: pending → approved → paid)

        - 若 status 从 pending 变为非 pending, 自动从 pending 集合移除
        - 若 status 变为 pending, 自动加入 pending 集合

        Raises:
            KeyError: 提现记录不存在
        """
        if is_redis_mode():
            return await self._redis_update_withdrawal_fields(withdraw_no, fields)
        return self._mem_update_withdrawal_fields(withdraw_no, fields)

    # ============================================================
    # 定期预付(wallet_deposits) · P1
    # ============================================================

    async def save_deposit(self, deposit: dict) -> dict:
        """新增定期记录(含 user_id 索引)"""
        if is_redis_mode():
            return await self._redis_save_deposit(deposit)
        return self._mem_save_deposit(deposit)

    async def get_deposit(self, deposit_no: str) -> Optional[dict]:
        """按定期编号查询"""
        if is_redis_mode():
            return await self._redis_get_deposit(deposit_no)
        return self._mem_get_deposit(deposit_no)

    async def list_deposits(self, user_id, status: str = None,
                             limit: int = 50) -> list[dict]:
        """列出用户定期记录(可按 status 筛选)"""
        if is_redis_mode():
            return await self._redis_list_deposits(user_id, status, limit)
        return self._mem_list_deposits(user_id, status, limit)

    async def update_deposit_fields(self, deposit_no: str, fields: dict) -> dict:
        """部分字段更新(如 status: active → matured → settled)

        Raises:
            KeyError: 定期记录不存在
        """
        if is_redis_mode():
            return await self._redis_update_deposit_fields(deposit_no, fields)
        return self._mem_update_deposit_fields(deposit_no, fields)

    # ============================================================
    # 奖品领取(wallet_rewards) · P1
    # ============================================================

    async def save_reward(self, reward: dict) -> dict:
        """新增奖品记录(含 user_id 索引 + claimable 集合)"""
        if is_redis_mode():
            return await self._redis_save_reward(reward)
        return self._mem_save_reward(reward)

    async def get_reward(self, reward_no: str) -> Optional[dict]:
        """按奖品编号查询"""
        if is_redis_mode():
            return await self._redis_get_reward(reward_no)
        return self._mem_get_reward(reward_no)

    async def list_rewards(self, user_id, status: str = None,
                            limit: int = 50) -> list[dict]:
        """列出用户奖品记录(可按 status 筛选)"""
        if is_redis_mode():
            return await self._redis_list_rewards(user_id, status, limit)
        return self._mem_list_rewards(user_id, status, limit)

    async def list_claimable_rewards(self, user_id) -> list[dict]:
        """列出用户可领取奖品(定时通知/前端展示用)"""
        if is_redis_mode():
            return await self._redis_list_claimable_rewards(user_id)
        return self._mem_list_claimable_rewards(user_id)

    async def update_reward_fields(self, reward_no: str, fields: dict) -> dict:
        """部分字段更新(如 status: claimable → claimed → shipped → signed)

        - 若 status 从 claimable 变为非 claimable, 自动从 claimable 集合移除

        Raises:
            KeyError: 奖品记录不存在
        """
        if is_redis_mode():
            return await self._redis_update_reward_fields(reward_no, fields)
        return self._mem_update_reward_fields(reward_no, fields)

    # ============================================================
    # 内存后端
    # ============================================================

    def _ensure_store(self):
        """确保 store 包含钱包相关键(懒初始化, 兼容既有 _mock_store)"""
        if "wallets" not in self.store:
            self.store["wallets"] = {}
        if "wallet_transactions" not in self.store:
            self.store["wallet_transactions"] = {}
        if "wallet_withdrawals" not in self.store:
            self.store["wallet_withdrawals"] = {}
        if "wallet_deposits" not in self.store:
            self.store["wallet_deposits"] = {}
        if "wallet_rewards" not in self.store:
            self.store["wallet_rewards"] = {}
        if "_wallet_tx_seq" not in self.store:
            self.store["_wallet_tx_seq"] = 0
        if "_wallet_withdraw_seq" not in self.store:
            self.store["_wallet_withdraw_seq"] = 0
        if "_wallet_deposit_seq" not in self.store:
            self.store["_wallet_deposit_seq"] = 0
        if "_wallet_reward_seq" not in self.store:
            self.store["_wallet_reward_seq"] = 0

    # ---------- 钱包账户(内存) ----------

    def _mem_open_account(self, user_id, account_data: dict) -> dict:
        self._ensure_store()
        if user_id in self.store["wallets"]:
            raise ValueError(f"用户 {user_id} 已开通钱包")
        account_data["userId"] = user_id
        self.store["wallets"][user_id] = account_data
        return account_data

    def _mem_get_account(self, user_id) -> Optional[dict]:
        self._ensure_store()
        return self.store["wallets"].get(user_id)

    def _mem_list_accounts(self, status: str = None) -> list[dict]:
        self._ensure_store()
        result = list(self.store["wallets"].values())
        if status:
            result = [a for a in result if a.get("status") == status]
        result.sort(key=lambda x: x.get("openedAt", ""), reverse=True)
        return result

    def _mem_save_account(self, user_id, account_data: dict) -> dict:
        self._ensure_store()
        account_data["userId"] = user_id
        self.store["wallets"][user_id] = account_data
        return account_data

    def _mem_update_account_fields(self, user_id, fields: dict) -> dict:
        self._ensure_store()
        account = self.store["wallets"].get(user_id)
        if not account:
            raise KeyError(user_id)
        account.update(fields)
        return account

    def _mem_add_balance(self, user_id, amount: float) -> float:
        self._ensure_store()
        account = self.store["wallets"].get(user_id)
        if not account:
            raise KeyError(user_id)
        current = float(account.get("balance", 0))
        new_balance = round(current + amount, 2)
        if new_balance < 0:
            raise ValueError(f"余额不足: 当前 {current}, 需扣除 {-amount}")
        account["balance"] = new_balance
        return new_balance

    def _mem_get_balance(self, user_id) -> float:
        self._ensure_store()
        account = self.store["wallets"].get(user_id)
        if not account:
            raise KeyError(user_id)
        return float(account.get("balance", 0))

    def _mem_add_reward_balance(self, user_id, amount: float) -> float:
        """奖励余额累加(内存模式, 只可购物不可提现)"""
        self._ensure_store()
        account = self.store["wallets"].get(user_id)
        if not account:
            raise KeyError(user_id)
        current = float(account.get("rewardBalance", 0))
        new_balance = round(current + amount, 2)
        if new_balance < 0:
            raise ValueError(f"奖励余额不足: 当前 {current}, 需扣除 {-amount}")
        account["rewardBalance"] = new_balance
        return new_balance

    def _mem_add_frozen(self, user_id, amount: float) -> float:
        self._ensure_store()
        account = self.store["wallets"].get(user_id)
        if not account:
            raise KeyError(user_id)
        current = float(account.get("frozenAmount", 0))
        new_frozen = round(current + amount, 2)
        account["frozenAmount"] = new_frozen
        return new_frozen

    def _mem_reduce_frozen(self, user_id, amount: float) -> float:
        self._ensure_store()
        account = self.store["wallets"].get(user_id)
        if not account:
            raise KeyError(user_id)
        current = float(account.get("frozenAmount", 0))
        if current < amount:
            raise ValueError(f"冻结金额不足: 当前 {current}, 需释放 {amount}")
        new_frozen = round(current - amount, 2)
        account["frozenAmount"] = new_frozen
        return new_frozen

    def _mem_get_status(self, user_id) -> str:
        self._ensure_store()
        account = self.store["wallets"].get(user_id)
        if not account:
            raise KeyError(user_id)
        return account.get("status", STATUS_PENDING)

    def _mem_update_status(self, user_id, new_status: str) -> str:
        self._ensure_store()
        account = self.store["wallets"].get(user_id)
        if not account:
            raise KeyError(user_id)
        old_status = account.get("status", STATUS_PENDING)
        account["status"] = new_status
        return old_status

    # ---------- 交易记录(内存) ----------

    def _mem_save_transaction(self, tx: dict) -> dict:
        self._ensure_store()
        tx_no = tx["txNo"]
        user_id = tx["userId"]
        self.store["wallet_transactions"][tx_no] = tx
        # 用户索引
        index_set = self.store.setdefault("_wallet_tx_index", {})
        index_set.setdefault(user_id, set()).add(tx_no)
        return tx

    def _mem_get_transaction(self, tx_no: str) -> Optional[dict]:
        self._ensure_store()
        return self.store["wallet_transactions"].get(tx_no)

    def _mem_list_transactions(self, user_id, tx_type: str = None,
                                status: str = None, limit: int = 50) -> list[dict]:
        self._ensure_store()
        index_set = self.store.get("_wallet_tx_index", {}).get(user_id, set())
        result = []
        for tx_no in index_set:
            tx = self.store["wallet_transactions"].get(tx_no)
            if not tx:
                continue
            if tx_type and tx.get("type") != tx_type:
                continue
            if status and tx.get("status") != status:
                continue
            result.append(tx)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    def _mem_update_transaction_fields(self, tx_no: str, fields: dict) -> dict:
        self._ensure_store()
        tx = self.store["wallet_transactions"].get(tx_no)
        if not tx:
            raise KeyError(tx_no)
        tx.update(fields)
        return tx

    # ---------- 提现记录(内存) ----------

    def _mem_save_withdrawal(self, withdraw: dict) -> dict:
        self._ensure_store()
        withdraw_no = withdraw["withdrawNo"]
        user_id = withdraw["userId"]
        self.store["wallet_withdrawals"][withdraw_no] = withdraw
        # 用户索引
        index_set = self.store.setdefault("_wallet_withdraw_index", {})
        index_set.setdefault(user_id, set()).add(withdraw_no)
        # pending 集合(仅 status=pending 的加入)
        if withdraw.get("status") == "pending":
            self.store.setdefault("_wallet_withdraw_pending", set()).add(withdraw_no)
        return withdraw

    def _mem_get_withdrawal(self, withdraw_no: str) -> Optional[dict]:
        self._ensure_store()
        return self.store["wallet_withdrawals"].get(withdraw_no)

    def _mem_list_withdrawals(self, user_id, status: str = None,
                               limit: int = 50) -> list[dict]:
        self._ensure_store()
        index_set = self.store.get("_wallet_withdraw_index", {}).get(user_id, set())
        result = []
        for wn in index_set:
            w = self.store["wallet_withdrawals"].get(wn)
            if not w:
                continue
            if status and w.get("status") != status:
                continue
            result.append(w)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    def _mem_list_pending_withdrawals(self, limit: int = 100) -> list[dict]:
        self._ensure_store()
        pending_set = self.store.get("_wallet_withdraw_pending", set())
        result = []
        for wn in pending_set:
            w = self.store["wallet_withdrawals"].get(wn)
            if w and w.get("status") == "pending":
                result.append(w)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    def _mem_update_withdrawal_fields(self, withdraw_no: str, fields: dict) -> dict:
        self._ensure_store()
        w = self.store["wallet_withdrawals"].get(withdraw_no)
        if not w:
            raise KeyError(withdraw_no)
        old_status = w.get("status")
        w.update(fields)
        new_status = w.get("status")
        # 维护 pending 集合
        pending_set = self.store.setdefault("_wallet_withdraw_pending", set())
        if old_status == "pending" and new_status != "pending":
            pending_set.discard(withdraw_no)
        elif old_status != "pending" and new_status == "pending":
            pending_set.add(withdraw_no)
        return w

    # ---------- 定期预付(内存) ----------

    def _mem_save_deposit(self, deposit: dict) -> dict:
        self._ensure_store()
        deposit_no = deposit["depositNo"]
        user_id = deposit["userId"]
        self.store["wallet_deposits"][deposit_no] = deposit
        # 用户索引
        index_set = self.store.setdefault("_wallet_deposit_index", {})
        index_set.setdefault(user_id, set()).add(deposit_no)
        return deposit

    def _mem_get_deposit(self, deposit_no: str) -> Optional[dict]:
        self._ensure_store()
        return self.store["wallet_deposits"].get(deposit_no)

    def _mem_list_deposits(self, user_id, status: str = None,
                            limit: int = 50) -> list[dict]:
        self._ensure_store()
        index_set = self.store.get("_wallet_deposit_index", {}).get(user_id, set())
        result = []
        for dn in index_set:
            d = self.store["wallet_deposits"].get(dn)
            if not d:
                continue
            if status and d.get("status") != status:
                continue
            result.append(d)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    def _mem_update_deposit_fields(self, deposit_no: str, fields: dict) -> dict:
        self._ensure_store()
        d = self.store["wallet_deposits"].get(deposit_no)
        if not d:
            raise KeyError(deposit_no)
        d.update(fields)
        return d

    # ---------- 奖品领取(内存) ----------

    def _mem_save_reward(self, reward: dict) -> dict:
        self._ensure_store()
        reward_no = reward["rewardNo"]
        user_id = reward["userId"]
        self.store["wallet_rewards"][reward_no] = reward
        # 用户索引
        index_set = self.store.setdefault("_wallet_reward_index", {})
        index_set.setdefault(user_id, set()).add(reward_no)
        # claimable 集合
        if reward.get("status") == "claimable":
            self.store.setdefault("_wallet_reward_claimable", set()).add(reward_no)
        return reward

    def _mem_get_reward(self, reward_no: str) -> Optional[dict]:
        self._ensure_store()
        return self.store["wallet_rewards"].get(reward_no)

    def _mem_list_rewards(self, user_id, status: str = None,
                           limit: int = 50) -> list[dict]:
        self._ensure_store()
        index_set = self.store.get("_wallet_reward_index", {}).get(user_id, set())
        result = []
        for rn in index_set:
            r = self.store["wallet_rewards"].get(rn)
            if not r:
                continue
            if status and r.get("status") != status:
                continue
            result.append(r)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    def _mem_list_claimable_rewards(self, user_id) -> list[dict]:
        self._ensure_store()
        index_set = self.store.get("_wallet_reward_index", {}).get(user_id, set())
        result = []
        for rn in index_set:
            r = self.store["wallet_rewards"].get(rn)
            if r and r.get("status") == "claimable":
                result.append(r)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result

    def _mem_update_reward_fields(self, reward_no: str, fields: dict) -> dict:
        self._ensure_store()
        r = self.store["wallet_rewards"].get(reward_no)
        if not r:
            raise KeyError(reward_no)
        old_status = r.get("status")
        r.update(fields)
        new_status = r.get("status")
        # 维护 claimable 集合
        claimable_set = self.store.setdefault("_wallet_reward_claimable", set())
        if old_status == "claimable" and new_status != "claimable":
            claimable_set.discard(reward_no)
        elif old_status != "claimable" and new_status == "claimable":
            claimable_set.add(reward_no)
        return r

    # ============================================================
    # Redis 后端
    # ============================================================

    # ---------- 钱包账户(Redis) ----------

    async def _redis_open_account(self, user_id, account_data: dict) -> dict:
        client = await get_redis_client()
        key = _k("wallet", user_id)
        # 用 SETNX 保证 user_id 唯一(已存在则报错)
        acquired = await client.setnx(key, "{}")  # 先占位
        if not acquired:
            raise ValueError(f"用户 {user_id} 已开通钱包")
        account_data["userId"] = user_id
        # 覆盖占位数据为真实账户
        await client.hset(key, mapping=self._serialize_hash(account_data))
        # 加入全局索引
        await client.sadd(_k("wallet", "index"), user_id)
        return account_data

    async def _redis_get_account(self, user_id) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.hgetall(_k("wallet", user_id))
        if not data:
            return None
        return self._deserialize_account(data)

    async def _redis_list_accounts(self, status: str = None) -> list[dict]:
        client = await get_redis_client()
        user_ids = await client.smembers(_k("wallet", "index"))
        result = []
        for uid in user_ids:
            data = await client.hgetall(_k("wallet", uid))
            if not data:
                continue
            account = self._deserialize_account(data)
            if status and account.get("status") != status:
                continue
            result.append(account)
        result.sort(key=lambda x: x.get("openedAt", ""), reverse=True)
        return result

    async def _redis_save_account(self, user_id, account_data: dict) -> dict:
        client = await get_redis_client()
        account_data["userId"] = user_id
        await client.hset(_k("wallet", user_id),
                          mapping=self._serialize_hash(account_data))
        await client.sadd(_k("wallet", "index"), user_id)
        return account_data

    async def _redis_update_account_fields(self, user_id, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("wallet", user_id)
        if not await client.exists(key):
            raise KeyError(user_id)
        await client.hset(key, mapping=self._serialize_hash(fields))
        return self._deserialize_account(await client.hgetall(key))

    async def _redis_add_balance(self, user_id, amount: float) -> float:
        """余额累加(Redis 浮点 HINCRBYFLOAT 原子操作)

        amount < 0 时需先读后写校验(余额不足抛 ValueError), 用 Lua 保证原子性
        """
        client = await get_redis_client()
        key = _k("wallet", user_id)
        if not await client.exists(key):
            raise KeyError(user_id)
        if amount < 0:
            current = float(await client.hget(key, "balance") or 0)
            if current + amount < 0:
                raise ValueError(f"余额不足: 当前 {current}, 需扣除 {-amount}")
        new_balance = await client.hincrbyfloat(key, "balance", amount)
        return round(new_balance, 2)

    async def _redis_get_balance(self, user_id) -> float:
        client = await get_redis_client()
        key = _k("wallet", user_id)
        if not await client.exists(key):
            raise KeyError(user_id)
        return float(await client.hget(key, "balance") or 0)

    async def _redis_add_reward_balance(self, user_id, amount: float) -> float:
        """奖励余额累加(Redis HINCRBYFLOAT 原子操作, 只可购物不可提现)"""
        client = await get_redis_client()
        key = _k("wallet", user_id)
        if not await client.exists(key):
            raise KeyError(user_id)
        if amount < 0:
            current = float(await client.hget(key, "rewardBalance") or 0)
            if current + amount < 0:
                raise ValueError(
                    f"奖励余额不足: 当前 {current}, 需扣除 {-amount}")
        new_balance = await client.hincrbyfloat(key, "rewardBalance", amount)
        return round(new_balance, 2)

    async def _redis_add_frozen(self, user_id, amount: float) -> float:
        client = await get_redis_client()
        key = _k("wallet", user_id)
        if not await client.exists(key):
            raise KeyError(user_id)
        new_frozen = await client.hincrbyfloat(key, "frozenAmount", amount)
        return round(new_frozen, 2)

    async def _redis_reduce_frozen(self, user_id, amount: float) -> float:
        client = await get_redis_client()
        key = _k("wallet", user_id)
        if not await client.exists(key):
            raise KeyError(user_id)
        current = float(await client.hget(key, "frozenAmount") or 0)
        if current < amount:
            raise ValueError(f"冻结金额不足: 当前 {current}, 需释放 {amount}")
        new_frozen = await client.hincrbyfloat(key, "frozenAmount", -amount)
        return round(new_frozen, 2)

    async def _redis_get_status(self, user_id) -> str:
        client = await get_redis_client()
        key = _k("wallet", user_id)
        if not await client.exists(key):
            raise KeyError(user_id)
        return await client.hget(key, "status") or STATUS_PENDING

    async def _redis_update_status(self, user_id, new_status: str) -> str:
        client = await get_redis_client()
        key = _k("wallet", user_id)
        if not await client.exists(key):
            raise KeyError(user_id)
        old_status = await client.hget(key, "status") or STATUS_PENDING
        await client.hset(key, "status", new_status)
        return old_status

    # ---------- 交易记录(Redis) ----------

    async def _redis_save_transaction(self, tx: dict) -> dict:
        client = await get_redis_client()
        tx_no = tx["txNo"]
        user_id = tx["userId"]
        # 主信息: String(JSON)
        await client.set(_k("wallet", "tx", tx_no),
                         json.dumps(tx, ensure_ascii=False))
        # 用户索引
        await client.sadd(_k("wallet", "tx", "index", user_id), tx_no)
        return tx

    async def _redis_get_transaction(self, tx_no: str) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("wallet", "tx", tx_no))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_transactions(self, user_id, tx_type: str = None,
                                        status: str = None, limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        tx_nos = await client.smembers(_k("wallet", "tx", "index", user_id))
        result = []
        for tn in tx_nos:
            data = await client.get(_k("wallet", "tx", tn))
            if not data:
                continue
            tx = json.loads(data)
            if tx_type and tx.get("type") != tx_type:
                continue
            if status and tx.get("status") != status:
                continue
            result.append(tx)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    async def _redis_update_transaction_fields(self, tx_no: str, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("wallet", "tx", tx_no)
        data = await client.get(key)
        if not data:
            raise KeyError(tx_no)
        tx = json.loads(data)
        tx.update(fields)
        await client.set(key, json.dumps(tx, ensure_ascii=False))
        return tx

    # ---------- 提现记录(Redis) ----------

    async def _redis_save_withdrawal(self, withdraw: dict) -> dict:
        client = await get_redis_client()
        withdraw_no = withdraw["withdrawNo"]
        user_id = withdraw["userId"]
        # 主信息
        await client.set(_k("wallet", "withdraw", withdraw_no),
                         json.dumps(withdraw, ensure_ascii=False))
        # 用户索引
        await client.sadd(_k("wallet", "withdraw", "index", user_id), withdraw_no)
        # pending 集合
        if withdraw.get("status") == "pending":
            await client.sadd(_k("wallet", "withdraw", "pending"), withdraw_no)
        return withdraw

    async def _redis_get_withdrawal(self, withdraw_no: str) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("wallet", "withdraw", withdraw_no))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_withdrawals(self, user_id, status: str = None,
                                       limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        withdraw_nos = await client.smembers(_k("wallet", "withdraw", "index", user_id))
        result = []
        for wn in withdraw_nos:
            data = await client.get(_k("wallet", "withdraw", wn))
            if not data:
                continue
            w = json.loads(data)
            if status and w.get("status") != status:
                continue
            result.append(w)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    async def _redis_list_pending_withdrawals(self, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        pending_nos = await client.smembers(_k("wallet", "withdraw", "pending"))
        result = []
        for wn in pending_nos:
            data = await client.get(_k("wallet", "withdraw", wn))
            if not data:
                continue
            w = json.loads(data)
            if w.get("status") == "pending":
                result.append(w)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    async def _redis_update_withdrawal_fields(self, withdraw_no: str, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("wallet", "withdraw", withdraw_no)
        data = await client.get(key)
        if not data:
            raise KeyError(withdraw_no)
        w = json.loads(data)
        old_status = w.get("status")
        w.update(fields)
        new_status = w.get("status")
        await client.set(key, json.dumps(w, ensure_ascii=False))
        # 维护 pending 集合
        pending_key = _k("wallet", "withdraw", "pending")
        if old_status == "pending" and new_status != "pending":
            await client.srem(pending_key, withdraw_no)
        elif old_status != "pending" and new_status == "pending":
            await client.sadd(pending_key, withdraw_no)
        return w

    # ---------- 定期预付(Redis) ----------

    async def _redis_save_deposit(self, deposit: dict) -> dict:
        client = await get_redis_client()
        deposit_no = deposit["depositNo"]
        user_id = deposit["userId"]
        await client.set(_k("wallet", "deposit", deposit_no),
                         json.dumps(deposit, ensure_ascii=False))
        await client.sadd(_k("wallet", "deposit", "index", user_id), deposit_no)
        return deposit

    async def _redis_get_deposit(self, deposit_no: str) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("wallet", "deposit", deposit_no))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_deposits(self, user_id, status: str = None,
                                     limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        deposit_nos = await client.smembers(_k("wallet", "deposit", "index", user_id))
        result = []
        for dn in deposit_nos:
            data = await client.get(_k("wallet", "deposit", dn))
            if not data:
                continue
            d = json.loads(data)
            if status and d.get("status") != status:
                continue
            result.append(d)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    async def _redis_update_deposit_fields(self, deposit_no: str, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("wallet", "deposit", deposit_no)
        data = await client.get(key)
        if not data:
            raise KeyError(deposit_no)
        d = json.loads(data)
        d.update(fields)
        await client.set(key, json.dumps(d, ensure_ascii=False))
        return d

    # ---------- 奖品领取(Redis) ----------

    async def _redis_save_reward(self, reward: dict) -> dict:
        client = await get_redis_client()
        reward_no = reward["rewardNo"]
        user_id = reward["userId"]
        await client.set(_k("wallet", "reward", reward_no),
                         json.dumps(reward, ensure_ascii=False))
        await client.sadd(_k("wallet", "reward", "index", user_id), reward_no)
        if reward.get("status") == "claimable":
            await client.sadd(_k("wallet", "reward", "claimable"), reward_no)
        return reward

    async def _redis_get_reward(self, reward_no: str) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("wallet", "reward", reward_no))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_rewards(self, user_id, status: str = None,
                                    limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        reward_nos = await client.smembers(_k("wallet", "reward", "index", user_id))
        result = []
        for rn in reward_nos:
            data = await client.get(_k("wallet", "reward", rn))
            if not data:
                continue
            r = json.loads(data)
            if status and r.get("status") != status:
                continue
            result.append(r)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    async def _redis_list_claimable_rewards(self, user_id) -> list[dict]:
        client = await get_redis_client()
        reward_nos = await client.smembers(_k("wallet", "reward", "index", user_id))
        result = []
        for rn in reward_nos:
            data = await client.get(_k("wallet", "reward", rn))
            if not data:
                continue
            r = json.loads(data)
            if r.get("status") == "claimable":
                result.append(r)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result

    async def _redis_update_reward_fields(self, reward_no: str, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("wallet", "reward", reward_no)
        data = await client.get(key)
        if not data:
            raise KeyError(reward_no)
        r = json.loads(data)
        old_status = r.get("status")
        r.update(fields)
        new_status = r.get("status")
        await client.set(key, json.dumps(r, ensure_ascii=False))
        # 维护 claimable 集合
        claimable_key = _k("wallet", "reward", "claimable")
        if old_status == "claimable" and new_status != "claimable":
            await client.srem(claimable_key, reward_no)
        elif old_status != "claimable" and new_status == "claimable":
            await client.sadd(claimable_key, reward_no)
        return r

    # ============================================================
    # 序列化辅助(Redis Hash 要求 value 为 str/int/float)
    # ============================================================

    def _serialize_hash(self, data: dict) -> dict:
        """将 dict 序列化为 Redis Hash 兼容的 mapping

        - None 跳过
        - bool → 0/1
        - list/dict → JSON 字符串
        - int/float 原样保留(redis-py 支持)
        - 其他 → str
        """
        result = {}
        for k, v in data.items():
            if v is None:
                continue
            if isinstance(v, bool):
                result[k] = 1 if v else 0
            elif isinstance(v, (list, dict)):
                result[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, (int, float)):
                result[k] = v
            else:
                result[k] = str(v)
        return result

    def _deserialize_account(self, data: dict) -> dict:
        """将 Redis hgetall 返回的账户 dict 反序列化(数值字段还原)

        钱包账户金额字段需还原为 float, user_id 还原为 int(若可能)
        """
        def _to_number(v):
            if v is None:
                return None
            try:
                if "." in str(v):
                    return float(v)
                return int(v)
            except (TypeError, ValueError):
                return v

        def _to_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return v

        result = dict(data)
        # 金额字段(DECIMAL(12,2))
        amount_fields = {
            "balance", "frozenAmount", "totalDeposit", "totalWithdraw",
            "totalInterest", "totalReward", "totalRebate", "pendingInterest",
        }
        for k in amount_fields:
            if k in result:
                result[k] = _to_number(result[k])
        # 用户ID(整数)
        if "userId" in result:
            result["userId"] = _to_int(result["userId"])
        return result
