"""37号·AI智能网站同盟模块·核心服务

核心业务(设计文档 v1.0 §2):
    - 入盟网关: 超级会员申请 → AI预审(评分器) → 人工审核 → 签约
      → 试用(90天) → 转正; 清退 90 天冷却
    - 商品中心: 三道门禁上架(资质/溯源/合规) → 站内交易下单
    - 交易分润: 成交价 15% 抽佣五方拆账(T+1 结算/幂等/冲正),
      双写 role profit_ledger + wallet 货款入账
    - 评价信用: 双向评价(1-5星)/折叠/星级聚合

对接模块(复用不合并):
    - ai_scoring: alliance_onboarding 评分器(入盟 AI 预审)
    - trace_prod: 酒类商品溯源门禁(batch 须已放行)
    - role: record_external_settlement 分润统一总账(venue 已验证范式)
    - wallet: 货款/分润入账(deposit)
    - compliance: 入驻行为上报(best-effort)

锁保护:
    - 入盟审核: lock:alliance:application:{id}
    - 下单扣库存: lock:alliance:product:{id}
    - 结算: lock:alliance:settle:{orderId}

异常约定(遵循项目约定):
    - KeyError → 404(申请/商户/商品/订单/结算单不存在)
    - ValueError → 409(门槛不达/状态非法/资质缺失/库存不足/已结算等)
"""

import logging
from datetime import datetime, UTC, timedelta

from core.locks import get_lock
from repositories.alliance_repository import (
    AllianceRepository,
    CATEGORIES, CATEGORY_SEEDS, TRACE_LEVEL_FULL,
    ONBOARD_MEMBER_LEVEL_MIN, ONBOARD_CREDIT_MIN, REJECT_COOLDOWN_DAYS,
    STATUS_PENDING, STATUS_AI_REVIEWING, STATUS_MANUAL_REVIEWING,
    STATUS_SIGNED, STATUS_PROBATION, STATUS_ACTIVE, STATUS_SUSPENDED,
    STATUS_TERMINATED, STATUS_REJECTED, STATUS_TRANSITIONS,
    PROBATION_DAYS, AI_PASS_SCORE, AI_REVIEW_SCORE,
    PRODUCT_STATUS_PENDING, PRODUCT_STATUS_ACTIVE, PRODUCT_STATUS_OFFLINE,
    PRODUCT_STATUS_BLOCKED, PRODUCT_BANNED_WORDS,
    DEFAULT_SHARE_RATES, PLATFORM_COMMISSION_RATE,
    SETTLEMENT_STATUS_SETTLED, SETTLEMENT_STATUS_REVERSED,
    REVIEW_MIN_SCORE, REVIEW_MAX_SCORE, SETTLE_DELAY_HOURS,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AllianceService:
    """37号·AI智能网站同盟编排(入盟/商品/交易分润/评价)"""

    def __init__(self, repo: AllianceRepository = AllianceRepository()):
        self.repo = repo

    # ============================================================
    # 1. 入盟网关
    # ============================================================

    async def apply(self, member_id: int, category: str,
                    shop_name: str, credentials: list = None,
                    referrer_member_id: int = None) -> dict:
        """超级会员入盟申请(自动 AI 预审)

        门槛(设计文档 §1.3): 会员等级≥Lv4 + 实名 + 信用分≥80 +
        清退 90 天冷却。AI 预审三档: ≥80 快车道 / 60-79 人工重点审 /
        <60 直接拒。

        Raises:
            ValueError: 类目无效/门槛不达/冷却期内/重复在盟
        """
        if category not in CATEGORIES:
            raise ValueError(
                f"类目无效({category}, 须为{'/'.join(CATEGORIES)})")
        if not (shop_name or "").strip():
            raise ValueError("店铺名称不能为空")
        await self.repo.ensure_categories()

        # 门槛校验: 会员等级(实名/信用 P0 用申请字段简化口径, P1 接 member/role)
        from repositories.member_repository import MemberRepository
        member = await MemberRepository().get_by_id(member_id)
        if member is None:
            raise KeyError(f"会员不存在(memberId={member_id})")
        level = int(member.get("level", 1) or 1)
        if level < ONBOARD_MEMBER_LEVEL_MIN:
            raise ValueError(
                f"会员等级不足(Lv{level}<{ONBOARD_MEMBER_LEVEL_MIN}, "
                "须为超级会员)")

        # 重复在盟检查: 在途申请或有效商铺(同一会员均不允许)
        existing = await self.repo.find_merchant_by_member(member_id)
        if existing and existing.get("status") in (
                STATUS_PENDING, STATUS_AI_REVIEWING,
                STATUS_MANUAL_REVIEWING, STATUS_SIGNED,
                STATUS_PROBATION, STATUS_ACTIVE, STATUS_SUSPENDED):
            raise ValueError(
                f"会员已有在盟申请或商铺(merchantId="
                f"{existing['merchantId']}, 状态{existing['status']})")
        applications = await self.repo.list_applications(limit=2000)
        pending_app = next(
            (a for a in applications
             if a.get("memberId") == member_id
             and a.get("status") in (STATUS_PENDING, STATUS_AI_REVIEWING,
                                     STATUS_MANUAL_REVIEWING, STATUS_SIGNED)),
            None)
        if pending_app is not None:
            raise ValueError(
                f"会员存在在途入盟申请(applicationId="
                f"{pending_app['applicationId']}, "
                f"状态{pending_app['status']})")

        # 清退冷却期
        recent = await self.repo.find_terminated_recent(
            member_id, REJECT_COOLDOWN_DAYS)
        if recent is not None:
            raise ValueError(
                f"清退冷却期内({REJECT_COOLDOWN_DAYS}天, 上次"
                f"{recent.get('status')}于{recent.get('updatedAt', '')[:10]})")

        # AI 预审(评分器)
        category_meta = CATEGORY_SEEDS[category]
        required_creds = category_meta["requiredCredentials"]
        provided = [c for c in (credentials or []) if (c or "").strip()]
        from services.ai_scoring_service import AllianceOnboardingScorer
        scorer = AllianceOnboardingScorer()
        ai_result = await scorer.score({
            "applicantId": member_id,
            "memberLevel": level,
            "creditScore": member.get("creditScore", 85)
            if isinstance(member.get("creditScore"), int) else 85,
            "realnameVerified": bool(member.get("realnameVerified")),
            "credentialsTotal": len(required_creds),
            "credentialsProvided": len(provided),
            "gridOccupancy": 0, "gridCap": category_meta["gridCap"],
        })

        application_id = await self.repo.next_id("application")
        # AI 三档路由: reject → 直接拒绝; 其余进人工审核
        status = (STATUS_REJECTED if ai_result["action"] == "reject"
                  else STATUS_MANUAL_REVIEWING)
        application = {
            "applicationId": application_id,
            "memberId": member_id,
            "category": category,
            "shopName": shop_name.strip(),
            "credentials": provided,
            "referrerMemberId": referrer_member_id,
            "aiReview": {
                "score": ai_result["score"],
                "level": ai_result["level"],
                "action": ai_result["action"],
                "factors": ai_result["factors"],
            },
            "status": status,
            "reviewer": "",
            "reviewNote": "",
            "createdAt": _now_iso(),
        }
        await self.repo.save_application(application)

        # 入驻行为上报(best-effort, compliance module_name 自由参数)
        try:
            from services.compliance_service import ComplianceService
            await ComplianceService().monitor_behavior(
                module_name="alliance", behavior_type="onboarding_apply",
                behavior_data={
                    "memberId": member_id, "category": category,
                    "aiScore": ai_result["score"]})
        except Exception as exc:
            logger.warning("alliance_apply_report_failed: %s", exc)

        return application

    async def audit_application(self, application_id: int, approved: bool,
                                reviewer: str = "admin",
                                note: str = "") -> dict:
        """人工终审(approved → 签约生成同盟商; 拒 → rejected)

        Raises:
            KeyError: 申请不存在
            ValueError: 状态非法
        """
        async with get_lock(f"alliance:application:{application_id}"):
            application = await self.repo.get_application(application_id)
            if application is None:
                raise KeyError(f"申请不存在(applicationId={application_id})")
            if application["status"] != STATUS_MANUAL_REVIEWING:
                raise ValueError(
                    f"申请状态非法(当前{application['status']}, "
                    f"须为{STATUS_MANUAL_REVIEWING})")
            if approved:
                application.update({"status": STATUS_SIGNED,
                                    "reviewer": reviewer,
                                    "reviewNote": note,
                                    "auditedAt": _now_iso()})
                # 签约即建档: 创建同盟商记录(signed → 激活试用)
                merchant_id = await self.repo.next_id("merchant")
                merchant = {
                    "merchantId": merchant_id,
                    "applicationId": application_id,
                    "memberId": application["memberId"],
                    "category": application["category"],
                    "shopName": application["shopName"],
                    "referrerMemberId": application.get("referrerMemberId"),
                    "status": STATUS_SIGNED,
                    "grade": "C",          # 等级 D/C/B/A/S(venue 范式, 考核 P1)
                    "creditScore": 100,     # 信用分起步, 违规扣减(P1 接 role)
                    "ratingAvg": 0.0,
                    "ratingCount": 0,
                    "activatedAt": "",
                    "terminatedAt": "",
                    "createdAt": _now_iso(),
                    "updatedAt": _now_iso(),
                }
                await self.repo.save_merchant(merchant)
            else:
                application.update({"status": STATUS_REJECTED,
                                    "reviewer": reviewer,
                                    "reviewNote": note or "人工审核未通过",
                                    "auditedAt": _now_iso()})
            await self.repo.save_application(application)
            return application

    def _transition(self, merchant: dict, target: str) -> None:
        """状态转移校验(设计文档 §2.1 转移表)

        Raises:
            ValueError: 非法转移
        """
        allowed = STATUS_TRANSITIONS.get(merchant["status"], ())
        if target not in allowed:
            raise ValueError(
                f"状态转移非法({merchant['status']}→{target}, "
                f"允许:{'/'.join(allowed) or '终态'})")

    async def activate_merchant(self, merchant_id: int) -> dict:
        """激活进入试用期(signed→probation)

        Raises:
            KeyError: 商户不存在 / ValueError: 状态非法
        """
        merchant = await self.repo.get_merchant(merchant_id)
        if merchant is None:
            raise KeyError(f"商户不存在(merchantId={merchant_id})")
        self._transition(merchant, STATUS_PROBATION)
        merchant.update({"status": STATUS_PROBATION,
                         "activatedAt": _now_iso(),
                         "updatedAt": _now_iso()})
        return await self.repo.save_merchant(merchant)

    async def confirm_merchant(self, merchant_id: int) -> dict:
        """试用转正(probation→active; P0 简化为人工确认, P1 接考核)

        Raises:
            KeyError: 商户不存在 / ValueError: 状态非法
        """
        merchant = await self.repo.get_merchant(merchant_id)
        if merchant is None:
            raise KeyError(f"商户不存在(merchantId={merchant_id})")
        self._transition(merchant, STATUS_ACTIVE)
        merchant.update({"status": STATUS_ACTIVE, "updatedAt": _now_iso()})
        return await self.repo.save_merchant(merchant)

    async def suspend_merchant(self, merchant_id: int,
                               reason: str = "") -> dict:
        """暂停(probation/active→suspended)

        Raises:
            KeyError: 商户不存在 / ValueError: 状态非法
        """
        merchant = await self.repo.get_merchant(merchant_id)
        if merchant is None:
            raise KeyError(f"商户不存在(merchantId={merchant_id})")
        self._transition(merchant, STATUS_SUSPENDED)
        merchant.update({"status": STATUS_SUSPENDED,
                         "suspendReason": reason, "updatedAt": _now_iso()})
        # 暂停即全量下架在售商品(保护消费者)
        for product in await self.repo.list_products(
                merchant_id=merchant_id, status=PRODUCT_STATUS_ACTIVE):
            product["status"] = PRODUCT_STATUS_OFFLINE
            product["offlineReason"] = f"商户暂停: {reason}"
            await self.repo.save_product(product)
        return await self.repo.save_merchant(merchant)

    async def terminate_merchant(self, merchant_id: int,
                                 reason: str = "") -> dict:
        """终止(主动退出/强制清退; 结清在途分润由结算域幂等保障)

        同步关闭关联在途申请(避免在途申请状态悬挂), 90 天冷却。

        Raises:
            KeyError: 商户不存在 / ValueError: 状态非法
        """
        merchant = await self.repo.get_merchant(merchant_id)
        if merchant is None:
            raise KeyError(f"商户不存在(merchantId={merchant_id})")
        self._transition(merchant, STATUS_TERMINATED)
        merchant.update({"status": STATUS_TERMINATED,
                         "terminatedReason": reason,
                         "terminatedAt": _now_iso(),
                         "updatedAt": _now_iso()})
        # 终止即全量下架
        for product in await self.repo.list_products(
                merchant_id=merchant_id, status=PRODUCT_STATUS_ACTIVE):
            product["status"] = PRODUCT_STATUS_OFFLINE
            product["offlineReason"] = f"商户终止: {reason}"
            await self.repo.save_product(product)
        # 关联在途申请同步关闭
        application = await self.repo.get_application(
            merchant.get("applicationId", 0))
        if application is not None and application.get("status") in (
                STATUS_PENDING, STATUS_AI_REVIEWING,
                STATUS_MANUAL_REVIEWING, STATUS_SIGNED):
            application["status"] = STATUS_TERMINATED
            application["terminatedAt"] = _now_iso()
            await self.repo.save_application(application)
        return await self.repo.save_merchant(merchant)

    async def list_merchants(self, status: str = None,
                             category: str = None) -> list[dict]:
        return await self.repo.list_merchants(status=status,
                                              category=category)

    async def get_merchant(self, merchant_id: int) -> dict:
        merchant = await self.repo.get_merchant(merchant_id)
        if merchant is None:
            raise KeyError(f"商户不存在(merchantId={merchant_id})")
        return merchant

    async def list_applications(self, status: str = None) -> list[dict]:
        return await self.repo.list_applications(status=status)

    async def list_categories(self) -> list[dict]:
        await self.repo.ensure_categories()
        return await self.repo.list_categories()

    # ============================================================
    # 2. 商品中心(三道门禁)
    # ============================================================

    @staticmethod
    def _product_compliance_check(name: str, description: str) -> list[str]:
        """第三道门禁: 合规(禁用词, 复用 attract 口径 + 食品宣称补充)"""
        text = f"{name} {description}"
        return [w for w in PRODUCT_BANNED_WORDS if w in text]

    async def create_product(self, merchant_id: int, name: str,
                             description: str, price: float, stock: int,
                             trace_batch_no: str = "",
                             trace_credentials: list = None) -> dict:
        """商品上架(三道门禁: 资质/溯源/合规)

        门禁:
            1. 资质: 商户申请时资质已核(入盟审核把关)
            2. 溯源: 酒类必须挂 trace_prod 已放行批次; 其他类目至少
               提交简化溯源凭证(批次/产地/检疫等)
            3. 合规: 禁用词扫描(极限词/医疗功效)

        Raises:
            KeyError: 商户不存在
            ValueError: 商户非在售态/参数非法/溯源门禁不过/含禁用词
        """
        merchant = await self.repo.get_merchant(merchant_id)
        if merchant is None:
            raise KeyError(f"商户不存在(merchantId={merchant_id})")
        if merchant["status"] not in (STATUS_ACTIVE, STATUS_PROBATION):
            raise ValueError(
                f"商户非在售状态(当前{merchant['status']}, "
                f"须为{STATUS_ACTIVE}/{STATUS_PROBATION})")
        if not (name or "").strip():
            raise ValueError("商品名称不能为空")
        if price <= 0:
            raise ValueError("商品价格必须大于 0")
        if stock < 0:
            raise ValueError("库存不能为负")

        category = merchant["category"]
        category_meta = CATEGORY_SEEDS[category]

        # 溯源门禁
        trace_info = {"level": category_meta["traceLevel"],
                      "batchNo": "", "credentials": [],
                      "traceVerified": False}
        if category_meta["traceLevel"] == TRACE_LEVEL_FULL:
            # 酒类: trace_prod 批次须存在且已放行(release_batch 后
            # batch.status == "released", 7 工段完成+瓶码绑定)
            if not trace_batch_no:
                raise ValueError("酒类商品必须绑定溯源批次号(traceBatchNo)")
            try:
                from repositories.trace_prod_repository import (
                    TraceProdRepository,
                )
                batch = await TraceProdRepository().get_batch(trace_batch_no)
                if batch is None:
                    raise ValueError(
                        f"溯源批次不存在(batchNo={trace_batch_no})")
                if batch.get("status") != "released":
                    raise ValueError(
                        f"溯源批次未放行(batchNo={trace_batch_no}, 当前"
                        f"状态{batch.get('status')}, 须 7 工段完成+瓶码"
                        "绑定后放行)")
                trace_info.update({"batchNo": trace_batch_no,
                                   "traceVerified": True})
            except ImportError:
                # 溯源模块不可用: 保守拒绝酒类上架
                raise ValueError("溯源服务不可用, 酒类商品暂不可上架")
        else:
            provided = [c for c in (trace_credentials or [])
                        if (c or "").strip()]
            if not provided:
                raise ValueError(
                    "须提交简化溯源凭证(traceCredentials: 批次/产地/检疫/冷链等)")
            trace_info["credentials"] = provided

        # 合规门禁
        banned = self._product_compliance_check(name, description or "")
        if banned:
            raise ValueError(f"商品文案含禁用词({banned}), 不可上架")

        product_id = await self.repo.next_id("product")
        sku = f"AL-{category[:2].upper()}-{product_id:06d}"
        product = {
            "productId": product_id,
            "sku": sku,
            "merchantId": merchant_id,
            "category": category,
            "name": name.strip(),
            "description": (description or "").strip(),
            "price": round(float(price), 2),
            "stock": int(stock),
            "trace": trace_info,
            "status": PRODUCT_STATUS_ACTIVE,
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        return await self.repo.save_product(product)

    async def list_products(self, merchant_id: int = None,
                            category: str = None,
                            status: str = None) -> list[dict]:
        return await self.repo.list_products(
            merchant_id=merchant_id, category=category, status=status)

    async def get_product(self, product_id: int) -> dict:
        product = await self.repo.get_product(product_id)
        if product is None:
            raise KeyError(f"商品不存在(productId={product_id})")
        return product

    async def offline_product(self, product_id: int,
                              reason: str = "") -> dict:
        """商户下架商品

        Raises:
            KeyError: 商品不存在 / ValueError: 状态非法
        """
        product = await self.repo.get_product(product_id)
        if product is None:
            raise KeyError(f"商品不存在(productId={product_id})")
        if product["status"] != PRODUCT_STATUS_ACTIVE:
            raise ValueError(
                f"商品状态非法(当前{product['status']}, "
                f"须为{PRODUCT_STATUS_ACTIVE})")
        product.update({"status": PRODUCT_STATUS_OFFLINE,
                        "offlineReason": reason,
                        "updatedAt": _now_iso()})
        return await self.repo.save_product(product)

    # ============================================================
    # 3. 交易与 15% 分润
    # ============================================================

    async def preview_shares(self, order_amount: float) -> dict:
        """分润拆账预览(成交价 → 15% 抽佣五方明细)"""
        if order_amount <= 0:
            raise ValueError("订单金额必须大于 0")
        settings = await self.repo.get_share_settings()
        commission = round(order_amount * settings["commissionRate"], 2)
        shares = {}
        for role, rate in settings["shareRates"].items():
            shares[role] = round(commission * rate, 2)
        # 舍入残差归平台(对账平账)
        diff = round(commission - sum(shares.values()), 2)
        if diff:
            shares["platform"] = round(shares["platform"] + diff, 2)
        return {
            "orderAmount": round(order_amount, 2),
            "commissionRate": settings["commissionRate"],
            "commission": commission,
            "merchantProceeds": round(order_amount - commission, 2),
            "shares": shares,
        }

    async def place_order(self, product_id: int, buyer_id: int,
                          quantity: int = 1) -> dict:
        """同盟商品下单(锁内原子扣库存; 支付回调后 settle)

        P0 简化: 下单即支付成功口径(与 checkout 服务的真实支付链
        对接留 P1); 分润在 settle_order 触发。

        Raises:
            KeyError: 商品不存在
            ValueError: 商品非在售/库存不足/数量非法
        """
        if quantity <= 0:
            raise ValueError("购买数量必须大于 0")
        async with get_lock(f"alliance:product:{product_id}"):
            product = await self.repo.get_product(product_id)
            if product is None:
                raise KeyError(f"商品不存在(productId={product_id})")
            if product["status"] != PRODUCT_STATUS_ACTIVE:
                raise ValueError(
                    f"商品非在售(当前{product['status']})")
            if product["stock"] < quantity:
                raise ValueError(
                    f"库存不足(剩余{product['stock']}, 需要{quantity})")
            product["stock"] -= quantity
            product["updatedAt"] = _now_iso()
            await self.repo.save_product(product)

            order_id = (f"ALO{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
                        f"{product_id:04d}{buyer_id % 100:02d}")
            amount = round(product["price"] * quantity, 2)
            order = {
                "orderId": order_id,
                "productId": product_id,
                "merchantId": product["merchantId"],
                "buyerId": buyer_id,
                "quantity": quantity,
                "amount": amount,
                "status": "paid",
                "settled": False,
                "createdAt": _now_iso(),
            }
            return await self.repo.save_order(order)

    async def settle_order(self, order_id: str) -> dict:
        """订单结算: 15% 抽佣五方拆账 + 总账双写 + 商户货款入账

        幂等: 已结算订单重复调用返回既有结算单。
        T+1 口径由调度器保证(仅结算 paidAt 距今 ≥ SETTLE_DELAY_HOURS
        的订单); 手动触发不校验延迟(运维通道)。

        Raises:
            KeyError: 订单不存在
            ValueError: 订单状态非法
        """
        async with get_lock(f"alliance:settle:{order_id}"):
            order = await self.repo.get_order(order_id)
            if order is None:
                raise KeyError(f"订单不存在(orderId={order_id})")
            if order.get("settled"):
                existing = await self.repo.find_settlement_by_order(order_id)
                return existing
            if order["status"] not in ("paid", "completed"):
                raise ValueError(
                    f"订单状态不可结算(当前{order['status']})")

            preview = await self.preview_shares(order["amount"])
            merchant = await self.repo.get_merchant(order["merchantId"]) or {}
            settlement_id = await self.repo.next_id("settlement")
            ledger_entries = []
            # 五方总账记账(role profit_ledger, venue 已验证旁路范式,
            # best-effort 失败不阻断主流程)
            try:
                from services.role_service import RoleService
                from repositories.role_repository import ROLE_PLATFORM
                role_service = RoleService()
                role_map = {"platform": ROLE_PLATFORM}
                # 记账主体: referrer→推荐人; 其余角色记 0(资金池归属
                # 由 role 侧口径统一定义, venue 范式)
                share_settings = await self.repo.get_share_settings()
                referrer_id = int(merchant.get("referrerMemberId") or 0)
                for share_role, amount in preview["shares"].items():
                    user_id = referrer_id if share_role == "referrer" else 0
                    ledger_no = f"ALS-{settlement_id}-{share_role}"
                    entry = await role_service.record_external_settlement(
                        ledger_no=ledger_no,
                        source_module="alliance",
                        role_code=role_map.get(share_role, share_role),
                        user_id=user_id,
                        basis=f"同盟订单 {order_id} 分润({share_role})",
                        base=order["amount"],
                        rate=preview["commissionRate"]
                        * share_settings["shareRates"][share_role],
                        amount=amount, ref_no=order_id,
                        note="37号同盟分润")
                    ledger_entries.append({
                        "role": share_role,
                        "ledgerNo": ledger_no,
                        "created": entry["created"],
                    })
            except Exception as exc:
                logger.warning("alliance_ledger_record_failed: %s", exc)

            # 商户货款入账(wallet deposit_reward 奖励余额口径;
            # P0 货款记账通道: 未开户则记录待重试, 提现体系走 wallet 既有链)
            proceeds = preview["merchantProceeds"]
            wallet_tx = ""
            try:
                from services.wallet_service import WalletService
                member_id = merchant.get("memberId", 0)
                try:
                    result = await WalletService().deposit_reward(
                        member_id, proceeds,
                        description=f"同盟货款 {order_id}")
                    wallet_tx = result.get("txNo", "")
                except KeyError:
                    wallet_tx = "PENDING_NO_WALLET"
            except Exception as exc:
                logger.warning("alliance_proceeds_deposit_failed: %s", exc)
                wallet_tx = "FAILED"

            settlement = {
                "settlementId": settlement_id,
                "orderId": order_id,
                "merchantId": order["merchantId"],
                "orderAmount": order["amount"],
                "commission": preview["commission"],
                "merchantProceeds": proceeds,
                "shares": preview["shares"],
                "ledgerEntries": ledger_entries,
                "walletTxNo": wallet_tx,
                "status": SETTLEMENT_STATUS_SETTLED,
                "settledAt": _now_iso(),
            }
            await self.repo.save_settlement(settlement)
            order["settled"] = True
            order["settledAt"] = _now_iso()
            await self.repo.save_order(order)
            logger.info("alliance_settled order=%s commission=%.2f "
                        "proceeds=%.2f", order_id, preview["commission"],
                        proceeds)
            return settlement

    async def reverse_settlement(self, order_id: str,
                                 reason: str = "") -> dict:
        """结算冲正(退款场景: 结算单标记 reversed, 分润负向调整留 P1)

        Raises:
            KeyError: 订单/结算单不存在
            ValueError: 未结算/已冲正
        """
        settlement = await self.repo.find_settlement_by_order(order_id)
        if settlement is None:
            raise KeyError(f"结算单不存在(orderId={order_id})")
        if settlement["status"] != SETTLEMENT_STATUS_SETTLED:
            raise ValueError(
                f"结算单状态不可冲正(当前{settlement['status']})")
        settlement.update({"status": SETTLEMENT_STATUS_REVERSED,
                           "reverseReason": reason,
                           "reversedAt": _now_iso()})
        return await self.repo.save_settlement(settlement)

    async def run_scheduled_settlement(self) -> dict:
        """T+1 定时结算: 扫描已支付未结算且过延迟窗口的订单"""
        threshold = (datetime.now(UTC)
                     - timedelta(hours=SETTLE_DELAY_HOURS)).isoformat()
        orders = await self.repo.list_orders(status="paid", limit=1000)
        settled, skipped = [], 0
        for order in orders:
            if order.get("settled") or order.get("createdAt", "") > threshold:
                skipped += 1
                continue
            try:
                result = await self.settle_order(order["orderId"])
                if result.get("status") == SETTLEMENT_STATUS_SETTLED:
                    settled.append(order["orderId"])
            except (KeyError, ValueError) as exc:
                logger.warning("alliance_scheduled_skip order=%s: %s",
                               order["orderId"], exc)
        if settled:
            logger.info("alliance_scheduled_settled count=%s", len(settled))
        return {"settled": settled, "skipped": skipped}

    async def get_settlement(self, order_id: str) -> dict:
        settlement = await self.repo.find_settlement_by_order(order_id)
        if settlement is None:
            raise KeyError(f"结算单不存在(orderId={order_id})")
        return settlement

    async def list_settlements(self, status: str = None) -> list[dict]:
        return await self.repo.list_settlements(status=status)

    async def get_share_settings(self) -> dict:
        return await self.repo.get_share_settings()

    async def update_share_settings(self, commission_rate: float = None,
                                    share_rates: dict = None) -> dict:
        """更新分润配置(抽佣率/五方比例; 比例合计须=1)

        Raises:
            ValueError: 参数非法/比例和≠1
        """
        settings = await self.repo.get_share_settings()
        if commission_rate is not None:
            if not (0 < commission_rate <= 0.5):
                raise ValueError("抽佣率须在 (0, 0.5] 区间")
            settings["commissionRate"] = commission_rate
        if share_rates is not None:
            if set(share_rates) != set(DEFAULT_SHARE_RATES):
                raise ValueError(
                    f"分润角色集合不匹配(须为{'/'.join(DEFAULT_SHARE_RATES)})")
            total = sum(share_rates.values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"分润比例合计须为 1.0(实际{total:.4f})")
            if any(r < 0 for r in share_rates.values()):
                raise ValueError("分润比例不能为负")
            settings["shareRates"] = dict(share_rates)
        settings["updatedAt"] = _now_iso()
        return await self.repo.save_share_settings(settings)

    async def list_orders(self, merchant_id: int = None,
                          status: str = None) -> list[dict]:
        return await self.repo.list_orders(merchant_id=merchant_id,
                                           status=status)

    # ============================================================
    # 4. 评价信用(P0 基础版: 提交/查询/星级聚合/折叠)
    # ============================================================

    async def submit_review(self, order_id: str, reviewer_id: int,
                            score: int, content: str = "") -> dict:
        """消费者评价商户(订单完成/结算后; 一单一评)

        Raises:
            KeyError: 订单不存在
            ValueError: 订单未结算/评分越界/重复评价
        """
        if not (REVIEW_MIN_SCORE <= score <= REVIEW_MAX_SCORE):
            raise ValueError(
                f"评分须为{REVIEW_MIN_SCORE}-{REVIEW_MAX_SCORE}星")
        order = await self.repo.get_order(order_id)
        if order is None:
            raise KeyError(f"订单不存在(orderId={order_id})")
        if not order.get("settled"):
            raise ValueError("订单结算后方可评价")
        if reviewer_id != order.get("buyerId"):
            raise ValueError("仅订单购买者可评价")
        existing = await self.repo.list_reviews(order_id=order_id)
        if existing:
            raise ValueError("该订单已评价(一单一评)")

        review_id = await self.repo.next_id("review")
        review = {
            "reviewId": review_id,
            "orderId": order_id,
            "merchantId": order["merchantId"],
            "reviewerId": reviewer_id,
            "score": int(score),
            "content": (content or "").strip()[:500],
            "folded": False,
            "createdAt": _now_iso(),
        }
        await self.repo.save_review(review)
        # 星级聚合回写商户
        await self._refresh_merchant_rating(order["merchantId"])
        return review

    async def _refresh_merchant_rating(self, merchant_id: int) -> None:
        """重算商户星级聚合(未折叠评价; 全折叠时归零)"""
        merchant = await self.repo.get_merchant(merchant_id)
        if merchant is None:
            return
        reviews = await self.repo.list_reviews(
            merchant_id=merchant_id, folded=False, limit=10000)
        if reviews:
            avg = round(sum(r["score"] for r in reviews) / len(reviews), 2)
            merchant.update({"ratingAvg": avg, "ratingCount": len(reviews),
                             "updatedAt": _now_iso()})
        else:
            merchant.update({"ratingAvg": 0.0, "ratingCount": 0,
                             "updatedAt": _now_iso()})
        await self.repo.save_merchant(merchant)

    async def fold_review(self, review_id: int, reason: str = "") -> dict:
        """折叠违规评价(P0 人工折叠; P1 接 alliance_review AI 评分器)

        Raises:
            KeyError: 评价不存在 / ValueError: 已折叠
        """
        review = await self.repo.get_review(review_id)
        if review is None:
            raise KeyError(f"评价不存在(reviewId={review_id})")
        if review.get("folded"):
            raise ValueError("评价已折叠")
        review.update({"folded": True, "foldReason": reason,
                       "foldedAt": _now_iso()})
        await self.repo.save_review(review)
        await self._refresh_merchant_rating(review["merchantId"])
        return review

    async def list_reviews(self, merchant_id: int = None,
                           folded: bool = None) -> list[dict]:
        return await self.repo.list_reviews(
            merchant_id=merchant_id, folded=folded)

    async def get_merchant_rating(self, merchant_id: int) -> dict:
        """商户星级概览(星级分布 + 均分)"""
        merchant = await self.get_merchant(merchant_id)
        reviews = await self.repo.list_reviews(
            merchant_id=merchant_id, folded=False, limit=10000)
        distribution = {str(s): 0 for s in
                        range(REVIEW_MIN_SCORE, REVIEW_MAX_SCORE + 1)}
        for review in reviews:
            distribution[str(review["score"])] += 1
        return {
            "merchantId": merchant_id,
            "shopName": merchant.get("shopName", ""),
            "ratingAvg": merchant.get("ratingAvg", 0.0),
            "ratingCount": merchant.get("ratingCount", 0),
            "distribution": distribution,
        }

    # ============================================================
    # 5. 报表
    # ============================================================

    async def report_overview(self) -> dict:
        """全景: 商户/商品/订单/分润汇总"""
        merchants = await self.repo.list_merchants(limit=10000)
        products = await self.repo.list_products(limit=10000)
        orders = await self.repo.list_orders(limit=10000)
        settlements = await self.repo.list_settlements(limit=10000)
        settled = [s for s in settlements
                   if s.get("status") == SETTLEMENT_STATUS_SETTLED]
        return {
            "merchants": {
                "total": len(merchants),
                "active": sum(1 for m in merchants
                              if m.get("status") == STATUS_ACTIVE),
                "probation": sum(1 for m in merchants
                                 if m.get("status") == STATUS_PROBATION),
                "suspended": sum(1 for m in merchants
                                 if m.get("status") == STATUS_SUSPENDED),
                "terminated": sum(1 for m in merchants
                                  if m.get("status") == STATUS_TERMINATED),
            },
            "products": {
                "total": len(products),
                "active": sum(1 for p in products
                              if p.get("status") == PRODUCT_STATUS_ACTIVE),
            },
            "orders": {
                "total": len(orders),
                "paid": sum(1 for o in orders
                            if o.get("status") == "paid"),
                "settled": sum(1 for o in orders if o.get("settled")),
                "gmv": round(sum(o.get("amount", 0) for o in orders), 2),
            },
            "settlements": {
                "total": len(settlements),
                "settled": len(settled),
                "commissionTotal": round(
                    sum(s.get("commission", 0) for s in settled), 2),
                "proceedsTotal": round(
                    sum(s.get("merchantProceeds", 0) for s in settled), 2),
            },
        }

    async def report_category(self) -> list[dict]:
        """类目维度报表(商户/商品/GMV/佣金)"""
        merchants = await self.repo.list_merchants(limit=10000)
        orders = await self.repo.list_orders(limit=10000)
        settlements = await self.repo.list_settlements(limit=10000)
        rows = []
        for category in CATEGORIES:
            cat_merchants = [m for m in merchants
                             if m.get("category") == category]
            cat_products = await self.repo.list_products(
                category=category, limit=10000)
            merchant_ids = {m["merchantId"] for m in cat_merchants}
            cat_orders = [o for o in orders
                          if o.get("merchantId") in merchant_ids]
            cat_settled = [s for s in settlements
                           if s.get("merchantId") in merchant_ids
                           and s.get("status") == SETTLEMENT_STATUS_SETTLED]
            rows.append({
                "category": category,
                "categoryName": CATEGORY_SEEDS[category]["name"],
                "merchants": len(cat_merchants),
                "products": len(cat_products),
                "orders": len(cat_orders),
                "gmv": round(sum(o.get("amount", 0) for o in cat_orders), 2),
                "commission": round(
                    sum(s.get("commission", 0) for s in cat_settled), 2),
            })
        return rows
