"""老酒兑换及回收模块业务逻辑层

核心业务:
    - 老酒估价(AI智能估值: 增值率+品质分级+市场比价)
    - 回收申请(用户提交兑换/回收申请)
    - 审核(管理员审核申请)
    - 兑换新酒(老酒价值抵扣新酒款+差价处理)
    - 折现回收(老酒价值×80%+个税扣除)
    - 状态流转(申请→估价→审核→回收→兑换)
    - 库存管理(回收老酒入库)

锁保护:
    - 估价: lock:recycle:valuate:{app_id}  (估价幂等)
    - 审核: lock:recycle:review:{app_id}   (审核状态流转)
    - 兑换: lock:recycle:exchange:{app_id} (兑换原子操作)
    - 回收: lock:recycle:recycle:{app_id}  (回收原子操作)
    - 库存: lock:recycle:inventory:{product_id} (库存原子更新)

异常约定:
    - KeyError → 404(申请/估价/兑换记录不存在)
    - ValueError → 409(业务冲突: 重复申请/状态非法/超限额等)
"""

from datetime import datetime, date, timedelta
from typing import Optional

from core.locks import get_lock
from core.helpers import ts, bc_hash
from repositories.recycle_repository import (
    RecycleRepository,
    # 状态常量
    STATUS_PENDING, STATUS_VALUING, STATUS_VALUED, STATUS_REVIEWING,
    STATUS_APPROVED, STATUS_REJECTED, STATUS_RECYCLING, STATUS_EXCHANGING,
    STATUS_COMPLETED, STATUS_CANCELLED,
    # 业务类型
    TYPE_EXCHANGE, TYPE_RECYCLE,
    # 品质分级
    GRADE_A, GRADE_B, GRADE_C, GRADE_D, GRADE_COEFFICIENTS,
    # 状态流转图
    STATUS_TRANSITIONS,
)


# ============================================================
# 增值率计算规则常量
# ============================================================

# 兑换门槛: 酒龄 ≥ 3 年
MIN_WINE_AGE_YEARS = 3
# 基础增值率: 3年时 15%
BASE_APPRECIATION_RATE = 0.15
# 逐年递增: 3年后每年 +5%
YEARLY_RATE_INCREMENT = 0.05
# 增值封顶: 100%
MAX_APPRECIATION_RATE = 1.00
# 封顶酒龄: 20年(15% + 5%×17 = 100%)
MAX_WINE_AGE_YEARS = 20
# 折现比例: 老酒价值 × 80%
CASH_RATE = 0.80
# 个税起征点: ¥800
TAX_THRESHOLD = 800.0
# 个税率: 超¥800部分 20%
TAX_RATE = 0.20
# 年度兑换限制: ≤10瓶/年
ANNUAL_EXCHANGE_LIMIT = 10
# 年度折现限制: ≤¥20000/年
ANNUAL_RECYCLE_LIMIT = 20000.0
# 单次兑换老酒上限: ≤5瓶
SINGLE_EXCHANGE_MAX_BOTTLES = 5
# 单次回收老酒上限: ≤3瓶
SINGLE_RECYCLE_MAX_BOTTLES = 3
# 单笔折现上限: ≤¥5000
SINGLE_CASH_LIMIT = 5000.0
# 最低折现金额: ≥¥50
MIN_CASH_AMOUNT = 50.0
# 差额转积分: 1元 = 10竹叶
POINTS_PER_YUAN = 10
# 兑换奖励积分
EXCHANGE_REWARD_POINTS = 50
# 回收奖励积分
RECYCLE_REWARD_POINTS = 30


class RecycleService:
    """老酒兑换回收业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: RecycleRepository = RecycleRepository()):
        self.repo = repo

    # ============================================================
    # 1. 增值率计算(AI智能估值核心)
    # ============================================================

    def calculate_appreciation_rate(self, wine_age_years: int,
                                     member_level: int = 1,
                                     for_exchange: bool = True) -> float:
        """计算老酒增值率

        规则:
            - 酒龄 < 3 年不可参与(返回0)
            - 基础增值率: 15%(3年)
            - 逐年递增: 3年后每年 +5%
            - 封顶: 100%(20年)
            - 会员等级加成(仅兑换): L3+2% / L4+5% / L5+8%

        Returns:
            增值率(0.0~1.0)
        """
        if wine_age_years < MIN_WINE_AGE_YEARS:
            return 0.0

        # 基础增值率
        rate = BASE_APPRECIATION_RATE + YEARLY_RATE_INCREMENT * (wine_age_years - MIN_WINE_AGE_YEARS)
        rate = min(rate, MAX_APPRECIATION_RATE)

        # 会员等级加成(仅兑换)
        if for_exchange:
            level_bonus = self._get_level_bonus(member_level)
            rate += level_bonus

        return rate

    def _get_level_bonus(self, member_level: int) -> float:
        """会员等级加成(仅兑换新酒享受)"""
        bonuses = {1: 0.0, 2: 0.0, 3: 0.02, 4: 0.05, 5: 0.08}
        return bonuses.get(member_level, 0.0)

    def calculate_old_wine_value(self, purchase_price: float, wine_age_years: int,
                                  condition_grade: str = GRADE_A,
                                  member_level: int = 1,
                                  for_exchange: bool = True) -> dict:
        """计算老酒价值

        规则:
            - 老酒价值 = 购买原价 × (1 + 增值率) × 品质系数
            - 品质系数: A=1.0 / B=0.95 / C=0.90 / D=0.85

        Returns:
            {wineAge, appreciationRate, baseValue, conditionCoefficient, oldValue, cashValue}
        """
        if wine_age_years < MIN_WINE_AGE_YEARS:
            return {
                "eligible": False,
                "message": f"酒龄未满{MIN_WINE_AGE_YEARS}年, 暂不可兑换/回收",
                "wineAge": wine_age_years,
                "appreciationRate": 0.0,
                "baseValue": purchase_price,
                "conditionGrade": condition_grade,
                "conditionCoefficient": 0.0,
                "oldValue": 0.0,
                "cashValue": 0.0,
            }

        appreciation_rate = self.calculate_appreciation_rate(
            wine_age_years, member_level, for_exchange
        )
        base_value = purchase_price * (1 + appreciation_rate)
        condition_coeff = GRADE_COEFFICIENTS.get(condition_grade, 1.0)
        old_value = round(base_value * condition_coeff, 2)
        cash_value = round(old_value * CASH_RATE, 2)

        return {
            "eligible": True,
            "wineAge": wine_age_years,
            "appreciationRate": round(appreciation_rate, 4),
            "baseValue": round(base_value, 2),
            "conditionGrade": condition_grade,
            "conditionCoefficient": condition_coeff,
            "oldValue": old_value,
            "cashValue": cash_value,
        }

    def calculate_wine_age(self, purchase_date: str, current_date: date = None) -> int:
        """计算酒龄(按年取整)"""
        if current_date is None:
            current_date = date.today()
        if isinstance(purchase_date, str):
            purchase_date = date.fromisoformat(purchase_date[:10])
        delta = current_date.year - purchase_date.year
        # 未满整年不算
        if (current_date.month, current_date.day) < (purchase_date.month, purchase_date.day):
            delta -= 1
        return max(0, delta)

    # ============================================================
    # 2. 老酒估价
    # ============================================================

    async def submit_valuation(self, user_id: int, product_id: str,
                                purchase_price: float, purchase_date: str,
                                condition_grade: str = GRADE_A,
                                member_level: int = 1,
                                for_exchange: bool = True) -> dict:
        """提交老酒估价

        规则:
            - 酒龄 < 3 年不可估价
            - 计算增值率+老酒价值+折现金额
            - 写入估价记录

        Returns:
            估价结果

        Raises:
            ValueError: 酒龄不足/参数无效
        """
        if purchase_price <= 0:
            raise ValueError("购买原价必须大于0")
        if condition_grade not in GRADE_COEFFICIENTS:
            raise ValueError(f"品质分级无效(须为A/B/C/D)")

        wine_age = self.calculate_wine_age(purchase_date)
        if wine_age < MIN_WINE_AGE_YEARS:
            raise ValueError(
                f"酒龄未满{MIN_WINE_AGE_YEARS}年(当前{wine_age}年), 暂不可估价"
            )

        valuation_result = self.calculate_old_wine_value(
            purchase_price, wine_age, condition_grade, member_level, for_exchange
        )

        lock_key = f"recycle:valuate:user:{user_id}"
        async with get_lock(lock_key):
            valuation = {
                "userId": user_id,
                "productId": product_id,
                "purchasePrice": purchase_price,
                "purchaseDate": purchase_date,
                "wineAge": wine_age,
                "conditionGrade": condition_grade,
                "memberLevel": member_level,
                "forExchange": 1 if for_exchange else 0,
                "appreciationRate": valuation_result["appreciationRate"],
                "baseValue": valuation_result["baseValue"],
                "conditionCoefficient": valuation_result["conditionCoefficient"],
                "oldValue": valuation_result["oldValue"],
                "cashValue": valuation_result["cashValue"],
                "blockHash": bc_hash(),
                "createdAt": ts(),
            }
            val_id = await self.repo.add_valuation(valuation)
            valuation["id"] = val_id
            return valuation

    async def get_valuation(self, val_id: int) -> dict:
        """查询估价"""
        valuation = await self.repo.get_valuation(val_id)
        if valuation is None:
            raise KeyError(f"估价记录不存在(valId={val_id})")
        return valuation

    async def list_valuations(self, user_id: int = None, limit: int = 50) -> list[dict]:
        """查询估价列表"""
        return await self.repo.list_valuations(user_id, limit)

    # ============================================================
    # 3. 回收申请
    # ============================================================

    async def submit_application(self, user_id: int, app_type: str,
                                  valuation_ids: list, new_product_id: str = None,
                                  new_product_price: float = None,
                                  payout_method: str = None,
                                  payout_account: str = None) -> dict:
        """提交回收申请(兑换新酒/折现回收)

        规则:
            - 兑换: 单次≤5瓶, 年度≤10瓶
            - 回收: 单次≤3瓶, 年度≤¥20000, 单笔≤¥5000, 最低≥¥50
            - 估价记录须存在且属于该用户

        Returns:
            申请结果

        Raises:
            ValueError: 超限额/估价不存在/类型无效
        """
        if app_type not in (TYPE_EXCHANGE, TYPE_RECYCLE):
            raise ValueError(f"业务类型无效(须为{TYPE_EXCHANGE}/{TYPE_RECYCLE})")

        if not valuation_ids:
            raise ValueError("估价记录不能为空")

        # 单次瓶数限制
        bottle_count = len(valuation_ids)
        if app_type == TYPE_EXCHANGE and bottle_count > SINGLE_EXCHANGE_MAX_BOTTLES:
            raise ValueError(f"单次兑换最多{SINGLE_EXCHANGE_MAX_BOTTLES}瓶")
        if app_type == TYPE_RECYCLE and bottle_count > SINGLE_RECYCLE_MAX_BOTTLES:
            raise ValueError(f"单次回收最多{SINGLE_RECYCLE_MAX_BOTTLES}瓶")

        lock_key = f"recycle:apply:user:{user_id}"
        async with get_lock(lock_key):
            # 校验估价记录
            valuations = []
            total_old_value = 0.0
            total_cash_value = 0.0
            for vid in valuation_ids:
                val = await self.repo.get_valuation(vid)
                if val is None:
                    raise KeyError(f"估价记录不存在(valId={vid})")
                if val.get("userId") != user_id:
                    raise ValueError(f"估价记录{vid}不属于当前用户")
                valuations.append(val)
                total_old_value += val.get("oldValue", 0)
                total_cash_value += val.get("cashValue", 0)

            # 年度限制校验
            year_str = str(date.today().year)
            user_apps = await self.repo.list_applications(user_id=user_id, limit=1000)
            if app_type == TYPE_EXCHANGE:
                year_count = sum(
                    1 for a in user_apps
                    if a.get("type") == TYPE_EXCHANGE
                    and a.get("createdAt", "").startswith(year_str)
                    and a.get("status") not in (STATUS_CANCELLED, STATUS_REJECTED)
                )
                if year_count + bottle_count > ANNUAL_EXCHANGE_LIMIT:
                    raise ValueError(
                        f"年度兑换超限(上限{ANNUAL_EXCHANGE_LIMIT}瓶/年)"
                    )
            else:  # 折现回收
                year_cash = sum(
                    a.get("cashAmount", 0) for a in user_apps
                    if a.get("type") == TYPE_RECYCLE
                    and a.get("createdAt", "").startswith(year_str)
                    and a.get("status") not in (STATUS_CANCELLED, STATUS_REJECTED)
                )
                if year_cash + total_cash_value > ANNUAL_RECYCLE_LIMIT:
                    raise ValueError(
                        f"年度折现超限(上限¥{ANNUAL_RECYCLE_LIMIT}/年)"
                    )
                if total_cash_value > SINGLE_CASH_LIMIT:
                    raise ValueError(
                        f"单笔折现超限(上限¥{SINGLE_CASH_LIMIT})"
                    )
                if total_cash_value < MIN_CASH_AMOUNT:
                    raise ValueError(
                        f"折现金额低于最低限额(≥¥{MIN_CASH_AMOUNT})"
                    )

            # 兑换新酒需指定新酒
            if app_type == TYPE_EXCHANGE:
                if not new_product_id or not new_product_price:
                    raise ValueError("兑换新酒须指定新酒产品及价格")

            application = {
                "userId": user_id,
                "type": app_type,
                "valuationIds": valuation_ids,
                "oldWineCount": bottle_count,
                "oldWineTotalValue": round(total_old_value, 2),
                "cashValue": round(total_cash_value, 2),
                "newProductId": new_product_id,
                "newProductPrice": new_product_price,
                "payoutMethod": payout_method,
                "payoutAccount": payout_account,
                "status": STATUS_PENDING,
                "createdAt": ts(),
                "updatedAt": ts(),
            }
            app_id = await self.repo.create_application(application)
            application["id"] = app_id
            return application

    async def review_application(self, app_id: int, approved: bool,
                                  reviewer: str = "admin",
                                  remark: str = "") -> dict:
        """审核回收申请

        规则:
            - 仅估价后(STATUS_VALUED)的申请可审核
            - 通过 → STATUS_APPROVED
            - 拒绝 → STATUS_REJECTED

        Raises:
            KeyError: 申请不存在
            ValueError: 状态非法
        """
        lock_key = f"recycle:review:{app_id}"
        async with get_lock(lock_key):
            app = await self.repo.get_application(app_id)
            if app is None:
                raise KeyError(f"回收申请不存在(appId={app_id})")

            if app["status"] != STATUS_VALUED and app["status"] != STATUS_PENDING:
                raise ValueError(
                    f"申请状态非法(当前{app['status']}, 须为{STATUS_VALUED})"
                )

            new_status = STATUS_APPROVED if approved else STATUS_REJECTED
            await self.repo.update_application(app_id, {
                "status": new_status,
                "reviewer": reviewer,
                "reviewRemark": remark,
                "reviewedAt": ts(),
                "updatedAt": ts(),
            })
            app["status"] = new_status
            return app

    async def get_application(self, app_id: int) -> dict:
        """查询回收申请"""
        app = await self.repo.get_application(app_id)
        if app is None:
            raise KeyError(f"回收申请不存在(appId={app_id})")
        return app

    async def list_applications(self, user_id: int = None, status: str = None,
                                 app_type: str = None, limit: int = 50) -> list[dict]:
        """查询回收申请列表"""
        return await self.repo.list_applications(user_id, status, app_type, limit)

    async def transition_status(self, app_id: int, new_status: str,
                                  operator: str = "admin") -> dict:
        """状态流转

        规则:
            - 校验状态流转图合法性
            - 更新申请状态

        Raises:
            KeyError: 申请不存在
            ValueError: 非法状态流转
        """
        lock_key = f"recycle:transition:{app_id}"
        async with get_lock(lock_key):
            app = await self.repo.get_application(app_id)
            if app is None:
                raise KeyError(f"回收申请不存在(appId={app_id})")

            current_status = app["status"]
            allowed = STATUS_TRANSITIONS.get(current_status, [])
            if new_status not in allowed:
                raise ValueError(
                    f"非法状态流转({current_status} → {new_status})"
                )

            await self.repo.update_application_status(app_id, new_status)
            app["status"] = new_status
            return app

    # ============================================================
    # 4. 兑换新酒
    # ============================================================

    async def exchange_new_wine(self, app_id: int, new_product_id: str,
                                new_product_price: float,
                                diff_payment_method: str = "wechat") -> dict:
        """兑换新酒

        规则:
            - 仅审核通过(STATUS_APPROVED)的兑换申请可执行
            - 老酒价值全额抵扣新酒款
            - 老酒价值 > 新酒价格: 差额转积分(1元=10竹叶)
            - 老酒价值 < 新酒价格: 需补差价

        Returns:
            兑换记录

        Raises:
            KeyError: 申请不存在
            ValueError: 状态非法/类型不匹配
        """
        lock_key = f"recycle:exchange:{app_id}"
        async with get_lock(lock_key):
            app = await self.repo.get_application(app_id)
            if app is None:
                raise KeyError(f"回收申请不存在(appId={app_id})")

            if app["type"] != TYPE_EXCHANGE:
                raise ValueError("非兑换申请不可执行兑换新酒")
            if app["status"] != STATUS_APPROVED:
                raise ValueError(f"申请状态非法(须为{STATUS_APPROVED})")

            old_value = app.get("oldWineTotalValue", 0)
            price_diff = round(new_product_price - old_value, 2)
            points_converted = 0
            diff_payment_amount = 0

            if price_diff < 0:
                # 差额转积分
                points_converted = int(abs(price_diff) * POINTS_PER_YUAN)
            elif price_diff > 0:
                diff_payment_amount = price_diff

            exchange = {
                "applicationId": app_id,
                "userId": app["userId"],
                "type": TYPE_EXCHANGE,
                "oldWineIds": app.get("valuationIds", []),
                "oldWineTotalValue": old_value,
                "newProductId": new_product_id,
                "newProductPrice": new_product_price,
                "priceDiff": price_diff,
                "diffPaymentMethod": diff_payment_method if price_diff > 0 else "",
                "diffPaymentAmount": diff_payment_amount,
                "pointsConverted": points_converted,
                "status": STATUS_EXCHANGING,
                "blockHash": bc_hash(),
                "createdAt": ts(),
            }
            ex_id = await self.repo.add_exchange(exchange)
            exchange["id"] = ex_id

            # 更新申请状态
            await self.repo.update_application(app_id, {
                "status": STATUS_EXCHANGING,
                "exchangeId": ex_id,
                "newProductId": new_product_id,
                "newProductPrice": new_product_price,
                "priceDiff": price_diff,
                "updatedAt": ts(),
            })

            return exchange

    # ============================================================
    # 5. 折现回收
    # ============================================================

    async def recycle_for_cash(self, app_id: int, payout_method: str,
                                payout_account: str) -> dict:
        """折现回收

        规则:
            - 仅审核通过(STATUS_APPROVED)的回收申请可执行
            - 折现金额 = 老酒价值 × 80%
            - 个税: 超¥800部分扣除20%
            - 实付 = 折现金额 - 个税

        Returns:
            兑换记录(回收类型)

        Raises:
            KeyError: 申请不存在
            ValueError: 状态非法/类型不匹配
        """
        lock_key = f"recycle:recycle:{app_id}"
        async with get_lock(lock_key):
            app = await self.repo.get_application(app_id)
            if app is None:
                raise KeyError(f"回收申请不存在(appId={app_id})")

            if app["type"] != TYPE_RECYCLE:
                raise ValueError("非回收申请不可执行折现回收")
            if app["status"] != STATUS_APPROVED:
                raise ValueError(f"申请状态非法(须为{STATUS_APPROVED})")

            cash_amount = app.get("cashValue", 0)
            # 个税计算: 超¥800部分 × 20%
            if cash_amount > TAX_THRESHOLD:
                tax_amount = round((cash_amount - TAX_THRESHOLD) * TAX_RATE, 2)
            else:
                tax_amount = 0.0
            actual_payout = round(cash_amount - tax_amount, 2)

            exchange = {
                "applicationId": app_id,
                "userId": app["userId"],
                "type": TYPE_RECYCLE,
                "oldWineIds": app.get("valuationIds", []),
                "oldWineTotalValue": app.get("oldWineTotalValue", 0),
                "cashRate": CASH_RATE,
                "cashAmount": cash_amount,
                "taxAmount": tax_amount,
                "actualPayout": actual_payout,
                "payoutMethod": payout_method,
                "payoutAccount": payout_account,
                "status": STATUS_RECYCLING,
                "blockHash": bc_hash(),
                "createdAt": ts(),
            }
            ex_id = await self.repo.add_exchange(exchange)
            exchange["id"] = ex_id

            # 更新申请状态
            await self.repo.update_application(app_id, {
                "status": STATUS_RECYCLING,
                "exchangeId": ex_id,
                "cashAmount": cash_amount,
                "taxAmount": tax_amount,
                "actualPayout": actual_payout,
                "payoutMethod": payout_method,
                "payoutAccount": payout_account,
                "updatedAt": ts(),
            })

            return exchange

    # ============================================================
    # 6. 完成回收/兑换
    # ============================================================

    async def complete_exchange(self, ex_id: int, operator: str = "admin") -> dict:
        """完成兑换/回收(老酒入库+状态完成)

        规则:
            - 仅兑换中(STATUS_EXCHANGING/STATUS_RECYCLING)的记录可完成
            - 老酒入库(库存+1)
            - 状态置为已完成

        Raises:
            KeyError: 兑换记录不存在
            ValueError: 状态非法
        """
        lock_key = f"recycle:complete:{ex_id}"
        async with get_lock(lock_key):
            exchange = await self.repo.get_exchange(ex_id)
            if exchange is None:
                raise KeyError(f"兑换记录不存在(exId={ex_id})")

            if exchange["status"] not in (STATUS_EXCHANGING, STATUS_RECYCLING):
                raise ValueError(f"兑换记录状态非法(当前{exchange['status']})")

            # 老酒入库
            for vid in exchange.get("oldWineIds", []):
                val = await self.repo.get_valuation(vid)
                if val:
                    product_id = val.get("productId", "unknown")
                    await self.repo.update_inventory(product_id, 1)

            # 更新兑换记录状态
            await self.repo.update_exchange(ex_id, {
                "status": STATUS_COMPLETED,
                "completedAt": ts(),
            })

            # 更新申请状态
            app_id = exchange.get("applicationId")
            await self.repo.update_application(app_id, {
                "status": STATUS_COMPLETED,
                "completedAt": ts(),
                "updatedAt": ts(),
            })

            return {
                "exchangeId": ex_id,
                "status": STATUS_COMPLETED,
                "completedAt": ts(),
                "operator": operator,
            }

    # ============================================================
    # 7. 查询统计
    # ============================================================

    async def get_exchange(self, ex_id: int) -> dict:
        """查询兑换记录"""
        exchange = await self.repo.get_exchange(ex_id)
        if exchange is None:
            raise KeyError(f"兑换记录不存在(exId={ex_id})")
        return exchange

    async def list_exchanges(self, user_id: int = None, ex_type: str = None,
                              limit: int = 50) -> list[dict]:
        """查询兑换记录列表"""
        return await self.repo.list_exchanges(user_id, ex_type, limit)

    async def get_inventory(self, product_id: str = None) -> dict:
        """查询回收库存"""
        return await self.repo.get_inventory(product_id)

    async def get_stats(self, user_id: int = None) -> dict:
        """回收统计"""
        apps = await self.repo.list_applications(user_id=user_id, limit=10000)
        exchanges = await self.repo.list_exchanges(user_id=user_id, limit=10000)

        # 按状态统计申请
        status_count = {}
        type_count = {TYPE_EXCHANGE: 0, TYPE_RECYCLE: 0}
        for app in apps:
            s = app.get("status", "unknown")
            status_count[s] = status_count.get(s, 0) + 1
            t = app.get("type", "unknown")
            type_count[t] = type_count.get(t, 0) + 1

        # 统计兑换/回收金额
        total_exchange_value = sum(
            e.get("oldWineTotalValue", 0) for e in exchanges
            if e.get("type") == TYPE_EXCHANGE
        )
        total_recycle_cash = sum(
            e.get("actualPayout", 0) for e in exchanges
            if e.get("type") == TYPE_RECYCLE
        )

        return {
            "userId": user_id,
            "totalApplications": len(apps),
            "totalExchanges": len(exchanges),
            "statusCount": status_count,
            "typeCount": type_count,
            "totalExchangeValue": round(total_exchange_value, 2),
            "totalRecycleCash": round(total_recycle_cash, 2),
        }
