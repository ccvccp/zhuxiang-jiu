"""信用管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    credit_scores   - 信用分账户(竹信分0-1000/5级信用等级/先享后付额度)
    credit_logs     - 信用流水(行为加减分/升降级/黑名单/恢复)
    credit_paylater_orders     - 先享后付订单(v8.0, 文档4.3)
    credit_quarterly_settlements - 季度信用积分结算(v8.0, 文档5.1)
    credit_reward_exchanges    - 季度奖励兑换记录(v8.0, 文档5.2)

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

# ---- v8.0 扩展体系常量(文档4.1/4.2/4.3/5.1/5.2) ----

# 升级所需持续天数(文档4.1.1: 按升级路径)
LEVEL_UPGRADE_SUSTAIN_DAYS = {
    LEVEL_L1: 30,   # L1→L2: ≥400持续30天
    LEVEL_L2: 60,   # L2→L3: ≥550持续60天
    LEVEL_L3: 90,   # L3→L4: ≥700持续90天
    LEVEL_L4: 120,  # L4→L5: ≥800持续120天
}

# 降级所需持续天数(文档4.1.2: 统一30天)
LEVEL_DOWNGRADE_SUSTAIN_DAYS = 30

# 升级保护期(文档4.1.3: 升级后30天内不降级)
UPGRADE_PROTECTION_DAYS = 30

# 降级缓冲预警期(文档4.1.3: 降级前30天预警)
DOWNGRADE_BUFFER_DAYS = 30

# 信用修复期(文档4.1.3: 降级后60天, 履约可加速恢复)
CREDIT_REPAIR_DAYS = 60

# B端先享后付额度(文档4.2.2/4.3.1)
LEVEL_B_PAYLATER_QUOTA = {
    LEVEL_L1: 0,
    LEVEL_L2: 0,
    LEVEL_L3: 50000,
    LEVEL_L4: 200000,
    LEVEL_L5: 500000,
}

# 先享后付单笔上限 {等级: (会员, B端)}(文档4.3.1)
LEVEL_PAYLATER_SINGLE_LIMIT = {
    LEVEL_L1: (0, 0),
    LEVEL_L2: (0, 0),
    LEVEL_L3: (1000, 20000),
    LEVEL_L4: (3000, 50000),
    LEVEL_L5: (5000, 100000),
}

# 先享后付月度上限 {等级: (会员, B端)}(文档4.3.1)
LEVEL_PAYLATER_MONTHLY_LIMIT = {
    LEVEL_L1: (0, 0),
    LEVEL_L2: (0, 0),
    LEVEL_L3: (5000, 50000),
    LEVEL_L4: (15000, 200000),
    LEVEL_L5: (30000, 500000),
}

# 逾期费率(文档4.3.3: 日费率0.035%, 逾期1天起; 罚息日息0.1%, 逾期7天起)
PAYLATER_OVERDUE_DAILY_RATE = 0.00035
PAYLATER_OVERDUE_PENALTY_RATE = 0.001
PAYLATER_PENALTY_START_DAYS = 7
# 逾期扣信用分(文档4.3.3: 信用分-20/次)
PAYLATER_OVERDUE_SCORE_PENALTY = 20

# 季度结算(文档5.1): 时序系数1.5 / 积分上限5000 / 现金兑换季度上限¥5000
QUARTER_TIME_FACTOR = 1.5
QUARTER_POINTS_CAP = 5000
QUARTER_CASH_CAP = 5000.0
# 现金兑换个税: 超¥800部分扣20%(文档5.2.2)
CASH_TAX_FREE_AMOUNT = 800.0
CASH_TAX_RATE = 0.2

# 兑换比例: 100积分兑换金额(文档5.2.1)
EXCHANGE_RATES = {
    "cash": 1.0,    # 现金: 100积分=¥1
    "goods": 1.2,   # 商品: 100积分=¥1.2
    "benefit": 1.5, # 权益: 100积分=¥1.5
    "combo": 1.3,   # 组合: 100积分=¥1.3
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
# ---- v8.0 扩展流水类型 ----
LOG_TYPE_SEASON_SETTLE = "season_settle"  # 季度结算
LOG_TYPE_EXCHANGE = "exchange"            # 积分兑换
LOG_TYPE_PAYLATER_ORDER = "paylater_order"   # 先享后付下单(占用额度)
LOG_TYPE_PAYLATER_REPAY = "paylater_repay"   # 先享后付还款(恢复额度)
LOG_TYPE_LEVEL_WARNING = "level_warning"     # 降级缓冲预警

# 行为权重(文档3.x/5.1.2: 消费履约35%/会员等级20%等; 按流水类型近似映射)
BEHAVIOR_WEIGHTS = {
    LOG_TYPE_EARN: 0.35,    # 消费履约维度
    LOG_TYPE_ADJUST: 0.20,  # 人工调整(类比会员等级维度)
    LOG_TYPE_DEDUCT: 0.35,  # 失信扣分(与履约维度对称)
}

# 先享后付订单状态
PAYLATER_STATUS_REVIEW = "review"    # 人工审批中
PAYLATER_STATUS_ACTIVE = "active"    # 已生效(待还款)
PAYLATER_STATUS_REPAID = "repaid"    # 已还清
PAYLATER_STATUS_REJECTED = "rejected"  # 已拒绝
PAYLATER_STATUS_OVERDUE = "overdue"  # 已逾期

# 先享后付账户类型
PAYLATER_ACCOUNT_MEMBER = "member"   # 会员额度
PAYLATER_ACCOUNT_B = "b"             # B端额度

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

# 兑换商品/权益目录(文档5.2.3): {目录ID: {分类/名称/积分/市场价/适用角色}}
EXCHANGE_CATALOG = {
    # 竹香酒产品(goods)
    "G001": {"category": "goods", "name": "竹香酒500ml标准瓶", "points": 5000, "value": 60.0, "roles": []},
    "G002": {"category": "goods", "name": "竹奕酒500ml精品瓶", "points": 20000, "value": 240.0, "roles": []},
    "G003": {"category": "goods", "name": "竹香酒礼盒(2瓶装)", "points": 12000, "value": 150.0, "roles": []},
    "G004": {"category": "goods", "name": "竹奕酒礼盒(2瓶装)", "points": 45000, "value": 540.0, "roles": []},
    "G005": {"category": "goods", "name": "竹香酒整箱(6瓶)", "points": 28000, "value": 336.0, "roles": []},
    # 周边礼品(goods)
    "G101": {"category": "goods", "name": "竹编酒具套装", "points": 8000, "value": 80.0, "roles": []},
    "G102": {"category": "goods", "name": "陶瓷酒杯(2只)", "points": 3000, "value": 30.0, "roles": []},
    "G103": {"category": "goods", "name": "竹香酒吉祥物", "points": 5000, "value": 50.0, "roles": []},
    "G104": {"category": "goods", "name": "文化礼品套装", "points": 15000, "value": 180.0, "roles": []},
    # 会员权益(benefit)
    "B001": {"category": "benefit", "name": "会员升级¥99/年", "points": 9900, "value": 99.0, "roles": []},
    "B002": {"category": "benefit", "name": "L5专属客服3月", "points": 3000, "value": 30.0, "roles": []},
    "B003": {"category": "benefit", "name": "擂台赛投票权+5票", "points": 1000, "value": 10.0, "roles": []},
    "B004": {"category": "benefit", "name": "生日专属礼品", "points": 5000, "value": 50.0, "roles": []},
    # 合作方/B端权益(benefit, 仅B端角色)
    "B101": {"category": "benefit", "name": "代理返利加成1%", "points": 50000, "value": 500.0,
             "roles": [ROLE_AGENT, ROLE_PARTNER, ROLE_DISTRIBUTOR, ROLE_CUSTOM]},
    "B102": {"category": "benefit", "name": "资质绿色通道", "points": 20000, "value": 200.0,
             "roles": [ROLE_AGENT, ROLE_PARTNER, ROLE_DISTRIBUTOR, ROLE_CUSTOM]},
    "B103": {"category": "benefit", "name": "优先供货权1月", "points": 30000, "value": 300.0,
             "roles": [ROLE_AGENT, ROLE_PARTNER, ROLE_DISTRIBUTOR, ROLE_CUSTOM]},
    "B104": {"category": "benefit", "name": "定制优惠5%券", "points": 50000, "value": 500.0,
             "roles": [ROLE_AGENT, ROLE_PARTNER, ROLE_DISTRIBUTOR, ROLE_CUSTOM]},
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
            # ---- v8.0 扩展字段 ----
            "bPaylaterQuota": LEVEL_B_PAYLATER_QUOTA.get(level, 0),
            "bPaylaterUsed": 0.0,
            # 等级规则跟踪: 分数区间进入时间/最近等级变更时间/信用修复期截止
            "scoreZoneSince": now,
            "lastLevelChangeAt": now,
            "repairUntil": None,
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

    async def list_logs_between(self, user_id: int, start: str, end: str,
                                limit: int = 100000) -> list[dict]:
        """查询用户在某时间区间内的流水(ISO字符串左闭右开, 供季度结算)

        Args:
            start: 起始时间ISO(含), 如 "2026-01-01"
            end:   结束时间ISO(不含), 如 "2026-04-01"
        """
        logs = await self.list_logs(user_id, None, limit)
        return [l for l in logs
                if start <= (l.get("createdAt") or "") < end]

    # ============================================================
    # v8.0 先享后付订单 CRUD
    # ============================================================

    async def next_order_id(self) -> int:
        """生成先享后付订单ID"""
        if is_redis_mode():
            return await self._redis_next_id("paylater_order")
        return self._mem_next_id("_credit_paylater_order_seq")

    async def add_paylater_order(self, order: dict) -> int:
        """新增先享后付订单(返回订单ID)"""
        order_id = await self.next_order_id()
        order["orderId"] = order_id
        if "createdAt" not in order:
            order["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_paylater_order(order)
        else:
            self._mem_add_paylater_order(order)
        return order_id

    async def get_paylater_order(self, order_id: int) -> Optional[dict]:
        """按ID查询先享后付订单"""
        if is_redis_mode():
            return await self._redis_get_paylater_order(order_id)
        return self._mem_get_paylater_order(order_id)

    async def save_paylater_order(self, order: dict) -> None:
        """保存先享后付订单(状态变更)"""
        if is_redis_mode():
            await self._redis_add_paylater_order(order, bump=False)
        else:
            self._mem_save_paylater_order(order)

    async def list_paylater_orders(self, user_id: int, status: str = None,
                                   limit: int = 100) -> list[dict]:
        """查询用户先享后付订单(支持按状态筛选)"""
        if is_redis_mode():
            return await self._redis_list_paylater_orders(user_id, status, limit)
        return self._mem_list_paylater_orders(user_id, status, limit)

    # ============================================================
    # v8.0 季度结算 CRUD
    # ============================================================

    async def next_settlement_id(self) -> int:
        """生成季度结算ID"""
        if is_redis_mode():
            return await self._redis_next_id("settlement")
        return self._mem_next_id("_credit_settlement_seq")

    async def add_settlement(self, settlement: dict) -> int:
        """新增季度结算记录(返回结算ID)"""
        settlement_id = await self.next_settlement_id()
        settlement["settlementId"] = settlement_id
        if "settledAt" not in settlement:
            settlement["settledAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_settlement(settlement)
        else:
            self._mem_add_settlement(settlement)
        return settlement_id

    async def get_settlement(self, user_id: int, year: int,
                             quarter: int) -> Optional[dict]:
        """查询某用户某季度的结算记录(幂等检查用)"""
        if is_redis_mode():
            return await self._redis_get_settlement(user_id, year, quarter)
        return self._mem_get_settlement(user_id, year, quarter)

    async def list_settlements(self, user_id: int, limit: int = 20) -> list[dict]:
        """查询用户季度结算记录列表"""
        if is_redis_mode():
            return await self._redis_list_settlements(user_id, limit)
        return self._mem_list_settlements(user_id, limit)

    # ============================================================
    # v8.0 奖励兑换 CRUD
    # ============================================================

    async def next_exchange_id(self) -> int:
        """生成兑换记录ID"""
        if is_redis_mode():
            return await self._redis_next_id("exchange")
        return self._mem_next_id("_credit_exchange_seq")

    async def add_exchange(self, exchange: dict) -> int:
        """新增兑换记录(返回兑换ID)"""
        exchange_id = await self.next_exchange_id()
        exchange["exchangeId"] = exchange_id
        if "createdAt" not in exchange:
            exchange["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_exchange(exchange)
        else:
            self._mem_add_exchange(exchange)
        return exchange_id

    async def list_exchanges(self, user_id: int, exchange_type: str = None,
                             limit: int = 50) -> list[dict]:
        """查询用户兑换记录(支持按类型筛选)"""
        if is_redis_mode():
            return await self._redis_list_exchanges(user_id, exchange_type, limit)
        return self._mem_list_exchanges(user_id, exchange_type, limit)

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
            # ---- v8.0 扩展实体 ----
            self.store["credit_paylater_orders"] = {}        # orderId → order
            self.store["credit_paylater_orders_by_user"] = {}  # userId → [orderId, ...]
            self.store["_credit_paylater_order_seq"] = 0
            self.store["credit_quarterly_settlements"] = {}  # settlementId → settlement
            self.store["credit_settlements_by_user"] = {}    # userId → [settlementId, ...]
            self.store["_credit_settlement_seq"] = 0
            self.store["credit_reward_exchanges"] = {}       # exchangeId → exchange
            self.store["credit_exchanges_by_user"] = {}      # userId → [exchangeId, ...]
            self.store["_credit_exchange_seq"] = 0

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

    # --- v8.0 先享后付订单(内存) ---

    def _mem_add_paylater_order(self, order: dict) -> None:
        self._ensure_store()
        order_id = order["orderId"]
        user_id = order["userId"]
        self.store["credit_paylater_orders"][order_id] = order
        if order_id not in self.store["credit_paylater_orders_by_user"].get(user_id, []):
            self.store["credit_paylater_orders_by_user"].setdefault(user_id, []).append(order_id)

    def _mem_save_paylater_order(self, order: dict) -> None:
        self._ensure_store()
        self.store["credit_paylater_orders"][order["orderId"]] = order

    def _mem_get_paylater_order(self, order_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["credit_paylater_orders"].get(order_id)

    def _mem_list_paylater_orders(self, user_id: int, status: str = None,
                                  limit: int = 100) -> list[dict]:
        self._ensure_store()
        order_ids = self.store["credit_paylater_orders_by_user"].get(user_id, [])
        orders = [self.store["credit_paylater_orders"][oid] for oid in order_ids
                  if oid in self.store["credit_paylater_orders"]]
        if status:
            orders = [o for o in orders if o.get("status") == status]
        orders.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        return orders[:limit]

    # --- v8.0 季度结算(内存) ---

    def _mem_add_settlement(self, settlement: dict) -> None:
        self._ensure_store()
        settlement_id = settlement["settlementId"]
        user_id = settlement["userId"]
        self.store["credit_quarterly_settlements"][settlement_id] = settlement
        self.store["credit_settlements_by_user"].setdefault(user_id, []).append(settlement_id)

    def _mem_get_settlement(self, user_id: int, year: int,
                            quarter: int) -> Optional[dict]:
        self._ensure_store()
        for sid in self.store["credit_settlements_by_user"].get(user_id, []):
            s = self.store["credit_quarterly_settlements"].get(sid)
            if s and s.get("year") == year and s.get("quarter") == quarter:
                return s
        return None

    def _mem_list_settlements(self, user_id: int, limit: int = 20) -> list[dict]:
        self._ensure_store()
        sids = self.store["credit_settlements_by_user"].get(user_id, [])
        items = [self.store["credit_quarterly_settlements"][sid] for sid in sids
                 if sid in self.store["credit_quarterly_settlements"]]
        items.sort(key=lambda s: (s.get("year", 0), s.get("quarter", 0)), reverse=True)
        return items[:limit]

    # --- v8.0 奖励兑换(内存) ---

    def _mem_add_exchange(self, exchange: dict) -> None:
        self._ensure_store()
        exchange_id = exchange["exchangeId"]
        user_id = exchange["userId"]
        self.store["credit_reward_exchanges"][exchange_id] = exchange
        self.store["credit_exchanges_by_user"].setdefault(user_id, []).append(exchange_id)

    def _mem_list_exchanges(self, user_id: int, exchange_type: str = None,
                            limit: int = 50) -> list[dict]:
        self._ensure_store()
        eids = self.store["credit_exchanges_by_user"].get(user_id, [])
        items = [self.store["credit_reward_exchanges"][eid] for eid in eids
                 if eid in self.store["credit_reward_exchanges"]]
        if exchange_type:
            items = [e for e in items if e.get("exchangeType") == exchange_type]
        items.sort(key=lambda e: e.get("createdAt", ""), reverse=True)
        return items[:limit]

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

    # --- v8.0 先享后付订单(Redis) ---

    async def _redis_add_paylater_order(self, order: dict,
                                        bump: bool = True) -> None:
        client = await get_redis_client()
        order_id = order["orderId"]
        user_id = order["userId"]
        await client.set(_k("credit", "paylater_order", order_id),
                         json.dumps(order, ensure_ascii=False))
        if bump:
            await client.lpush(_k("credit", "paylater_orders_by_user", user_id), order_id)

    async def _redis_get_paylater_order(self, order_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("credit", "paylater_order", order_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_paylater_orders(self, user_id: int, status: str = None,
                                          limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        order_ids = await client.lrange(_k("credit", "paylater_orders_by_user", user_id), 0, -1)
        orders = []
        for oid in order_ids:
            data = await client.get(_k("credit", "paylater_order", oid))
            if data:
                order = json.loads(data)
                if status and order.get("status") != status:
                    continue
                orders.append(order)
        orders.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        return orders[:limit]

    # --- v8.0 季度结算(Redis) ---

    async def _redis_add_settlement(self, settlement: dict) -> None:
        client = await get_redis_client()
        settlement_id = settlement["settlementId"]
        user_id = settlement["userId"]
        payload = json.dumps(settlement, ensure_ascii=False)
        await client.set(_k("credit", "settlement", settlement_id), payload)
        # (userId, year, quarter) 唯一索引: 供幂等检查
        await client.set(_k("credit", "settlement_idx",
                            user_id, settlement["year"], settlement["quarter"]),
                         settlement_id)
        await client.lpush(_k("credit", "settlements_by_user", user_id), settlement_id)

    async def _redis_get_settlement(self, user_id: int, year: int,
                                    quarter: int) -> Optional[dict]:
        client = await get_redis_client()
        settlement_id = await client.get(_k("credit", "settlement_idx",
                                            user_id, year, quarter))
        if not settlement_id:
            return None
        data = await client.get(_k("credit", "settlement", settlement_id))
        return json.loads(data) if data else None

    async def _redis_list_settlements(self, user_id: int, limit: int = 20) -> list[dict]:
        client = await get_redis_client()
        sids = await client.lrange(_k("credit", "settlements_by_user", user_id), 0, -1)
        items = []
        for sid in sids:
            data = await client.get(_k("credit", "settlement", sid))
            if data:
                items.append(json.loads(data))
        items.sort(key=lambda s: (s.get("year", 0), s.get("quarter", 0)), reverse=True)
        return items[:limit]

    # --- v8.0 奖励兑换(Redis) ---

    async def _redis_add_exchange(self, exchange: dict) -> None:
        client = await get_redis_client()
        exchange_id = exchange["exchangeId"]
        user_id = exchange["userId"]
        await client.set(_k("credit", "exchange", exchange_id),
                         json.dumps(exchange, ensure_ascii=False))
        await client.lpush(_k("credit", "exchanges_by_user", user_id), exchange_id)

    async def _redis_list_exchanges(self, user_id: int, exchange_type: str = None,
                                    limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        eids = await client.lrange(_k("credit", "exchanges_by_user", user_id), 0, -1)
        items = []
        for eid in eids:
            data = await client.get(_k("credit", "exchange", eid))
            if data:
                exchange = json.loads(data)
                if exchange_type and exchange.get("exchangeType") != exchange_type:
                    continue
                items.append(exchange)
        return items[:limit]
