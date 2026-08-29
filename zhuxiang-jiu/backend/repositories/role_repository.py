"""AI智能管理模块(角色经济中枢)数据访问层(双模式: 内存 + Redis)

表清单:
    role_catalog:              角色目录(认领条件/配额/分润说明)
    role_claims:               角色认领申请(含AI预审结果)
    role_contracts:            权责利三合一契约实例(试用/转正/冻结/清退)
    service_dispatch_records:  AI服务调度派单记录(分润依据链)
    profit_ledger:             统一分润总账(双轨口径: diff_profit/sale_price)
    credit_events:             信用行为总线事件(分发至竹信分/权责信用分)

设计对齐(AI智能管理模块设计文档 v1.1 第六章):
    - 角色注册认领制: 先注册会员, 再认领具体角色
    - D-7: profit_ledger.basis 区分双轨口径
    - D-8: 服务分润参数(1%/断崖式满意度/信用/时效/封顶)
"""

import json
from datetime import datetime, UTC

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 角色编码(P0 目录种子, 管理端可增改)
# ============================================================

ROLE_MEMBER = "member"                    # 会员(注册即得)
ROLE_CUSTOMER_SERVICE = "customer_service"  # 人工客服
ROLE_PRODUCTION_WORKER = "production_worker"  # 生产工人
ROLE_AGENT = "agent"                      # 代理商
ROLE_PARTNER = "partner"                  # 酒店酒吧会所合作商
ROLE_PROMOTER = "promoter"                # 推广员/达人
ROLE_CITY_STORE = "city_store"            # 市级网店

# ============================================================
# 契约状态机
# ============================================================

CONTRACT_STATUS_PROBATION = "probation"   # 试用期(分润减半)
CONTRACT_STATUS_ACTIVE = "active"        # 转正
CONTRACT_STATUS_SUSPENDED = "suspended"  # 冻结(不可接单)
CONTRACT_STATUS_TERMINATED = "terminated"  # 清退/退出
CONTRACT_STATUSES = (
    CONTRACT_STATUS_PROBATION, CONTRACT_STATUS_ACTIVE,
    CONTRACT_STATUS_SUSPENDED, CONTRACT_STATUS_TERMINATED,
)
# 可接单状态(派单候选)
DISPATCHABLE_STATUSES = (CONTRACT_STATUS_PROBATION, CONTRACT_STATUS_ACTIVE)

# 认领申请状态
CLAIM_STATUS_PENDING = "pending"
CLAIM_STATUS_APPROVED = "approved"
CLAIM_STATUS_REJECTED = "rejected"
CLAIM_STATUSES = (CLAIM_STATUS_PENDING, CLAIM_STATUS_APPROVED,
                  CLAIM_STATUS_REJECTED)

# ============================================================
# 分润口径(D-7 双轨)
# ============================================================

PROFIT_BASIS_SALE_PRICE = "sale_price"      # 实际销售价格×60% 新口径
PROFIT_BASIS_DIFF_PROFIT = "diff_profit"    # 差价利润60/20/20 旧口径
PROFIT_BASIS_PURCHASE_AMOUNT = "purchase_amount"  # 进货额返利(代理商超额累进)

# 平台角色标记(总账中本站留存份额的记账方)
ROLE_PLATFORM = "platform"

# 分润总账状态
LEDGER_STATUS_PENDING = "pending"   # 待入账(钱包未开通等)
LEDGER_STATUS_SETTLED = "settled"    # 已结算入钱包
LEDGER_STATUS_REVERSED = "reversed"  # 已追回
LEDGER_STATUSES = (LEDGER_STATUS_PENDING, LEDGER_STATUS_SETTLED,
                   LEDGER_STATUS_REVERSED)

# ============================================================
# 服务分润参数(D-8 决策, 2026-08-29)
# ============================================================

# 客服基础分润率(基于订单实际销售价格)
SERVICE_PROFIT_RATE = 0.01
# 满意度系数(断崖式: ≤3星为0)
SATISFACTION_COEFF = {5: 2.0, 4: 1.2, 3: 0.0, 2: 0.0, 1: 0.0}
# 信用系数(竹信分等级)
CREDIT_LEVEL_COEFF = {"L5": 1.3, "L4": 1.15, "L3": 1.0, "L2": 0.8, "L1": 0.0}
# 时效系数
TIMELINESS_SLA_OK = 1.2
TIMELINESS_OVERDUE = 0.5
TIMELINESS_ESCALATED = 0.0
# 封顶(单笔/月度)
SINGLE_CAP = 50.0
MONTHLY_CAP = 3000.0
# 试用期分润减半
PROBATION_RATE = 0.5
# 试用期天数 / 契约有效期(天)
PROBATION_DAYS = 30
CONTRACT_VALID_DAYS = 365

# ============================================================
# 退款追回负账(D-8 细化: 文档5.3 追回规则)
# ============================================================

# 负账累计超该阈值 → 冻结接单(派单候选剔除)
CLAWBACK_FREEZE_THRESHOLD = 500.0

# ============================================================
# 生产工人分润(P1: 设计文档5.4)
# ============================================================

# 工段分润率(基于订单实际销售价格, 合计 = 15% 生产与质量子池上限)
STAGE_PROFIT_RATES = {
    "STG-BREW": 0.030,   # 工艺酿酒
    "STG-STOR": 0.015,   # 原酒储藏
    "STG-BLEND": 0.030,  # 产品调配检测(质检关卡)
    "STG-FILL": 0.025,   # 灌装
    "STG-PACK": 0.020,   # 包装质检(质检关卡)
    "STG-WARE": 0.015,   # 仓库
    "STG-OUT": 0.015,    # 出库
}

# 质量系数(设计文档5.4: 抽检合格1.0/优质1.2/质量事故0并追回)
WORKER_QUALITY_COEFF = {
    "pass": 1.0,
    "premium": 1.2,
    "accident": 0.0,
}

# 工人分润总账流水号前缀(幂等键: WRK-{batchNo}-{stageCode})
WORKER_LEDGER_PREFIX = "WRK-"

# ============================================================
# AI监管大脑(P2: 设计文档§4.6)
# ============================================================

# 1) 满意度预测: 进行中人工会话风险评分(0-100, ≥阈值进入干预名单)
SATISFACTION_RISK_THRESHOLD = 60
# 情绪负向关键词(规则引擎 B 级, 命中每词扣分)
NEGATIVE_EMOTION_KEYWORDS = (
    "投诉", "骗", "垃圾", "差评", "退款", "退货", "假的", "不耐烦",
    "等太久", "没人理", "垃圾服务", "骗人", "失望", "生气", "离谱",
)
# 2) 异常分润检测: 离群倍数(相对该客服均值)
ANOMALY_AMOUNT_MULTIPLIER = 5.0
# 同一客服-同一会员月度结算笔数上限(防刷)
ANOMALY_PAIR_MONTHLY_LIMIT = 5
# 3) 信用异动预警: 窗口天数与下滑阈值
CREDIT_DROP_WINDOW_DAYS = 7
CREDIT_DROP_THRESHOLD = 100
# 预警状态
ALERT_STATUS_OPEN = "open"       # 待处置
ALERT_STATUS_RESOLVED = "resolved"  # 已处置

# ============================================================
# 派单权重(AI服务调度中枢)
# ============================================================

DISPATCH_WEIGHTS = {"credit": 0.40, "skill": 0.25, "load": 0.20,
                    "satisfaction": 0.15}
# P0 无技能库, 技能匹配度取基线值
SKILL_BASE = 0.7
# 无历史满意度数据时的默认值
DEFAULT_SATISFACTION = 0.8
# 负载上限(处理中工单数达到该值视为满载)
LOAD_CAPACITY = 10

# ============================================================
# 信用行为码(客服角色 P0; 其他角色 P1 扩展)
# ============================================================

BEHAVIOR_CS_SATISFACTION_GOOD = "cs_satisfaction_good"    # 满意度≥4星 +5
BEHAVIOR_CS_SATISFACTION_BAD = "cs_satisfaction_bad"     # 满意度≤3星 -5
BEHAVIOR_CS_SLA_OVERDUE = "cs_sla_overdue"                # SLA超时 -10
BEHAVIOR_CS_ESCALATED = "cs_escalated"                    # 工单被升级 -15
BEHAVIOR_CLAIM_APPROVED = "claim_approved"                # 认领通过 +3
BEHAVIOR_WORKER_QUALITY_PREMIUM = "worker_quality_premium"  # 优质批次 +5
BEHAVIOR_WORKER_QUALITY_ACCIDENT = "worker_quality_accident"  # 质量事故 -15

BEHAVIOR_DELTAS = {
    BEHAVIOR_CS_SATISFACTION_GOOD: 5,
    BEHAVIOR_CS_SATISFACTION_BAD: -5,
    BEHAVIOR_CS_SLA_OVERDUE: -10,
    BEHAVIOR_CS_ESCALATED: -15,
    BEHAVIOR_CLAIM_APPROVED: 3,
    BEHAVIOR_WORKER_QUALITY_PREMIUM: 5,
    BEHAVIOR_WORKER_QUALITY_ACCIDENT: -15,
}

# ============================================================
# 角色目录种子(P0)
# ============================================================

ROLE_CATALOG_SEED = [
    {
        "roleCode": ROLE_MEMBER, "roleName": "会员", "category": "consumer",
        "claimConditions": "注册(18周岁实名合规)即得",
        "creditThreshold": 0, "quota": 0,
        "profitDesc": "实物+购物体验+消费返利1%/积分1.5竹叶每元",
        "dutyTerms": "按时付款、真实评价、账号安全", "status": "active",
    },
    {
        "roleCode": ROLE_CUSTOMER_SERVICE, "roleName": "人工客服",
        "category": "service",
        "claimConditions": "实名认证+客服培训考核+签署服务责任书",
        "creditThreshold": 400, "quota": 50,
        "profitDesc": "服务分润: 订单实售价×1%×满意度×信用×时效(单笔≤¥50,月度≤¥3000)",
        "dutyTerms": "SLA: 紧急立即/高2h/中4h/低24h; 满意度≥4星才计分润", "status": "active",
    },
    {
        "roleCode": ROLE_PRODUCTION_WORKER, "roleName": "生产工人",
        "category": "production",
        "claimConditions": "技能认证+岗位认领+签署生产责任书(P1开放)",
        "creditThreshold": 400, "quota": 200,
        "profitDesc": "计件分润: 订单实售价×环节分润率×质量系数(P1)",
        "dutyTerms": "工艺标准+生命码扫码留痕", "status": "active",
    },
    {
        "roleCode": ROLE_AGENT, "roleName": "代理商", "category": "channel",
        "claimConditions": "区域授权+保证金(对接代理商管理模块)",
        "creditThreshold": 450, "quota": 100,
        "profitDesc": "超额累进返利(15%/25%/30%)+区域分润+品鉴酒",
        "dutyTerms": "区域销售目标+渠道秩序(禁窜货乱价)", "status": "active",
    },
    {
        "roleCode": ROLE_PARTNER, "roleName": "酒店酒吧会所合作商",
        "category": "channel",
        "claimConditions": "铺货协议(对接合作商管理模块)",
        "creditThreshold": 450, "quota": 300,
        "profitDesc": "差价利润分润(有代理: 本站60%/代理20%/酒店20%)",
        "dutyTerms": "陈列动销+结算配合", "status": "active",
    },
    {
        "roleCode": ROLE_PROMOTER, "roleName": "推广员/达人",
        "category": "traffic",
        "claimConditions": "认领推广码(对接流量管理模块)",
        "creditThreshold": 400, "quota": 1000,
        "profitDesc": "订单金额×等级佣金(推广员5%-15%/达人10%-20%)",
        "dutyTerms": "真实推广, 禁刷量(自购不计佣)", "status": "active",
    },
    {
        "roleCode": ROLE_CITY_STORE, "roleName": "市级网店", "category": "channel",
        "claimConditions": "店铺资质(对接市级网店模块)",
        "creditThreshold": 400, "quota": 50,
        "profitDesc": "三档进货折扣(0.70/0.80/0.90)+分润",
        "dutyTerms": "月销目标+服务标准", "status": "active",
    },
]


class RoleRepository:
    """AI智能管理模块数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # ID 生成
    # ============================================================

    async def next_id(self, entity: str) -> int:
        """生成自增ID(内存/Redis 双模式)"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("role", entity, "seq"))
        return self._mem_next_id(f"_role_{entity}_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    def generate_contract_no(self) -> str:
        """生成契约编号: RL+时间戳+序号"""
        now = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        seq = self.store.get("_role_contract_seq", 0) + 1
        return f"RL{now}{seq:04d}"

    def generate_ledger_no(self, ticket_no: str) -> str:
        """服务分润流水号: SVC-{工单号}(幂等键)"""
        return f"SVC-{ticket_no}"

    # ============================================================
    # 角色目录
    # ============================================================

    async def get_catalog_role(self, role_code: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("role", "catalog", role_code))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store["role_catalog"].get(role_code)

    async def list_catalog(self, status: str = None) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("role", "catalog", "*"))
            roles = []
            for key in keys:
                data = await client.get(key)
                if data:
                    roles.append(json.loads(data))
        else:
            self._ensure_store()
            roles = list(self.store["role_catalog"].values())
        if status:
            roles = [r for r in roles if r.get("status") == status]
        roles.sort(key=lambda r: r.get("roleCode", ""))
        return roles

    async def upsert_catalog_role(self, role: dict) -> dict:
        """新增/更新角色目录条目"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("role", "catalog", role["roleCode"]),
                             json.dumps(role, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["role_catalog"][role["roleCode"]] = role
        return role

    # ============================================================
    # 认领申请
    # ============================================================

    async def create_claim(self, claim: dict) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("role", "claim", claim["id"]),
                             json.dumps(claim, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["role_claims"][claim["id"]] = claim
        return claim["id"]

    async def get_claim(self, claim_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("role", "claim", claim_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store["role_claims"].get(claim_id)

    async def update_claim(self, claim_id: int, updates: dict) -> None:
        claim = await self.get_claim(claim_id)
        if claim is None:
            return
        claim.update(updates)
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("role", "claim", claim_id),
                             json.dumps(claim, ensure_ascii=False))

    async def list_claims(self, user_id: int = None, role_code: str = None,
                          status: str = None, limit: int = 100) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("role", "claim", "*"))
            claims = []
            for key in keys:
                data = await client.get(key)
                if data:
                    claims.append(json.loads(data))
        else:
            self._ensure_store()
            claims = list(self.store["role_claims"].values())
        if user_id is not None:
            claims = [c for c in claims if c.get("userId") == user_id]
        if role_code:
            claims = [c for c in claims if c.get("roleCode") == role_code]
        if status:
            claims = [c for c in claims if c.get("status") == status]
        claims.sort(key=lambda c: c.get("createdAt", ""), reverse=True)
        return claims[:limit]

    # ============================================================
    # 契约实例
    # ============================================================

    async def create_contract(self, contract: dict) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("role", "contract", contract["id"]),
                             json.dumps(contract, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["role_contracts"][contract["id"]] = contract
        return contract["id"]

    async def get_contract(self, contract_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("role", "contract", contract_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store["role_contracts"].get(contract_id)

    async def update_contract(self, contract_id: int, updates: dict) -> None:
        contract = await self.get_contract(contract_id)
        if contract is None:
            return
        contract.update(updates)
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("role", "contract", contract_id),
                             json.dumps(contract, ensure_ascii=False))

    async def list_contracts(self, user_id: int = None, role_code: str = None,
                             statuses: tuple = None,
                             limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("role", "contract", "*"))
            contracts = []
            for key in keys:
                data = await client.get(key)
                if data:
                    contracts.append(json.loads(data))
        else:
            self._ensure_store()
            contracts = list(self.store["role_contracts"].values())
        if user_id is not None:
            contracts = [c for c in contracts if c.get("userId") == user_id]
        if role_code:
            contracts = [c for c in contracts
                         if c.get("roleCode") == role_code]
        if statuses:
            contracts = [c for c in contracts
                         if c.get("status") in statuses]
        contracts.sort(key=lambda c: c.get("signedAt", ""), reverse=True)
        return contracts[:limit]

    async def get_active_contract(self, user_id: int, role_code: str) -> dict | None:
        """查询用户某角色的有效契约(试用/转正)"""
        contracts = await self.list_contracts(
            user_id=user_id, role_code=role_code,
            statuses=DISPATCHABLE_STATUSES, limit=1)
        return contracts[0] if contracts else None

    # ============================================================
    # 派单记录
    # ============================================================

    async def create_dispatch(self, dispatch: dict) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("role", "dispatch", dispatch["id"]),
                             json.dumps(dispatch, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["service_dispatch_records"][dispatch["id"]] = dispatch
        return dispatch["id"]

    async def get_dispatch_by_session(self, session_id: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("role", "dispatch", "*"))
            for key in keys:
                data = await client.get(key)
                if data:
                    d = json.loads(data)
                    if d.get("sessionId") == session_id:
                        return d
            return None
        self._ensure_store()
        for d in self.store["service_dispatch_records"].values():
            if d.get("sessionId") == session_id:
                return d
        return None

    async def list_dispatches(self, assignee_id: int = None,
                              limit: int = 100) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("role", "dispatch", "*"))
            dispatches = []
            for key in keys:
                data = await client.get(key)
                if data:
                    dispatches.append(json.loads(data))
        else:
            self._ensure_store()
            dispatches = list(self.store["service_dispatch_records"].values())
        if assignee_id is not None:
            dispatches = [d for d in dispatches
                         if d.get("assigneeId") == assignee_id]
        dispatches.sort(key=lambda d: d.get("createdAt", ""), reverse=True)
        return dispatches[:limit]

    # ============================================================
    # 分润总账
    # ============================================================

    async def create_ledger(self, ledger: dict) -> str:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("role", "ledger", ledger["ledgerNo"]),
                             json.dumps(ledger, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["profit_ledger"][ledger["ledgerNo"]] = ledger
        return ledger["ledgerNo"]

    async def get_ledger(self, ledger_no: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("role", "ledger", ledger_no))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store["profit_ledger"].get(ledger_no)

    async def update_ledger(self, ledger_no: str, updates: dict) -> None:
        ledger = await self.get_ledger(ledger_no)
        if ledger is None:
            return
        ledger.update(updates)
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("role", "ledger", ledger_no),
                             json.dumps(ledger, ensure_ascii=False))

    async def list_ledgers(self, user_id: int = None, role_code: str = None,
                           basis: str = None, status: str = None,
                           limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("role", "ledger", "*"))
            ledgers = []
            for key in keys:
                data = await client.get(key)
                if data:
                    ledgers.append(json.loads(data))
        else:
            self._ensure_store()
            ledgers = list(self.store["profit_ledger"].values())
        if user_id is not None:
            ledgers = [l for l in ledgers if l.get("userId") == user_id]
        if role_code:
            ledgers = [l for l in ledgers if l.get("roleCode") == role_code]
        if basis:
            ledgers = [l for l in ledgers if l.get("basis") == basis]
        if status:
            ledgers = [l for l in ledgers if l.get("status") == status]
        ledgers.sort(key=lambda l: l.get("createdAt", ""), reverse=True)
        return ledgers[:limit]

    async def sum_monthly_settled(self, user_id: int, role_code: str,
                                  now: str) -> float:
        """统计用户当月已结算分润总额(月度封顶用)"""
        month_prefix = now[:7]  # "YYYY-MM"
        ledgers = await self.list_ledgers(user_id=user_id, role_code=role_code,
                                          limit=100000)
        return round(sum(
            l.get("amount", 0)
            for l in ledgers
            if l.get("status") == LEDGER_STATUS_SETTLED
            and l.get("createdAt", "").startswith(month_prefix)
        ), 2)

    async def ledger_exists_prefix(self, prefix: str) -> bool:
        """是否存在指定前缀的总账流水(工人批次结算幂等用)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("role", "ledger", f"{prefix}*"))
            return bool(keys)
        self._ensure_store()
        return any(no.startswith(prefix)
                   for no in self.store["profit_ledger"])

    # ============================================================
    # AI监管预警(P2)
    # ============================================================

    async def create_alert(self, alert: dict) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("role", "alert", alert["id"]),
                             json.dumps(alert, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["role_alerts"][alert["id"]] = alert
        return alert["id"]

    async def get_alert(self, alert_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("role", "alert", alert_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store["role_alerts"].get(alert_id)

    async def update_alert(self, alert_id: int, updates: dict) -> None:
        alert = await self.get_alert(alert_id)
        if alert is None:
            return
        alert.update(updates)
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("role", "alert", alert_id),
                             json.dumps(alert, ensure_ascii=False))

    async def list_alerts(self, alert_type: str = None,
                          status: str = None,
                          limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("role", "alert", "*"))
            alerts = []
            for key in keys:
                data = await client.get(key)
                if data:
                    alerts.append(json.loads(data))
        else:
            self._ensure_store()
            alerts = list(self.store["role_alerts"].values())
        if alert_type:
            alerts = [a for a in alerts if a.get("alertType") == alert_type]
        if status:
            alerts = [a for a in alerts if a.get("status") == status]
        alerts.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        return alerts[:limit]

    # ============================================================
    # 信用行为总线事件
    # ============================================================

    async def create_event(self, event: dict) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("role", "event", event["id"]),
                             json.dumps(event, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["credit_events"][event["id"]] = event
        return event["id"]

    async def list_events(self, user_id: int = None, role_code: str = None,
                          limit: int = 100) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("role", "event", "*"))
            events = []
            for key in keys:
                data = await client.get(key)
                if data:
                    events.append(json.loads(data))
        else:
            self._ensure_store()
            events = list(self.store["credit_events"].values())
        if user_id is not None:
            events = [e for e in events if e.get("userId") == user_id]
        if role_code:
            events = [e for e in events if e.get("roleCode") == role_code]
        events.sort(key=lambda e: e.get("ts", ""), reverse=True)
        return events[:limit]

    # ============================================================
    # 退款追回负账(D-8 细化)
    # ============================================================

    def _clawback_key(self, user_id: int, role_code: str) -> str:
        return f"{user_id}:{role_code}"

    async def get_clawback(self, user_id: int, role_code: str) -> float:
        """查询待抵扣负账余额"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("role", "clawback", self._clawback_key(user_id, role_code)))
            return float(data) if data else 0.0
        self._ensure_store()
        return self.store["role_clawbacks"].get(
            self._clawback_key(user_id, role_code), 0.0)

    async def adjust_clawback(self, user_id: int, role_code: str,
                              delta: float) -> float:
        """调整负账余额(正数累加追回额, 负数为抵扣), 返回新余额"""
        balance = await self.get_clawback(user_id, role_code)
        balance = round(balance + delta, 2)
        if balance < 0:
            balance = 0.0
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("role", "clawback", self._clawback_key(user_id, role_code)),
                json.dumps(balance))
        else:
            self._ensure_store()
            self.store["role_clawbacks"][
                self._clawback_key(user_id, role_code)] = balance
        return balance

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含角色模块的键(懒初始化+种子目录)"""
        if "role_catalog" not in self.store:
            self.store["role_catalog"] = {}
            self.store["role_claims"] = {}
            self.store["role_contracts"] = {}
            self.store["service_dispatch_records"] = {}
            self.store["profit_ledger"] = {}
            self.store["credit_events"] = {}
            self.store["role_clawbacks"] = {}   # userId:roleCode → 负账余额
            self.store["role_alerts"] = {}      # alertId → AI监管预警
            self.store["_role_claim_seq"] = 0
            self.store["_role_contract_seq"] = 0
            self.store["_role_dispatch_seq"] = 0
            self.store["_role_event_seq"] = 0
            self.store["_role_alert_seq"] = 0
            # 种子目录(仅内存模式需要; Redis 模式由部署脚本/管理端写入)
            now = datetime.now(UTC).isoformat()
            for role in ROLE_CATALOG_SEED:
                role = dict(role)
                role["createdAt"] = now
                role["updatedAt"] = now
                self.store["role_catalog"][role["roleCode"]] = role

    # ============================================================
    # Redis 模式种子目录
    # ============================================================

    async def seed_catalog(self) -> int:
        """写入角色目录种子(Redis 模式部署时调用一次, 幂等)"""
        count = 0
        for role in ROLE_CATALOG_SEED:
            existing = await self.get_catalog_role(role["roleCode"])
            if existing is None:
                role = dict(role)
                role["createdAt"] = datetime.now(UTC).isoformat()
                role["updatedAt"] = role["createdAt"]
                await self.upsert_catalog_role(role)
                count += 1
        return count
