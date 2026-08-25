"""会员积分管理模块业务逻辑层

核心业务:
    - 每日签到(连续签到+宝箱奖励+幂等防重)
    - 消费返分(订单返分+等级加成+每日/每月上限)
    - 积分抵现(FIFO过期消耗+30%订单上限+冻结/解冻)
    - 退款返还(扣回已发放积分)
    - 积分过期(24个月滚动过期扫描)
    - 查询统计(账户/流水/将过期/统计)

锁保护:
    - 签到: lock:points:signin:{user_id}:{date}  (幂等防重)
    - 返分: lock:points:account:{user_id}  (账户余额原子更新)
    - 抵扣: lock:points:account:{user_id}  (FIFO消耗+余额更新)
    - 退款: lock:points:account:{user_id}  (扣回积分)
    - 过期: lock:points:expire:run  (全局过期扫描串行)

异常约定:
    - KeyError → 404(账户不存在)
    - ValueError → 409(业务冲突: 重复签到/积分不足/超上限等)
"""

from datetime import datetime, date, timedelta

from core.locks import get_lock
from repositories.points_repository import (
    PointsRepository,
    # 流水类型
    LOG_TYPE_EARN, LOG_TYPE_SPEND, SOURCE_CHECKIN, SOURCE_ORDER, SOURCE_REFUND, SOURCE_EXPIRE, SOURCE_DEDUCT,
    # 流水状态
    LOG_STATUS_AVAILABLE, LOG_STATUS_EXPIRED, LOG_STATUS_CONSUMED,
    # 过期批次状态
    EXPIRE_STATUS_ACTIVE, EXPIRE_STATUS_EXPIRED, EXPIRE_STATUS_CONSUMED,
)


# ============================================================
# 积分规则常量
# ============================================================

# 积分价值: 100竹叶 = ¥1
POINTS_PER_YUAN = 100
# 抵扣上限: 单笔订单最多抵扣商品金额的30%
MAX_DEDUCT_RATIO = 0.30
# 积分有效期: 24个月
EXPIRE_MONTHS = 24
# 签到基础积分
SIGNIN_BASE_POINTS = 10
# 连续签到每日递增
SIGNIN_DAILY_BONUS = 5
# 连续签到上限天数(超过不再递增)
SIGNIN_BONUS_CAP = 7
# 签到宝箱奖励(第7/14/21/28天)
SIGNIN_BONUS_DAYS = {7, 14, 21, 28}
SIGNIN_BONUS_POINTS = 50
# 消费返分比例: 每消费¥1返1.5竹叶
EARN_RATE_PER_YUAN = 1.5
# 等级加成倍数
LEVEL_MULTIPLIERS = {
    1: 1.0,   # 普通会员
    2: 1.2,   # 银卡
    3: 1.5,   # 金卡
    4: 2.0,   # 白金
    5: 3.0,   # SVIP
}
# 每日获取上限
DAILY_EARN_LIMIT = 500
# 每月获取上限
MONTHLY_EARN_LIMIT = 5000
# 最低抵扣单位
MIN_DEDUCT_POINTS = 100


class PointsService:
    """会员积分业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: PointsRepository = PointsRepository()):
        self.repo = repo

    # ============================================================
    # 1. 每日签到
    # ============================================================

    async def signin(self, user_id: int) -> dict:
        """每日签到(含连续签到+宝箱奖励+幂等防重)

        规则:
            - 基础积分: 10 竹叶
            - 连续递增: 第N天额外 +5×min(N-1, 6) 竹叶
            - 宝箱奖励: 第7/14/21/28天额外 +50 竹叶

        Returns:
            签到结果(含获得积分/连续天数/是否宝箱)

        Raises:
            ValueError: 今日已签到(幂等)
        """
        today = date.today()
        date_str = today.isoformat()
        lock_key = f"points:signin:{user_id}:{date_str}"

        async with get_lock(lock_key):
            # 幂等校验: 今日是否已签到
            existing = await self.repo.get_signin(user_id, today)
            if existing:
                raise ValueError(f"今日已签到(date={date_str})")

            # 计算连续天数
            yesterday = today - timedelta(days=1)
            last_signin = await self.repo.get_signin(user_id, yesterday)
            continuous_days = last_signin.get("continuousDays", 0) + 1 if last_signin else 1

            # 计算获得积分
            bonus_days = min(continuous_days - 1, SIGNIN_BONUS_CAP - 1)
            points_earned = SIGNIN_BASE_POINTS + SIGNIN_DAILY_BONUS * bonus_days
            is_bonus = continuous_days in SIGNIN_BONUS_DAYS
            if is_bonus:
                points_earned += SIGNIN_BONUS_POINTS

            # 写入签到记录
            signin_record = {
                "userId": user_id,
                "signDate": date_str,
                "continuousDays": continuous_days,
                "pointsEarned": points_earned,
                "isBonus": 1 if is_bonus else 0,
            }
            signin_id = await self.repo.add_signin(signin_record)

            # 发放积分
            await self._earn_points(
                user_id=user_id,
                points=points_earned,
                source=SOURCE_CHECKIN,
                ref_id=date_str,
                ref_desc=f"每日签到(连续第{continuous_days}天)",
            )

            return {
                "signinId": signin_id,
                "userId": user_id,
                "signDate": date_str,
                "continuousDays": continuous_days,
                "pointsEarned": points_earned,
                "isBonus": is_bonus,
                "bonusPoints": SIGNIN_BONUS_POINTS if is_bonus else 0,
            }

    async def get_signin_records(self, user_id: int, limit: int = 30) -> list[dict]:
        """查询签到记录"""
        return await self.repo.list_signins(user_id, limit)

    # ============================================================
    # 2. 消费返分
    # ============================================================

    async def earn_order_points(self, user_id: int, order_id: str,
                                 order_amount: float, member_level: int = 1) -> dict:
        """订单完成消费返分

        规则:
            - 基础返分: order_amount × 1.5
            - 等级加成: × 等级倍数
            - 每日上限: 500 竹叶
            - 每月上限: 5000 竹叶

        Returns:
            返分结果(含获得积分/加成倍数)

        Raises:
            ValueError: 超过每日/每月上限
        """
        lock_key = f"points:account:{user_id}"

        async with get_lock(lock_key):
            # 等级加成倍数
            multiplier = LEVEL_MULTIPLIERS.get(member_level, 1.0)

            # 计算基础积分
            base_points = int(order_amount * EARN_RATE_PER_YUAN)
            earned_points = int(base_points * multiplier)

            if earned_points <= 0:
                raise ValueError("订单金额不足, 无法获得积分")

            # 检查每日/每月上限
            today = date.today()
            today_str = today.isoformat()
            month_str = today.strftime("%Y-%m")

            today_logs = await self.repo.list_logs(user_id, source=SOURCE_ORDER, limit=100)
            today_earned = sum(l["points"] for l in today_logs
                               if l.get("createdAt", "").startswith(today_str)
                               and l["points"] > 0)
            if today_earned + earned_points > DAILY_EARN_LIMIT:
                raise ValueError(f"今日消费返分已达上限({DAILY_EARN_LIMIT}竹叶)")

            month_earned = sum(l["points"] for l in today_logs
                               if l.get("createdAt", "").startswith(month_str)
                               and l["points"] > 0)
            if month_earned + earned_points > MONTHLY_EARN_LIMIT:
                raise ValueError(f"本月消费返分已达上限({MONTHLY_EARN_LIMIT}竹叶)")

            # 发放积分
            log_id = await self._earn_points(
                user_id=user_id,
                points=earned_points,
                source=SOURCE_ORDER,
                ref_id=order_id,
                ref_desc=f"订单消费返分(¥{order_amount:.2f} × {EARN_RATE_PER_YUAN} × {multiplier})",
            )

            return {
                "logId": log_id,
                "userId": user_id,
                "orderId": order_id,
                "orderAmount": order_amount,
                "basePoints": base_points,
                "multiplier": multiplier,
                "earnedPoints": earned_points,
            }

    # ============================================================
    # 3. 积分抵现
    # ============================================================

    async def deduct_points(self, user_id: int, order_id: str,
                             order_amount: float, deduct_points: int) -> dict:
        """积分抵现(FIFO过期消耗+30%订单上限)

        规则:
            - 最低抵扣: 100 竹叶
            - 上限: 订单金额 × 30% × 100(换算为竹叶)
            - FIFO消耗: 优先消耗即将过期的积分批次
            - 抵扣金额: deduct_points / 100 元

        Returns:
            抵扣结果(含实际消耗积分/抵扣金额/批次明细)

        Raises:
            ValueError: 积分不足/低于最低抵扣/超过上限
            KeyError: 账户不存在
        """
        if deduct_points < MIN_DEDUCT_POINTS:
            raise ValueError(f"最低抵扣{MIN_DEDUCT_POINTS}竹叶")
        if deduct_points % MIN_DEDUCT_POINTS != 0:
            raise ValueError(f"抵扣积分须为{MIN_DEDUCT_POINTS}的整数倍")

        # 计算上限: 订单金额 × 30% × 100
        max_deduct_points = int(order_amount * MAX_DEDUCT_RATIO * POINTS_PER_YUAN)
        if deduct_points > max_deduct_points:
            raise ValueError(
                f"抵扣超过上限(最多{max_deduct_points}竹叶 = ¥{order_amount * MAX_DEDUCT_RATIO:.2f})"
            )

        lock_key = f"points:account:{user_id}"

        async with get_lock(lock_key):
            account = await self.repo.get_account(user_id)
            if account is None:
                raise KeyError(f"积分账户不存在(userId={user_id})")

            available = account.get("totalPoints", 0)
            if available < deduct_points:
                raise ValueError(f"积分不足(可用{available}, 需{deduct_points})")

            # FIFO 消耗过期批次
            batches = await self.repo.list_expiring_batches(user_id)
            remaining = deduct_points
            batch_details = []

            for batch in batches:
                if remaining <= 0:
                    break
                batch_id = batch["id"]
                batch_points = batch["points"]
                consumed = batch.get("consumedPoints", 0)
                available_in_batch = batch_points - consumed

                if available_in_batch <= 0:
                    continue

                consume = min(available_in_batch, remaining)
                new_consumed = consumed + consume
                new_status = (EXPIRE_STATUS_CONSUMED
                              if new_consumed >= batch_points else EXPIRE_STATUS_ACTIVE)
                await self.repo.update_expire_batch(batch_id, new_consumed, new_status)

                batch_details.append({
                    "batchId": batch_id,
                    "consumed": consume,
                    "remaining": batch_points - new_consumed,
                })
                remaining -= consume

            # 扣减账户余额
            account["totalPoints"] = available - deduct_points
            account["totalSpent"] = account.get("totalSpent", 0) + deduct_points
            await self.repo.save_account(account)

            # 写入消耗流水
            deduct_amount = deduct_points / POINTS_PER_YUAN
            log_id = await self.repo.add_log({
                "userId": user_id,
                "type": LOG_TYPE_SPEND,
                "source": SOURCE_DEDUCT,
                "points": -deduct_points,
                "balance": account["totalPoints"],
                "refId": order_id,
                "refDesc": f"订单抵扣(消耗{deduct_points}竹叶 = ¥{deduct_amount:.2f})",
                "expireAt": None,
                "status": LOG_STATUS_CONSUMED,
            })

            return {
                "logId": log_id,
                "userId": user_id,
                "orderId": order_id,
                "deductPoints": deduct_points,
                "deductAmount": round(deduct_amount, 2),
                "batchDetails": batch_details,
                "remainingPoints": account["totalPoints"],
            }

    # ============================================================
    # 4. 退款返还
    # ============================================================

    async def refund_points(self, user_id: int, order_id: str,
                             refund_points: int) -> dict:
        """退款时扣回已发放的积分

        规则:
            - 扣回该订单发放的积分(若不足则扣到负数, 后续消费返分补回)
            - 仅扣回, 不退回过期批次

        Returns:
            扣回结果

        Raises:
            ValueError: 扣回积分无效
        """
        if refund_points <= 0:
            raise ValueError("扣回积分必须大于0")

        lock_key = f"points:account:{user_id}"

        async with get_lock(lock_key):
            account = await self.repo.get_or_create_account(user_id)
            available = account.get("totalPoints", 0)

            # 扣回积分(可扣到负数, 后续补回)
            actual_refund = min(refund_points, available + account.get("totalEarned", 0))
            account["totalPoints"] = available - refund_points
            if account["totalPoints"] < 0:
                account["totalPoints"] = 0
                actual_refund = available

            account["totalSpent"] = account.get("totalSpent", 0) + actual_refund
            await self.repo.save_account(account)

            log_id = await self.repo.add_log({
                "userId": user_id,
                "type": LOG_TYPE_SPEND,
                "source": SOURCE_REFUND,
                "points": -actual_refund,
                "balance": account["totalPoints"],
                "refId": order_id,
                "refDesc": f"订单退款扣回积分({actual_refund}竹叶)",
                "expireAt": None,
                "status": LOG_STATUS_CONSUMED,
            })

            return {
                "logId": log_id,
                "userId": user_id,
                "orderId": order_id,
                "requestedRefund": refund_points,
                "actualRefund": actual_refund,
                "remainingPoints": account["totalPoints"],
            }

    # ============================================================
    # 5. 积分过期处理
    # ============================================================

    async def run_expire_process(self) -> dict:
        """执行积分过期扫描(24个月滚动过期)

        规则:
            - 扫描所有 status=0 且 expire_at < now 的批次
            - 更新批次状态为已过期
            - 扣减账户余额
            - 写入过期流水

        Returns:
            过期处理统计
        """
        async with get_lock("points:expire:run"):
            now = datetime.utcnow()
            expired_batches = await self.repo.list_expired_batches(now)

            expired_count = 0
            expired_points_total = 0
            user_points = {}  # userId → expired_points

            for batch in expired_batches:
                batch_id = batch["id"]
                user_id = batch["userId"]
                points = batch["points"] - batch.get("consumedPoints", 0)

                # 更新批次状态
                await self.repo.update_expire_batch(
                    batch_id, batch.get("consumedPoints", 0), EXPIRE_STATUS_EXPIRED
                )

                expired_count += 1
                expired_points_total += points
                user_points[user_id] = user_points.get(user_id, 0) + points

            # 扣减账户余额 + 写入流水
            for user_id, points in user_points.items():
                account = await self.repo.get_account(user_id)
                if account:
                    account["totalPoints"] = max(0, account.get("totalPoints", 0) - points)
                    await self.repo.save_account(account)

                    await self.repo.add_log({
                        "userId": user_id,
                        "type": LOG_TYPE_SPEND,
                        "source": SOURCE_EXPIRE,
                        "points": -points,
                        "balance": account["totalPoints"],
                        "refId": None,
                        "refDesc": f"积分过期({points}竹叶)",
                        "expireAt": None,
                        "status": LOG_STATUS_EXPIRED,
                    })

            return {
                "expiredCount": expired_count,
                "expiredPoints": expired_points_total,
                "affectedUsers": len(user_points),
                "processedAt": now.isoformat(),
            }

    # ============================================================
    # 6. 查询统计
    # ============================================================

    async def get_account(self, user_id: int) -> dict:
        """查询积分账户(不存在则创建)"""
        account = await self.repo.get_or_create_account(user_id)
        # 计算将过期积分
        expiring = await self.repo.list_expiring_soon(user_id, days=30)
        expiring_points = sum(b["points"] - b.get("consumedPoints", 0) for b in expiring)
        account["expiringPoints"] = expiring_points
        return account

    async def list_logs(self, user_id: int, source: str = None,
                        log_type: str = None, limit: int = 50) -> list[dict]:
        """查询积分流水"""
        return await self.repo.list_logs(user_id, source, log_type, limit)

    async def get_expiring_points(self, user_id: int, days: int = 30) -> dict:
        """查询将过期积分"""
        batches = await self.repo.list_expiring_soon(user_id, days)
        total = sum(b["points"] - b.get("consumedPoints", 0) for b in batches)
        return {
            "userId": user_id,
            "days": days,
            "expiringPoints": total,
            "batches": [
                {
                    "batchId": b["id"],
                    "points": b["points"] - b.get("consumedPoints", 0),
                    "expireAt": b.get("expireAt"),
                }
                for b in batches
            ],
        }

    async def get_stats(self, user_id: int) -> dict:
        """积分统计"""
        account = await self.repo.get_or_create_account(user_id)
        logs = await self.repo.list_logs(user_id, limit=10000)

        # 按来源统计获取积分
        earn_by_source = {}
        spend_by_source = {}
        for log in logs:
            source = log.get("source", "unknown")
            points = log.get("points", 0)
            if points > 0:
                earn_by_source[source] = earn_by_source.get(source, 0) + points
            else:
                spend_by_source[source] = spend_by_source.get(source, 0) + abs(points)

        # 签到统计
        signins = await self.repo.list_signins(user_id, limit=365)
        last_signin = signins[0] if signins else None

        return {
            "userId": user_id,
            "totalPoints": account.get("totalPoints", 0),
            "frozenPoints": account.get("frozenPoints", 0),
            "totalEarned": account.get("totalEarned", 0),
            "totalSpent": account.get("totalSpent", 0),
            "earnBySource": earn_by_source,
            "spendBySource": spend_by_source,
            "signinCount": len(signins),
            "lastSigninDate": last_signin.get("signDate") if last_signin else None,
            "lastSigninContinuous": last_signin.get("continuousDays") if last_signin else 0,
        }

    # ============================================================
    # 内部方法
    # ============================================================

    async def _earn_points(self, user_id: int, points: int,
                            source: str, ref_id: str = None,
                            ref_desc: str = "") -> int:
        """发放积分(内部方法, 需在锁内调用)

        流程:
            1. 获取/创建账户
            2. 增加可用积分
            3. 累计获取积分
            4. 写入获取流水
            5. 创建过期批次(24个月后)
        """
        account = await self.repo.get_or_create_account(user_id)
        account["totalPoints"] = account.get("totalPoints", 0) + points
        account["totalEarned"] = account.get("totalEarned", 0) + points
        await self.repo.save_account(account)

        # 写入流水
        expire_at = (datetime.utcnow() + timedelta(days=EXPIRE_MONTHS * 30)).isoformat()
        log_id = await self.repo.add_log({
            "userId": user_id,
            "type": LOG_TYPE_EARN,
            "source": source,
            "points": points,
            "balance": account["totalPoints"],
            "refId": ref_id,
            "refDesc": ref_desc,
            "expireAt": expire_at,
            "status": LOG_STATUS_AVAILABLE,
        })

        # 创建过期批次
        await self.repo.add_expire_batch({
            "userId": user_id,
            "batchId": None,  # 由 repo 填充
            "logId": log_id,
            "points": points,
            "consumedPoints": 0,
            "earnedAt": datetime.utcnow().isoformat(),
            "expireAt": expire_at,
            "status": EXPIRE_STATUS_ACTIVE,
        })

        return log_id
