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
