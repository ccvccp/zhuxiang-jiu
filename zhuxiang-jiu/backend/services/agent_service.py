"""代理商业务:升级/降级 + 准入管理 + 档案管理 + 进货管理

并发安全:
    - 升级/降级/进货: 涉及 wallet RMW,使用 agent:{agentId} 锁(对齐前端 Mutex)
    - 进货库存扣减: 嵌套 stock:{productId} 锁(与 order_service 对齐)
    - 审核通过: 创建代理商档案使用 agent_apply:{applyId} 锁防止重复审核

等级体系(S/A/B/C/D,数字越小等级越高):
    S 顶级(折扣 0.6) / A 高级(0.65) / B 中级(0.7) / C 初级(0.75) / D 实习(0.8)
"""

import logging
from datetime import datetime
from typing import Optional

from core.helpers import ts
from core.locks import get_lock
from repositories.agent_repository import AgentRepository
from repositories.inventory_repository import InventoryRepository
from repositories.product_repository import ProductRepository

logger = logging.getLogger(__name__)


# ============================================================
# 等级体系常量
# ============================================================

# 等级顺序(高 → 低)
LEVEL_ORDER = ["S", "A", "B", "C", "D"]

# 进货折扣(零售价 × 折扣 = 代理商进货价)
LEVEL_DISCOUNTS = {"S": 0.6, "A": 0.65, "B": 0.7, "C": 0.75, "D": 0.8}

# 等级中文名
LEVEL_NAMES = {
    "S": "顶级代理商", "A": "高级代理商", "B": "中级代理商",
    "C": "初级代理商", "D": "实习代理商",
}

# 等级权益说明
LEVEL_RIGHTS = {
    "S": "进货折扣 0.6 / 专属客户经理 / 区域独家授权 / 优先供货 / 营销资源倾斜",
    "A": "进货折扣 0.65 / 区域保护 / 优先供货 / 季度返利",
    "B": "进货折扣 0.7 / 标准供货 / 月度返利 / 培训支持",
    "C": "进货折扣 0.75 / 标准供货 / 培训支持",
    "D": "进货折扣 0.8 / 试用阶段 / 限量供货",
}

# 代理商状态
STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"
STATUS_TERMINATED = "terminated"

STATUS_NAMES = {
    STATUS_ACTIVE: "正常", STATUS_SUSPENDED: "暂停", STATUS_TERMINATED: "终止",
}


# ============================================================
# 返利档位体系(超额累进制, 基于月度进货额)
# ============================================================

# 档位 / 名称 / 下限 / 上限 / 边际返利率(超额部分适用)
REBATE_TIERS = [
    {"tier": "T0", "name": "未达门槛", "min": 0,        "max": 200000,   "rate": 0.0},
    {"tier": "T1", "name": "基础档",   "min": 200000,   "max": 500000,   "rate": 0.15},
    {"tier": "T2", "name": "进阶档",   "min": 500000,   "max": 1000000,  "rate": 0.25},
    {"tier": "T3", "name": "核心档",   "min": 1000000,  "max": float("inf"), "rate": 0.30},
]

# 风控等级
RISK_LEVEL_LOW = "low"
RISK_LEVEL_MEDIUM = "medium"
RISK_LEVEL_HIGH = "high"

RISK_LEVEL_NAMES = {
    RISK_LEVEL_LOW: "低风险", RISK_LEVEL_MEDIUM: "中风险", RISK_LEVEL_HIGH: "高风险",
}


def _calc_rebate(amount: float) -> tuple:
    """超额累进计算返利(类似个税计算)

    示例(60万):
        0-20万: 0%       → 0
        20-50万: 15%     → 30万×15% = 45000
        50-60万: 25%     → 10万×25% = 25000
        合计: 70000, 档位 T2

    Returns:
        (rebateAmount, tier, tierRate)
    """
    rebate = 0.0
    tier = "T0"
    tier_rate = 0.0
    for t in REBATE_TIERS:
        if amount >= t["min"]:
            taxable = min(amount, t["max"]) - t["min"]
            rebate += taxable * t["rate"]
            tier = t["tier"]
            tier_rate = t["rate"]
        else:
            break
    return round(rebate, 2), tier, tier_rate


def _calc_credit_score(purchase_count: int, total_purchases: float,
                       return_rate: float = 0.0,
                       payment_delay_rate: float = 0.0) -> tuple:
    """信用评分计算(0-100)

    规则:
        基础分 60 + 进货频次分(最高 20, 每次进货 +2) +
        进货额分(最高 20, 每 10 万 +1) - 退货扣分(退货率×100) - 延迟扣分(延迟率×50)

    Returns:
        (creditScore, riskLevel)
    """
    base_score = 60
    freq_score = min(purchase_count * 2, 20)
    amount_score = min(total_purchases / 100000, 20)
    return_penalty = return_rate * 100
    delay_penalty = payment_delay_rate * 50
    credit_score = base_score + freq_score + amount_score - return_penalty - delay_penalty
    credit_score = max(0, min(100, round(credit_score, 1)))
    if credit_score >= 80:
        risk_level = RISK_LEVEL_LOW
    elif credit_score >= 60:
        risk_level = RISK_LEVEL_MEDIUM
    else:
        risk_level = RISK_LEVEL_HIGH
    return credit_score, risk_level


class AgentService:
    def __init__(self, agent_repo: AgentRepository = AgentRepository()):
        self.agent_repo = agent_repo
        self.inventory_repo = InventoryRepository()
        self.product_repo = ProductRepository()

    async def upgrade(self, agent_id, to_level: str, pay_amount: float) -> dict:
        """代理商升级(等级变更 + 钱包充值)

        Raises:
            KeyError: 代理商不存在
        """
        async with get_lock(f"agent:{agent_id}"):
            agent = await self.agent_repo.get(agent_id)
            if not agent:
                raise KeyError(f"代理商 {agent_id} 不存在")
            old_level = await self.agent_repo.update_level(agent_id, to_level)
            new_wallet = await self.agent_repo.add_wallet(agent_id, pay_amount)
            return {
                "success": True,
                "agentId": agent_id,
                "fromLevel": old_level,
                "toLevel": to_level,
                "wallet": new_wallet,
                "logs": [
                    {"step": "升级", "level": "INFO", "msg": f"{old_level}→{to_level}"},
                    {"step": "钱包", "level": "INFO", "msg": f"充值 ¥{pay_amount}"},
                ],
            }

    async def downgrade(self, agent_id, reason: str = "考核未达标") -> dict:
        """代理商降级(S→A→B→C→D,已为 D 则保持 D)

        并发安全: 与 upgrade 对齐,使用 agent:{agentId} 锁防止并发降级丢失更新

        Raises:
            KeyError: 代理商不存在
        """
        logger.info("downgrade_start agent_id=%r reason=%s", agent_id, reason)
        async with get_lock(f"agent:{agent_id}"):
            logger.debug("downgrade_lock_acquired key=agent:%r", agent_id)
            agent = await self.agent_repo.get(agent_id)
            if not agent:
                logger.warning("downgrade_agent_not_found agent_id=%r", agent_id)
                raise KeyError(f"代理商 {agent_id} 不存在")
            old_level = await self.agent_repo.get_level(agent_id)
            new_level = await self.agent_repo.downgrade_level(agent_id)
            logger.info("downgrade_applied agent_id=%r %s->%s reason=%s",
                        agent_id, old_level, new_level, reason)
            return {
                "success": True,
                "agentId": agent_id,
                "fromLevel": old_level,
                "toLevel": new_level,
                "reason": reason,
                "logs": [{"step": "降级", "level": "WARN",
                          "msg": f"{old_level}→{new_level}, 原因: {reason}"}],
            }

    # ============================================================
    #  准入管理
    # ============================================================

    async def apply(self, company_name: str, contact_name: str,
                    contact_phone: str, region: str,
                    apply_level: str) -> dict:
        """申请入驻(提交资料)

        Raises:
            ValueError: 参数缺失 / 申请等级非法
        """
        if not all([company_name, contact_name, contact_phone, region, apply_level]):
            raise ValueError("公司名/联系人/电话/区域/申请等级不能为空")
        if apply_level not in LEVEL_DISCOUNTS:
            raise ValueError(f"申请等级非法: {apply_level}(须为 S/A/B/C/D)")

        apply_id = await self.agent_repo.next_apply_id()
        now = ts()
        apply_data = {
            "applyId": apply_id,
            "company_name": company_name,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            "region": region,
            "apply_level": apply_level,
            "status": "pending",
            "audit_remark": "",
            "created_at": now,
        }
        await self.agent_repo.save_apply(apply_data)
        logger.info("agent_apply_submitted apply_id=%r company=%s level=%s",
                    apply_id, company_name, apply_level)
        return {
            "success": True,
            "applyId": apply_id,
            "status": "pending",
            "applyLevel": apply_level,
            "logs": [{"step": "提交申请", "level": "INFO",
                      "msg": f"申请单号 {apply_id}, 等级 {apply_level}"}],
        }

    async def audit(self, apply_id, decision: str,
                     audit_remark: str = "") -> dict:
        """审核申请(decision: approved/rejected)

        通过则创建代理商档案(等级=申请等级, 钱包=0, 状态=active)

        Raises:
            KeyError: 申请不存在
            ValueError: decision 非法 / 申请已处理
        """
        if decision not in ("approved", "rejected"):
            raise ValueError("decision 须为 approved/rejected")

        # 防止并发重复审核同一申请单
        async with get_lock(f"agent_apply:{apply_id}"):
            app = await self.agent_repo.get_apply(apply_id)
            if not app:
                raise KeyError(f"申请 {apply_id} 不存在")
            if app["status"] != "pending":
                raise ValueError(f"申请 {apply_id} 已处理(当前状态: {app['status']})")

            now = ts()
            logs = []

            if decision == "rejected":
                await self.agent_repo.update_apply_status(
                    apply_id, "rejected", audit_remark)
                logs.append({"step": "审核拒绝", "level": "WARN",
                             "msg": f"申请 {apply_id} 已拒绝"})
                logger.info("agent_apply_rejected apply_id=%r", apply_id)
                return {
                    "success": True,
                    "applyId": apply_id,
                    "decision": "rejected",
                    "auditRemark": audit_remark,
                    "logs": logs,
                }

            # 通过:创建代理商档案
            new_agent_id = await self.agent_repo.next_agent_id()
            agent_data = {
                "id": new_agent_id,
                "name": app["company_name"],
                "level": app["apply_level"],
                "wallet": 0.0,
                "status": STATUS_ACTIVE,
                "contact_name": app["contact_name"],
                "contact_phone": app["contact_phone"],
                "region": app["region"],
                "address": "",
                "created_at": now,
                "updated_at": now,
                "total_sales": 0.0,
                "total_purchases": 0.0,
            }
            await self.agent_repo.save(new_agent_id, agent_data)
            await self.agent_repo.update_apply_status(
                apply_id, "approved", audit_remark)
            logs.append({"step": "审核通过", "level": "INFO",
                         "msg": f"创建代理商档案 id={new_agent_id}, 等级 {app['apply_level']}"})
            logger.info("agent_apply_approved apply_id=%r agent_id=%r",
                        apply_id, new_agent_id)
            return {
                "success": True,
                "applyId": apply_id,
                "decision": "approved",
                "agentId": new_agent_id,
                "level": app["apply_level"],
                "auditRemark": audit_remark,
                "logs": logs,
            }

    async def list_applications(self, status: str = None) -> dict:
        """申请列表(可按状态筛选: pending/approved/rejected)"""
        apps = await self.agent_repo.list_applies(status)
        return {
            "success": True,
            "count": len(apps),
            "applications": apps,
            "logs": [],
        }

    # ============================================================
    #  档案管理
    # ============================================================

    async def list_agents(self, level: str = None, status: str = None,
                          page: int = 1, page_size: int = 20) -> dict:
        """代理商列表(支持等级/状态筛选 + 分页)"""
        agents = await self.agent_repo.list_all()
        if level:
            agents = [a for a in agents if a.get("level") == level]
        if status:
            agents = [a for a in agents if a.get("status", STATUS_ACTIVE) == status]
        # 按创建时间倒序
        agents.sort(key=lambda a: a.get("created_at", ""), reverse=True)
        total = len(agents)
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        start = (page - 1) * page_size
        end = start + page_size
        page_items = agents[start:end]
        # 装饰: 增加等级名/状态名(拷贝避免回写 store)
        decorated = []
        for a in page_items:
            a = dict(a)
            a["levelName"] = LEVEL_NAMES.get(a.get("level", "D"), "实习代理商")
            a["statusName"] = STATUS_NAMES.get(
                a.get("status", STATUS_ACTIVE), "正常")
            decorated.append(a)
        return {
            "success": True,
            "count": total,
            "page": page,
            "pageSize": page_size,
            "agents": decorated,
            "logs": [],
        }

    async def get_detail(self, agent_id) -> dict:
        """代理商详情

        Raises:
            KeyError: 代理商不存在
        """
        agent = await self.agent_repo.get(agent_id)
        if not agent:
            raise KeyError(f"代理商 {agent_id} 不存在")
        agent = dict(agent)
        agent["levelName"] = LEVEL_NAMES.get(agent.get("level", "D"), "实习代理商")
        agent["statusName"] = STATUS_NAMES.get(
            agent.get("status", STATUS_ACTIVE), "正常")
        agent["discountRate"] = LEVEL_DISCOUNTS.get(agent.get("level", "D"), 0.8)
        return {"success": True, "agent": agent, "logs": []}

    async def update_profile(self, agent_id, contact_name: str = None,
                              contact_phone: str = None,
                              address: str = None) -> dict:
        """更新代理商资料(联系人/电话/地址)

        Raises:
            KeyError: 代理商不存在
            ValueError: 无可更新字段
        """
        allowed = {"contact_name": contact_name,
                   "contact_phone": contact_phone,
                   "address": address}
        update_fields = {k: v for k, v in allowed.items() if v is not None}
        if not update_fields:
            raise ValueError("无可更新字段(允许: contact_name/contact_phone/address)")

        async with get_lock(f"agent:{agent_id}"):
            agent = await self.agent_repo.get(agent_id)
            if not agent:
                raise KeyError(f"代理商 {agent_id} 不存在")
            update_fields["updated_at"] = ts()
            updated = await self.agent_repo.update_fields(agent_id, update_fields)
            logger.info("agent_profile_updated agent_id=%r fields=%s",
                        agent_id, list(update_fields.keys()))
            updated = dict(updated)
            updated["levelName"] = LEVEL_NAMES.get(
                updated.get("level", "D"), "实习代理商")
            return {
                "success": True,
                "agentId": agent_id,
                "agent": updated,
                "logs": [{"step": "资料更新", "level": "INFO",
                          "msg": f"已更新: {', '.join(k for k in update_fields if k != 'updated_at')}"}],
            }

    async def get_levels(self) -> dict:
        """等级体系说明(S/A/B/C/D 的权益)"""
        levels = []
        for lv in LEVEL_ORDER:
            levels.append({
                "level": lv,
                "name": LEVEL_NAMES[lv],
                "discountRate": LEVEL_DISCOUNTS[lv],
                "rights": LEVEL_RIGHTS[lv],
            })
        return {
            "success": True,
            "levels": levels,
            "logs": [],
        }

    # ============================================================
    #  进货管理
    # ============================================================

    async def purchase(self, agent_id, items: list) -> dict:
        """代理商进货下单

        流程:
            1. 校验代理商存在 + 状态 active
            2. 校验商品 & 库存(扣减, 嵌套 stock:{pid} 锁)
            3. 价格计算(零售价 × 等级折扣)
            4. 扣减代理商钱包(余额不足抛 ValueError)
            5. 保存进货记录 + 累加累计进货额

        Raises:
            KeyError: 代理商不存在
            ValueError: 状态异常 / 商品不存在 / 库存不足 / 钱包不足
        """
        if not items:
            raise ValueError("进货商品列表不能为空")

        async with get_lock(f"agent:{agent_id}"):
            agent = await self.agent_repo.get(agent_id)
            if not agent:
                raise KeyError(f"代理商 {agent_id} 不存在")
            if agent.get("status", STATUS_ACTIVE) != STATUS_ACTIVE:
                raise ValueError(
                    f"代理商状态异常: {agent.get('status')}, 仅 active 可进货")

            logs = []
            level = agent.get("level", "D")
            discount = LEVEL_DISCOUNTS.get(level, 0.8)
            wallet = float(agent.get("wallet", 0))

            # 1. 校验商品 & 扣减库存
            resolved_items = []
            goods_total = 0.0
            for item in items:
                pid = str(item["productId"])
                qty = int(item["quantity"])
                if qty <= 0:
                    raise ValueError(f"商品 {pid} 数量须为正整数")

                async with get_lock(f"stock:{pid}"):
                    product = await self.product_repo.get_by_id(pid)
                    if not product:
                        raise ValueError(f"商品 {pid} 不存在")
                    if product.get("stock", 0) < qty:
                        raise ValueError(
                            f"库存不足: {pid} 当前 {product.get('stock', 0)}, 需 {qty}"
                        )
                    new_stock = await self.inventory_repo.deduct(pid, qty)
                    logs.append({"step": "扣减库存", "level": "INFO",
                                 "msg": f"{pid} ×{qty}, 剩余 {new_stock}"})

                unit_price = float(product["price"])
                subtotal = round(unit_price * qty, 2)
                goods_total += subtotal
                resolved_items.append({
                    "productId": pid,
                    "productName": product.get("name", pid),
                    "quantity": qty,
                    "unitPrice": unit_price,
                    "subtotal": subtotal,
                })

            # 2. 价格计算(等级折扣)
            total_amount = round(goods_total * discount, 2)
            logs.append({"step": "价格计算", "level": "INFO",
                         "msg": f"商品 ¥{round(goods_total,2)}, 折扣 {discount}, 实付 ¥{total_amount}"})

            # 3. 钱包扣减(余额不足)
            if wallet < total_amount:
                raise ValueError(
                    f"钱包余额不足: 当前 ¥{wallet}, 需 ¥{total_amount}")
            new_wallet = await self.agent_repo.add_wallet(agent_id, -total_amount)
            logs.append({"step": "扣减钱包", "level": "INFO",
                         "msg": f"-¥{total_amount}, 余额 ¥{new_wallet}"})

            # 4. 保存进货记录
            purchase_id = await self.agent_repo.next_purchase_id()
            now = ts()
            purchase = {
                "purchaseId": purchase_id,
                "agentId": agent_id,
                "items": resolved_items,
                "totalAmount": total_amount,
                "goodsTotal": round(goods_total, 2),
                "discountRate": discount,
                "level": level,
                "status": "paid",
                "created_at": now,
            }
            await self.agent_repo.save_purchase(purchase)

            # 5. 累加累计进货额
            await self.agent_repo.update_fields(agent_id, {
                "total_purchases": float(agent.get("total_purchases", 0)) + total_amount,
                "updated_at": now,
            })
            logs.append({"step": "进货完成", "level": "INFO",
                         "msg": f"进货单号 {purchase_id}, 实付 ¥{total_amount}"})
            logger.info("agent_purchase agent_id=%r purchase_id=%s amount=%.2f",
                        agent_id, purchase_id, total_amount)
            return {
                "success": True,
                "agentId": agent_id,
                "purchaseId": purchase_id,
                "totalAmount": total_amount,
                "goodsTotal": round(goods_total, 2),
                "discountRate": discount,
                "wallet": new_wallet,
                "logs": logs,
            }

    async def list_purchases(self, agent_id, page: int = 1,
                             page_size: int = 20) -> dict:
        """进货记录(分页)

        Raises:
            KeyError: 代理商不存在
        """
        agent = await self.agent_repo.get(agent_id)
        if not agent:
            raise KeyError(f"代理商 {agent_id} 不存在")
        purchases = await self.agent_repo.list_purchases_by_agent(agent_id)
        total = len(purchases)
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        start = (page - 1) * page_size
        end = start + page_size
        page_items = purchases[start:end]
        return {
            "success": True,
            "agentId": agent_id,
            "count": total,
            "page": page,
            "pageSize": page_size,
            "purchases": page_items,
            "logs": [],
        }

    async def get_purchase_detail(self, agent_id, purchase_id) -> dict:
        """进货明细

        Raises:
            KeyError: 代理商/进货记录不存在
        """
        agent = await self.agent_repo.get(agent_id)
        if not agent:
            raise KeyError(f"代理商 {agent_id} 不存在")
        purchase = await self.agent_repo.get_purchase(purchase_id)
        if not purchase:
            raise KeyError(f"进货记录 {purchase_id} 不存在")
        # 越权校验: 进货记录须属于该代理商
        if purchase.get("agentId") != agent_id:
            raise KeyError(f"进货记录 {purchase_id} 不属于代理商 {agent_id}")
        return {"success": True, "purchase": purchase, "logs": []}

    # ============================================================
    #  返利结算管理
    # ============================================================

    async def rebate_calc(self, agent_id, purchase_amount: float,
                           period: str = None) -> dict:
        """返利计算(超额累进制, 基于月度进货额)

        生成返利记录(status=pending), 等待提现。

        Raises:
            KeyError: 代理商不存在
            ValueError: 进货额非法
        """
        if purchase_amount < 0:
            raise ValueError("进货额不能为负数")
        if not period:
            from datetime import datetime, timezone, timedelta
            now_sh = datetime.now(timezone(timedelta(hours=8)))
            period = now_sh.strftime("%Y-%m")

        async with get_lock(f"agent:{agent_id}"):
            agent = await self.agent_repo.get(agent_id)
            if not agent:
                raise KeyError(f"代理商 {agent_id} 不存在")

            rebate_amount, tier, tier_rate = _calc_rebate(purchase_amount)
            rebate_id = await self.agent_repo.next_rebate_id()
            now = ts()
            rebate_data = {
                "rebateId": rebate_id,
                "agentId": agent_id,
                "period": period,
                "tier": tier,
                "purchaseAmount": round(purchase_amount, 2),
                "rebateRate": tier_rate,
                "rebateAmount": rebate_amount,
                "status": "pending",
                "withdrawnAt": "",
                "createdAt": now,
            }
            await self.agent_repo.save_rebate(rebate_data)
            logs = [{"step": "返利计算", "level": "INFO",
                     "msg": f"进货额 ¥{purchase_amount:.2f}, 档位 {tier}, 返利 ¥{rebate_amount:.2f}"}]
            if tier == "T0":
                logs.append({"step": "未达门槛", "level": "WARN",
                             "msg": "进货额未达 20 万门槛, 返利 0 元"})
            logger.info("agent_rebate_calc agent_id=%r tier=%s amount=%.2f rebate=%.2f",
                        agent_id, tier, purchase_amount, rebate_amount)
            return {
                "success": True,
                "agentId": agent_id,
                "rebateId": rebate_id,
                "period": period,
                "tier": tier,
                "purchaseAmount": round(purchase_amount, 2),
                "rebateRate": tier_rate,
                "rebateAmount": rebate_amount,
                "status": "pending",
                "logs": logs,
            }

    async def list_rebates(self, agent_id, page: int = 1,
                            page_size: int = 20, status: str = None) -> dict:
        """返利记录列表(分页, 可按状态筛选)

        Raises:
            KeyError: 代理商不存在
        """
        agent = await self.agent_repo.get(agent_id)
        if not agent:
            raise KeyError(f"代理商 {agent_id} 不存在")
        rebates = await self.agent_repo.list_rebates_by_agent(agent_id, status)
        total = len(rebates)
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        start = (page - 1) * page_size
        end = start + page_size
        page_items = rebates[start:end]
        return {
            "success": True,
            "agentId": agent_id,
            "count": total,
            "page": page,
            "pageSize": page_size,
            "rebates": page_items,
            "logs": [],
        }

    async def rebate_withdraw(self, agent_id, rebate_id) -> dict:
        """返利提现(转入钱包)

        将 pending 返利转入代理商钱包, 状态改为 withdrawn。

        Raises:
            KeyError: 代理商/返利记录不存在 / 返利记录不属于该代理商
            ValueError: 返利记录已提现
        """
        async with get_lock(f"agent:{agent_id}"):
            agent = await self.agent_repo.get(agent_id)
            if not agent:
                raise KeyError(f"代理商 {agent_id} 不存在")
            rebate = await self.agent_repo.get_rebate(rebate_id)
            if not rebate:
                raise KeyError(f"返利记录 {rebate_id} 不存在")
            if rebate.get("agentId") != agent_id:
                raise KeyError(f"返利记录 {rebate_id} 不属于代理商 {agent_id}")
            if rebate.get("status") != "pending":
                raise ValueError(
                    f"返利记录 {rebate_id} 已提现(当前状态: {rebate.get('status')})")

            amount = float(rebate.get("rebateAmount", 0))
            new_wallet = await self.agent_repo.add_wallet(agent_id, amount)
            now = ts()
            await self.agent_repo.update_rebate_fields(rebate_id, {
                "status": "withdrawn",
                "withdrawnAt": now,
            })
            logs = [{"step": "返利提现", "level": "INFO",
                     "msg": f"返利 ¥{amount:.2f} 转入钱包, 余额 ¥{new_wallet:.2f}"}]
            logger.info("agent_rebate_withdraw agent_id=%r rebate_id=%s amount=%.2f",
                        agent_id, rebate_id, amount)
            return {
                "success": True,
                "agentId": agent_id,
                "rebateId": rebate_id,
                "amount": amount,
                "wallet": new_wallet,
                "status": "withdrawn",
                "logs": logs,
            }

    async def rebate_summary(self, agent_id) -> dict:
        """返利汇总(本年累计/本月/可提现/已提现)

        Raises:
            KeyError: 代理商不存在
        """
        agent = await self.agent_repo.get(agent_id)
        if not agent:
            raise KeyError(f"代理商 {agent_id} 不存在")
        rebates = await self.agent_repo.list_rebates_by_agent(agent_id)

        from datetime import datetime, timezone, timedelta
        now_sh = datetime.now(timezone(timedelta(hours=8)))
        current_year = now_sh.strftime("%Y")
        current_month = now_sh.strftime("%Y-%m")

        year_total = 0.0
        month_total = 0.0
        withdrawable = 0.0
        withdrawn_total = 0.0
        for r in rebates:
            period = r.get("period", "")
            amount = float(r.get("rebateAmount", 0))
            if period.startswith(current_year):
                year_total += amount
            if period == current_month:
                month_total += amount
            if r.get("status") == "pending":
                withdrawable += amount
            elif r.get("status") == "withdrawn":
                withdrawn_total += amount

        return {
            "success": True,
            "agentId": agent_id,
            "yearTotal": round(year_total, 2),
            "monthTotal": round(month_total, 2),
            "withdrawable": round(withdrawable, 2),
            "withdrawnTotal": round(withdrawn_total, 2),
            "totalCount": len(rebates),
            "logs": [],
        }

    async def get_rebate_tiers(self) -> dict:
        """返利档位说明(T0-T3 规则, 超额累进制)"""
        tiers = []
        for t in REBATE_TIERS:
            min_wan = int(t["min"] / 10000)
            if t["max"] == float("inf"):
                range_str = f"{min_wan}万以上"
                max_val = None
            else:
                max_wan = int(t["max"] / 10000)
                range_str = f"{min_wan}-{max_wan}万"
                max_val = t["max"]
            tiers.append({
                "tier": t["tier"],
                "name": t["name"],
                "range": range_str,
                "min": t["min"],
                "max": max_val,
                "rate": t["rate"],
                "desc": f"月度进货额 {range_str}, 超额部分返利率 {t['rate']*100:.0f}%",
            })
        return {
            "success": True,
            "tiers": tiers,
            "calcMode": "超额累进制(类似个税计算, 分段计税累加)",
            "logs": [],
        }

    # ============================================================
    #  风控管理
    # ============================================================

    async def risk_report(self, agent_id) -> dict:
        """风控报告(信用评分 + 异常指标 + 预警)

        优先返回最近一次评级记录, 若无则实时计算。

        Raises:
            KeyError: 代理商不存在
        """
        agent = await self.agent_repo.get(agent_id)
        if not agent:
            raise KeyError(f"代理商 {agent_id} 不存在")

        # 获取该代理商的风控记录(按时间倒序, 取最新评级)
        risks = await self.agent_repo.list_risks_by_agent(agent_id)
        latest_assessment = None
        agent_alerts = []
        for r in risks:
            if r.get("type") == "assessment" and latest_assessment is None:
                latest_assessment = r
            if r.get("type") == "alert":
                agent_alerts.append(r)

        if latest_assessment:
            credit_score = float(latest_assessment.get("creditScore", 0))
            risk_level = latest_assessment.get("riskLevel", RISK_LEVEL_MEDIUM)
            indicators = latest_assessment.get("indicators", {})
            latest_risk_id = latest_assessment.get("riskId")
        else:
            # 无评级记录, 实时计算
            purchases = await self.agent_repo.list_purchases_by_agent(agent_id)
            purchase_count = len(purchases)
            total_purchases = float(agent.get("total_purchases", 0))
            return_rate = float(agent.get("return_rate", 0.0))
            payment_delay_rate = float(agent.get("payment_delay_rate", 0.0))
            credit_score, risk_level = _calc_credit_score(
                purchase_count, total_purchases, return_rate, payment_delay_rate)
            indicators = {
                "purchaseCount": purchase_count,
                "totalPurchases": round(total_purchases, 2),
                "returnRate": return_rate,
                "paymentDelayRate": payment_delay_rate,
                "purchaseStability": round(min(purchase_count / 10, 1.0), 2),
            }
            latest_risk_id = None

        # 跨区域销售检测(实时)
        cross_region_alert = self._detect_cross_region(agent)

        # 合并预警(alert 记录 + 实时检测)
        all_alerts = list(agent_alerts)
        if cross_region_alert and not any(
            a.get("alertType") == "cross_region" or a.get("type") == "cross_region"
            for a in all_alerts
        ):
            all_alerts.append(cross_region_alert)

        return {
            "success": True,
            "agentId": agent_id,
            "agentName": agent.get("name", ""),
            "creditScore": credit_score,
            "riskLevel": risk_level,
            "riskLevelName": RISK_LEVEL_NAMES.get(risk_level, "中风险"),
            "indicators": indicators,
            "alerts": all_alerts,
            "latestAssessmentId": latest_risk_id,
            "logs": [],
        }

    async def risk_alerts(self) -> dict:
        """窜货预警列表(admin, 跨区域销售检测)

        扫描所有代理商, 检测实际销售区域与授权区域是否匹配。
        """
        agents = await self.agent_repo.list_all()
        alerts = []
        for agent in agents:
            alert = self._detect_cross_region(agent)
            if alert:
                alerts.append({
                    "agentId": agent.get("id"),
                    "agentName": agent.get("name", ""),
                    **alert,
                })
        return {
            "success": True,
            "count": len(alerts),
            "alerts": alerts,
            "logs": [],
        }

    async def risk_assess(self, agent_id) -> dict:
        """信用评级(基于进货/退货/付款记录)

        计算信用评分(0-100)并保存评级记录, 同时检测窜货预警。

        Raises:
            KeyError: 代理商不存在
        """
        async with get_lock(f"agent:{agent_id}"):
            agent = await self.agent_repo.get(agent_id)
            if not agent:
                raise KeyError(f"代理商 {agent_id} 不存在")

            # 基于进货记录评估
            purchases = await self.agent_repo.list_purchases_by_agent(agent_id)
            purchase_count = len(purchases)
            total_purchases = float(agent.get("total_purchases", 0))
            return_rate = float(agent.get("return_rate", 0.0))
            payment_delay_rate = float(agent.get("payment_delay_rate", 0.0))

            credit_score, risk_level = _calc_credit_score(
                purchase_count, total_purchases, return_rate, payment_delay_rate)
            stability = round(min(purchase_count / 10, 1.0), 2)

            # 窜货预警检测
            alerts = []
            cross_alert = self._detect_cross_region(agent)
            if cross_alert:
                alerts.append(cross_alert)

            # 保存评级记录
            risk_id = await self.agent_repo.next_risk_id()
            now = ts()
            risk_data = {
                "riskId": risk_id,
                "agentId": agent_id,
                "type": "assessment",
                "creditScore": credit_score,
                "riskLevel": risk_level,
                "indicators": {
                    "purchaseCount": purchase_count,
                    "totalPurchases": round(total_purchases, 2),
                    "returnRate": return_rate,
                    "paymentDelayRate": payment_delay_rate,
                    "purchaseStability": stability,
                },
                "alerts": alerts,
                "createdAt": now,
            }
            await self.agent_repo.save_risk(risk_data)

            logs = [{"step": "信用评级", "level": "INFO",
                     "msg": f"信用分 {credit_score}, 风险等级 {risk_level}"}]
            if alerts:
                logs.append({"step": "窜货预警", "level": "WARN",
                             "msg": f"检测到 {len(alerts)} 条预警"})
            logger.info("agent_risk_assess agent_id=%r score=%s level=%s",
                        agent_id, credit_score, risk_level)
            return {
                "success": True,
                "agentId": agent_id,
                "riskId": risk_id,
                "creditScore": credit_score,
                "riskLevel": risk_level,
                "riskLevelName": RISK_LEVEL_NAMES.get(risk_level, "中风险"),
                "indicators": risk_data["indicators"],
                "alerts": alerts,
                "logs": logs,
            }

    def _detect_cross_region(self, agent: dict) -> Optional[dict]:
        """检测跨区域销售(窜货预警)

        比对代理商实际销售区域(sales_region)与授权区域(region),
        不一致则返回预警信息。
        """
        authorized_region = agent.get("region", "")
        sales_region = agent.get("sales_region", "")
        if sales_region and authorized_region and sales_region != authorized_region:
            return {
                "type": "cross_region",
                "alertType": "cross_region",
                "level": "high",
                "desc": f"销售区域({sales_region})与授权区域({authorized_region})不匹配",
                "authorizedRegion": authorized_region,
                "detectedRegion": sales_region,
            }
        return None
