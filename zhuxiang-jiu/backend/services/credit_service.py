"""信用管理模块业务逻辑层

核心业务:
    - 信用分调整(加分/扣分/人工调整/0-1000上下限)
    - 5级信用等级评定与升降级(v8.0: 持续天数规则+升级保护期+降级缓冲预警+信用修复期)
    - 先享后付额度查询与扣减/恢复
    - 先享后付订单流转(v8.0, 文档4.3: AI审批→占用额度→还款恢复→逾期费率)
    - 季度信用积分结算(v8.0, 文档5.1: 行为分×权重×时序系数+等级加成)
    - 季度奖励兑换(v8.0, 文档5.2: 现金/商品/权益/组合+个税规则+AI推荐)
    - 黑名单/冻结/恢复(状态机)
    - 信用统计与信用报告(全维度画像)

v8.0 等级规则引擎(文档4.1, 替代旧的即时映射):
    - 升级: 竹信分进入更高区间持续 N 天(L1→L2:30/L2→L3:60/L3→L4:90/L4→L5:120)
    - 降级: 竹信分跌破当前等级区间持续 30 天(升级后30天保护期内不降级)
    - 预警: 分数跌破区间即进入降级缓冲期(30天), 触发预警流水
    - 修复: 降级后60天修复期内, 升级所需持续天数减半(履约加速恢复)
    - 调分只更新分数与区间跟踪(scoreZoneSince), 等级由评估器按规则变更

锁保护:
    - 信用调整/订单/结算/兑换: lock:credit:account:{user_id}  (RMW原子操作)
    - 黑名单/恢复: lock:credit:account:{user_id}  (状态切换)

异常约定:
    - KeyError → 404(账户/订单不存在)
    - ValueError → 409(业务冲突: 已黑名单/额度不足等)
"""

from datetime import datetime, timedelta

from core.locks import get_lock
from core.helpers import ts
from repositories.credit_repository import (
    CreditRepository,
    # 信用等级
    LEVEL_L1, LEVEL_L2, LEVEL_L3, LEVEL_L4, LEVEL_L5,
    LEVEL_PAYLATER_QUOTA, LEVEL_B_PAYLATER_QUOTA,
    LEVEL_PAYLATER_INTEREST_FREE_DAYS,
    LEVEL_PAYLATER_SINGLE_LIMIT, LEVEL_PAYLATER_MONTHLY_LIMIT,
    LEVEL_REWARD_MULTIPLIER,
    LEVEL_UPGRADE_SUSTAIN_DAYS, LEVEL_DOWNGRADE_SUSTAIN_DAYS,
    UPGRADE_PROTECTION_DAYS, CREDIT_REPAIR_DAYS,
    level_from_score, clamp_score,
    # 流水类型
    LOG_TYPE_EARN, LOG_TYPE_DEDUCT, LOG_TYPE_ADJUST,
    LOG_TYPE_UPGRADE, LOG_TYPE_DOWNGRADE,
    LOG_TYPE_BLACKLIST, LOG_TYPE_RESTORE,
    LOG_TYPE_SEASON_SETTLE, LOG_TYPE_EXCHANGE,
    LOG_TYPE_PAYLATER_ORDER, LOG_TYPE_PAYLATER_REPAY,
    LOG_TYPE_LEVEL_WARNING,
    # 季度结算/兑换常量
    QUARTER_TIME_FACTOR, QUARTER_POINTS_CAP, QUARTER_CASH_CAP,
    CASH_TAX_FREE_AMOUNT, CASH_TAX_RATE,
    BEHAVIOR_WEIGHTS, EXCHANGE_RATES, EXCHANGE_CATALOG,
    # 先享后付常量
    PAYLATER_OVERDUE_DAILY_RATE, PAYLATER_OVERDUE_PENALTY_RATE,
    PAYLATER_PENALTY_START_DAYS, PAYLATER_OVERDUE_SCORE_PENALTY,
    PAYLATER_STATUS_REVIEW, PAYLATER_STATUS_ACTIVE,
    PAYLATER_STATUS_REPAID, PAYLATER_STATUS_REJECTED,
    PAYLATER_STATUS_OVERDUE,
    PAYLATER_ACCOUNT_MEMBER, PAYLATER_ACCOUNT_B,
    # 账户状态
    STATUS_NORMAL, STATUS_BLACKLIST,
    # 角色
    ROLE_MEMBER,
)

# B端角色(可使用B端先享后付额度)
B_ROLE_TYPES = {"agent", "partner", "distributor", "custom"}

# 季度时间范围: {季度: (起始月日, 结束月日)}
_QUARTER_RANGES = {
    1: ("01-01", "04-01"),
    2: ("04-01", "07-01"),
    3: ("07-01", "10-01"),
    4: ("10-01", "01-01"),
}


class CreditService:
    """信用管理业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: CreditRepository = CreditRepository()):
        self.repo = repo

    # ============================================================
    # 1. 查询信用分
    # ============================================================

    async def get_score(self, user_id: int) -> dict:
        """查询信用分账户(不存在则按会员创建)"""
        account = await self.repo.get_or_create_score(user_id, ROLE_MEMBER)
        return account

    # ============================================================
    # 2. 调整信用分
    # ============================================================

    async def adjust_score(self, user_id: int, delta: int,
                            reason: str = "", operator: str = "system",
                            role_type: str = ROLE_MEMBER) -> dict:
        """调整信用分(加分/扣分/人工调整)

        规则(v8.0, 文档4.1):
            - 竹信分上下限: 0 ≤ 竹信分 ≤ 1000
            - 黑名单账户不可调整
            - 调分只更新分数与区间跟踪(scoreZoneSince)
            - 等级变更由规则评估器决定(持续天数/保护期/修复期), 不再即时映射

        Returns:
            调整结果(含变化前后/等级评估摘要)

        Raises:
            ValueError: 黑名单账户不可调整
        """
        lock_key = f"credit:account:{user_id}"

        async with get_lock(lock_key):
            account = await self.repo.get_or_create_score(user_id, role_type)
            return await self._adjust_score_locked(
                account, delta, reason, operator)

    async def _adjust_score_locked(self, account: dict, delta: int,
                                    reason: str, operator: str) -> dict:
        """调整信用分(调用方须已持有 credit:account:{userId} 锁)"""
        user_id = account["userId"]

        if account.get("status") == STATUS_BLACKLIST:
            raise ValueError(f"黑名单账户不可调整信用分(userId={user_id})")

        now = datetime.utcnow().isoformat()
        score_before = account.get("bambooScore", 0)
        level_before = account.get("creditLevel", LEVEL_L1)

        # 计算新分数(限制在0-1000)
        new_score = clamp_score(score_before + delta)
        actual_delta = new_score - score_before

        # v8.0: 分数区间跟踪(跨区间时重置持续天数计时)
        old_zone = level_from_score(score_before)
        new_zone = level_from_score(new_score)
        if old_zone != new_zone:
            account["scoreZoneSince"] = now
            account["downgradeWarningIssuedAt"] = None  # 离开降级区间, 清除预警标记

        account["bambooScore"] = new_score
        if actual_delta > 0:
            account["totalEarned"] = account.get("totalEarned", 0) + actual_delta
            log_type = LOG_TYPE_EARN if delta > 0 else LOG_TYPE_ADJUST
        elif actual_delta < 0:
            log_type = LOG_TYPE_DEDUCT if delta < 0 else LOG_TYPE_ADJUST
        else:
            log_type = LOG_TYPE_ADJUST

        await self.repo.save_score(account)

        # 写入调整流水
        log_id = await self.repo.add_log({
            "userId": user_id,
            "type": log_type,
            "scoreBefore": score_before,
            "scoreAfter": new_score,
            "delta": actual_delta,
            "levelBefore": level_before,
            "levelAfter": account.get("creditLevel", level_before),
            "reason": reason,
            "operator": operator,
        })

        # v8.0: 规则评估(持续天数达标/预警时才变更等级)
        evaluation = await self._evaluate_level_locked(account, now, operator)

        return {
            "logId": log_id,
            "userId": user_id,
            "scoreBefore": score_before,
            "scoreAfter": new_score,
            "delta": actual_delta,
            "levelBefore": level_before,
            "levelAfter": account.get("creditLevel", level_before),
            "levelChanged": evaluation.get("levelChanged", False),
            "isUpgrade": evaluation.get("isUpgrade", False),
            "levelEvaluation": evaluation,
        }

    # ============================================================
    # 2.5 v8.0 等级规则评估引擎(文档4.1: 持续天数+保护期+预警+修复期)
    # ============================================================

    async def _evaluate_level_locked(self, account: dict, now: str,
                                     operator: str = "system") -> dict:
        """评估并应用等级变更(调用方须已持有锁; 变更时写升降级/预警流水)"""
        user_id = account["userId"]
        level_before = account.get("creditLevel", LEVEL_L1)
        score = account.get("bambooScore", 0)
        zone = level_from_score(score)
        zone_since = account.get("scoreZoneSince") or account.get("createdAt") or now
        zone_days = self._days_between(zone_since, now)
        last_change = account.get("lastLevelChangeAt") or now
        days_since_change = self._days_between(last_change, now)

        # 升级保护期(升级后30天内不降级)
        protection_left = max(0, UPGRADE_PROTECTION_DAYS - days_since_change)
        protected = protection_left > 0

        # 信用修复期(降级后60天, 升级所需持续天数减半)
        repair_until = account.get("repairUntil")
        in_repair = bool(repair_until and now < repair_until)

        zone_rank = self._level_rank(zone)
        level_rank = self._level_rank(level_before)
        action = "none"
        reason = ""
        new_level = level_before

        if zone_rank > level_rank:
            # 升级: 逐级晋升, 每级所需持续天数(修复期减半), 允许跨级追赶
            while (self._level_rank(new_level) < zone_rank
                   and zone_days >= self._upgrade_required_days(new_level, in_repair)):
                new_level = self._next_level(new_level)
            if new_level != level_before:
                action = "upgrade"
                required = self._upgrade_required_days(
                    self._prev_level(new_level), in_repair)
                reason = (f"竹信分≥{self._level_min_score(new_level)}持续"
                          f"{zone_days}天(需{required}天)"
                          + ("[修复期加速]" if in_repair else ""))
        elif zone_rank < level_rank:
            # 降级: 跌破区间持续30天; 保护期内不降级
            if protected:
                action = "none"
                reason = f"升级保护期内(剩{protection_left}天), 暂不降级"
            elif zone_days >= LEVEL_DOWNGRADE_SUSTAIN_DAYS:
                new_level = self._prev_level(level_before)
                action = "downgrade"
                reason = f"竹信分跌破{self._level_min_score(level_before)}持续{zone_days}天"
            else:
                # 降级缓冲期: 30天内预警一次
                action = "warning"
                reason = (f"降级缓冲期: 竹信分已跌破{self._level_min_score(level_before)}, "
                          f"持续{zone_days}天(满{LEVEL_DOWNGRADE_SUSTAIN_DAYS}天降级)")

        level_changed = new_level != level_before
        is_upgrade = level_changed and self._is_upgrade(level_before, new_level)

        # 应用等级变更
        if level_changed:
            self._apply_level_change(account, new_level, now, is_upgrade)
            await self.repo.save_score(account)
            await self.repo.add_log({
                "userId": user_id,
                "type": LOG_TYPE_UPGRADE if is_upgrade else LOG_TYPE_DOWNGRADE,
                "scoreBefore": score,
                "scoreAfter": score,
                "delta": 0,
                "levelBefore": level_before,
                "levelAfter": new_level,
                "reason": f"信用{'升级' if is_upgrade else '降级'}"
                          f"({level_before}→{new_level}): {reason}",
                "operator": operator,
            })
        elif action == "warning":
            # 缓冲期预警流水(同一区间只预警一次)
            if not account.get("downgradeWarningIssuedAt"):
                account["downgradeWarningIssuedAt"] = now
                await self.repo.save_score(account)
                await self.repo.add_log({
                    "userId": user_id,
                    "type": LOG_TYPE_LEVEL_WARNING,
                    "scoreBefore": score,
                    "scoreAfter": score,
                    "delta": 0,
                    "levelBefore": level_before,
                    "levelAfter": level_before,
                    "reason": reason,
                    "operator": operator,
                })

        return {
            "levelBefore": level_before,
            "levelAfter": account.get("creditLevel", level_before),
            "scoreZone": zone,
            "zoneSustainedDays": zone_days,
            "action": action,
            "actionReason": reason,
            "levelChanged": level_changed,
            "isUpgrade": is_upgrade,
            "protectionDaysLeft": protection_left,
            "inRepairPeriod": in_repair,
        }

    async def evaluate_level_transition(self, user_id: int,
                                        now: str = None) -> dict:
        """评估用户等级变更(定时任务/手动触发入口)

        Returns:
            评估结果(含是否变更/区间持续天数/保护期状态)
        """
        lock_key = f"credit:account:{user_id}"
        async with get_lock(lock_key):
            account = await self.repo.get_or_create_score(user_id, ROLE_MEMBER)
            return await self._evaluate_level_locked(
                account, now or datetime.utcnow().isoformat())

    async def evaluate_all_levels(self) -> dict:
        """批量评估全部账户等级(管理端触发, 逐账户加锁)"""
        accounts = await self.repo.list_scores(limit=1000000)
        changed, warned, total = 0, 0, len(accounts)
        for account in accounts:
            result = await self.evaluate_level_transition(account["userId"])
            if result.get("levelChanged"):
                changed += 1
            elif result.get("action") == "warning":
                warned += 1
        return {"totalAccounts": total, "levelChanged": changed,
                "warningsIssued": warned, "evaluatedAt": ts()}

    def _apply_level_change(self, account: dict, new_level: str,
                            now: str, is_upgrade: bool) -> None:
        """应用等级变更: 更新等级/额度/时间戳(降级进入60天修复期)"""
        account["creditLevel"] = new_level
        account["paylaterQuota"] = LEVEL_PAYLATER_QUOTA.get(new_level, 0)
        account["bPaylaterQuota"] = LEVEL_B_PAYLATER_QUOTA.get(new_level, 0)
        if account.get("paylaterUsed", 0) > account["paylaterQuota"]:
            account["paylaterUsed"] = account["paylaterQuota"]
        if account.get("bPaylaterUsed", 0) > account["bPaylaterQuota"]:
            account["bPaylaterUsed"] = account["bPaylaterQuota"]
        account["lastLevelChangeAt"] = now
        account["downgradeWarningIssuedAt"] = None
        if is_upgrade:
            account["repairUntil"] = None
        else:
            # 降级进入60天信用修复期
            repair_end = (datetime.fromisoformat(now)
                          + timedelta(days=CREDIT_REPAIR_DAYS)).isoformat()
            account["repairUntil"] = repair_end

    # ============================================================
    # 3. 信用流水查询
    # ============================================================

    async def list_logs(self, user_id: int, log_type: str = None,
                        limit: int = 50) -> list[dict]:
        """查询用户信用流水"""
        return await self.repo.list_logs(user_id, log_type, limit)

    # ============================================================
    # 4. 先享后付额度查询
    # ============================================================

    async def get_paylater_quota(self, user_id: int) -> dict:
        """查询先享后付额度

        Returns:
            额度详情(总额度/已用/可用/免息期)
        """
        account = await self.repo.get_or_create_score(user_id, ROLE_MEMBER)
        level = account.get("creditLevel", LEVEL_L1)
        quota = account.get("paylaterQuota", 0)
        used = account.get("paylaterUsed", 0)
        return {
            "userId": user_id,
            "creditLevel": level,
            "bambooScore": account.get("bambooScore", 0),
            "totalQuota": quota,
            "usedQuota": used,
            "availableQuota": max(0, quota - used),
            "interestFreeDays": LEVEL_PAYLATER_INTEREST_FREE_DAYS.get(level, 0),
            "status": account.get("status", STATUS_NORMAL),
        }

    # ============================================================
    # 5. 信用升级(强制升级)
    # ============================================================

    async def upgrade_level(self, user_id: int, target_level: str,
                             reason: str = "", operator: str = "admin") -> dict:
        """信用升级(强制设为目标等级对应分数下限)

        Returns:
            升级结果

        Raises:
            ValueError: 目标等级低于当前等级(不可降级)
            KeyError: 账户不存在
        """
        if target_level not in (LEVEL_L2, LEVEL_L3, LEVEL_L4, LEVEL_L5):
            raise ValueError(f"无效目标等级: {target_level}")

        lock_key = f"credit:account:{user_id}"

        async with get_lock(lock_key):
            account = await self.repo.get_score(user_id)
            if account is None:
                raise KeyError(f"信用账户不存在(userId={user_id})")

            if account.get("status") == STATUS_BLACKLIST:
                raise ValueError(f"黑名单账户不可升级(userId={user_id})")

            level_before = account.get("creditLevel", LEVEL_L1)
            if self._level_rank(target_level) <= self._level_rank(level_before):
                raise ValueError(f"目标等级{target_level}不高于当前等级{level_before}, 不可升级")

            # 设为目标等级下限
            target_score = self._level_min_score(target_level)
            score_before = account.get("bambooScore", 0)
            account["bambooScore"] = target_score
            now = datetime.utcnow().isoformat()
            self._apply_level_change(account, target_level, now, is_upgrade=True)
            await self.repo.save_score(account)

            log_id = await self.repo.add_log({
                "userId": user_id,
                "type": LOG_TYPE_UPGRADE,
                "scoreBefore": score_before,
                "scoreAfter": target_score,
                "delta": target_score - score_before,
                "levelBefore": level_before,
                "levelAfter": target_level,
                "reason": reason or f"管理员强制升级至{target_level}",
                "operator": operator,
            })

            return {
                "logId": log_id,
                "userId": user_id,
                "levelBefore": level_before,
                "levelAfter": target_level,
                "scoreBefore": score_before,
                "scoreAfter": target_score,
            }

    # ============================================================
    # 6. 信用降级(强制降级)
    # ============================================================

    async def downgrade_level(self, user_id: int, target_level: str,
                               reason: str = "", operator: str = "admin") -> dict:
        """信用降级(强制设为目标等级对应分数上限)

        Returns:
            降级结果

        Raises:
            ValueError: 目标等级高于当前等级(不可升级)
            KeyError: 账户不存在
        """
        if target_level not in (LEVEL_L1, LEVEL_L2, LEVEL_L3, LEVEL_L4):
            raise ValueError(f"无效目标等级: {target_level}")

        lock_key = f"credit:account:{user_id}"

        async with get_lock(lock_key):
            account = await self.repo.get_score(user_id)
            if account is None:
                raise KeyError(f"信用账户不存在(userId={user_id})")

            level_before = account.get("creditLevel", LEVEL_L1)
            if self._level_rank(target_level) >= self._level_rank(level_before):
                raise ValueError(f"目标等级{target_level}不低于当前等级{level_before}, 不可降级")

            # 设为目标等级上限
            target_score = self._level_max_score(target_level)
            score_before = account.get("bambooScore", 0)
            account["bambooScore"] = target_score
            now = datetime.utcnow().isoformat()
            # 强制降级进入60天信用修复期(文档4.1.3)
            self._apply_level_change(account, target_level, now, is_upgrade=False)
            await self.repo.save_score(account)

            log_id = await self.repo.add_log({
                "userId": user_id,
                "type": LOG_TYPE_DOWNGRADE,
                "scoreBefore": score_before,
                "scoreAfter": target_score,
                "delta": target_score - score_before,
                "levelBefore": level_before,
                "levelAfter": target_level,
                "reason": reason or f"管理员强制降级至{target_level}",
                "operator": operator,
            })

            return {
                "logId": log_id,
                "userId": user_id,
                "levelBefore": level_before,
                "levelAfter": target_level,
                "scoreBefore": score_before,
                "scoreAfter": target_score,
            }

    # ============================================================
    # 7. 加入黑名单
    # ============================================================

    async def add_to_blacklist(self, user_id: int, reason: str = "",
                                operator: str = "admin") -> dict:
        """加入黑名单(状态: blacklist, 竹信分直接扣至0)

        Returns:
            黑名单结果

        Raises:
            ValueError: 已在黑名单
            KeyError: 账户不存在
        """
        lock_key = f"credit:account:{user_id}"

        async with get_lock(lock_key):
            account = await self.repo.get_score(user_id)
            if account is None:
                raise KeyError(f"信用账户不存在(userId={user_id})")

            if account.get("status") == STATUS_BLACKLIST:
                raise ValueError(f"账户已在黑名单(userId={user_id})")

            score_before = account.get("bambooScore", 0)
            level_before = account.get("creditLevel", LEVEL_L1)
            account["status"] = STATUS_BLACKLIST
            account["bambooScore"] = 0
            now = datetime.utcnow().isoformat()
            self._apply_level_change(account, LEVEL_L1, now, is_upgrade=False)
            account["paylaterUsed"] = 0
            account["bPaylaterUsed"] = 0
            await self.repo.save_score(account)

            log_id = await self.repo.add_log({
                "userId": user_id,
                "type": LOG_TYPE_BLACKLIST,
                "scoreBefore": score_before,
                "scoreAfter": 0,
                "delta": -score_before,
                "levelBefore": level_before,
                "levelAfter": LEVEL_L1,
                "reason": reason or "加入黑名单",
                "operator": operator,
            })

            return {
                "logId": log_id,
                "userId": user_id,
                "status": STATUS_BLACKLIST,
                "scoreBefore": score_before,
                "scoreAfter": 0,
                "reason": reason,
            }

    # ============================================================
    # 8. 恢复信用(解除黑名单)
    # ============================================================

    async def restore_credit(self, user_id: int, restore_score: int = 350,
                              reason: str = "", operator: str = "admin") -> dict:
        """恢复信用(解除黑名单/冻结, 重置为指定分数)

        Returns:
            恢复结果

        Raises:
            ValueError: 账户非异常状态无需恢复
            KeyError: 账户不存在
        """
        if restore_score < 0 or restore_score > 1000:
            raise ValueError(f"恢复分数须在0-1000之间(got {restore_score})")

        lock_key = f"credit:account:{user_id}"

        async with get_lock(lock_key):
            account = await self.repo.get_score(user_id)
            if account is None:
                raise KeyError(f"信用账户不存在(userId={user_id})")

            if account.get("status") == STATUS_NORMAL:
                raise ValueError(f"账户状态正常, 无需恢复(userId={user_id})")

            score_before = account.get("bambooScore", 0)
            level_before = account.get("creditLevel", LEVEL_L1)
            restore_score = clamp_score(restore_score)
            new_level = level_from_score(restore_score)

            account["status"] = STATUS_NORMAL
            account["bambooScore"] = restore_score
            now = datetime.utcnow().isoformat()
            self._apply_level_change(account, new_level, now, is_upgrade=True)
            account["paylaterUsed"] = 0
            account["bPaylaterUsed"] = 0
            await self.repo.save_score(account)

            log_id = await self.repo.add_log({
                "userId": user_id,
                "type": LOG_TYPE_RESTORE,
                "scoreBefore": score_before,
                "scoreAfter": restore_score,
                "delta": restore_score - score_before,
                "levelBefore": level_before,
                "levelAfter": new_level,
                "reason": reason or "解除黑名单/冻结, 恢复信用",
                "operator": operator,
            })

            return {
                "logId": log_id,
                "userId": user_id,
                "status": STATUS_NORMAL,
                "scoreBefore": score_before,
                "scoreAfter": restore_score,
                "levelAfter": new_level,
                "reason": reason,
            }

    # ============================================================
    # 9. 信用统计
    # ============================================================

    async def get_stats(self, user_id: int) -> dict:
        """信用统计"""
        account = await self.repo.get_or_create_score(user_id, ROLE_MEMBER)
        logs = await self.repo.list_logs(user_id, limit=10000)

        # 按类型统计
        earn_count = sum(1 for l in logs if l.get("type") == LOG_TYPE_EARN)
        deduct_count = sum(1 for l in logs if l.get("type") == LOG_TYPE_DEDUCT)
        adjust_count = sum(1 for l in logs if l.get("type") == LOG_TYPE_ADJUST)
        upgrade_count = sum(1 for l in logs if l.get("type") == LOG_TYPE_UPGRADE)
        downgrade_count = sum(1 for l in logs if l.get("type") == LOG_TYPE_DOWNGRADE)
        blacklist_count = sum(1 for l in logs if l.get("type") == LOG_TYPE_BLACKLIST)
        restore_count = sum(1 for l in logs if l.get("type") == LOG_TYPE_RESTORE)

        level = account.get("creditLevel", LEVEL_L1)
        return {
            "userId": user_id,
            "bambooScore": account.get("bambooScore", 0),
            "creditLevel": level,
            "roleType": account.get("roleType", ROLE_MEMBER),
            "status": account.get("status", STATUS_NORMAL),
            "totalEarned": account.get("totalEarned", 0),
            "paylaterQuota": account.get("paylaterQuota", 0),
            "paylaterUsed": account.get("paylaterUsed", 0),
            "rewardMultiplier": LEVEL_REWARD_MULTIPLIER.get(level, 0.0),
            "logCount": len(logs),
            "earnCount": earn_count,
            "deductCount": deduct_count,
            "adjustCount": adjust_count,
            "upgradeCount": upgrade_count,
            "downgradeCount": downgrade_count,
            "blacklistCount": blacklist_count,
            "restoreCount": restore_count,
        }

    # ============================================================
    # 10. 信用报告
    # ============================================================

    async def get_credit_report(self, user_id: int) -> dict:
        """信用报告(全维度画像)"""
        account = await self.repo.get_or_create_score(user_id, ROLE_MEMBER)
        logs = await self.repo.list_logs(user_id, limit=100)

        level = account.get("creditLevel", LEVEL_L1)
        score = account.get("bambooScore", 0)

        # 等级权益
        benefits = self._get_level_benefits(level)

        # 近期变化
        recent_changes = [
            {
                "type": l.get("type"),
                "delta": l.get("delta", 0),
                "scoreBefore": l.get("scoreBefore"),
                "scoreAfter": l.get("scoreAfter"),
                "reason": l.get("reason", ""),
                "createdAt": l.get("createdAt"),
            }
            for l in logs[:10]
        ]

        return {
            "userId": user_id,
            "bambooScore": score,
            "creditLevel": level,
            "roleType": account.get("roleType", ROLE_MEMBER),
            "status": account.get("status", STATUS_NORMAL),
            "paylater": {
                "totalQuota": account.get("paylaterQuota", 0),
                "usedQuota": account.get("paylaterUsed", 0),
                "availableQuota": max(0, account.get("paylaterQuota", 0)
                                       - account.get("paylaterUsed", 0)),
                "interestFreeDays": LEVEL_PAYLATER_INTEREST_FREE_DAYS.get(level, 0),
            },
            "benefits": benefits,
            "rewardMultiplier": LEVEL_REWARD_MULTIPLIER.get(level, 0.0),
            "totalEarned": account.get("totalEarned", 0),
            "recentChanges": recent_changes,
            "reportAt": ts(),
        }

    # ============================================================
    # 11. v8.0 季度信用积分结算(文档5.1)
    # ============================================================

    async def settle_quarter(self, user_id: int, year: int, quarter: int,
                             operator: str = "system") -> dict:
        """季度信用积分结算

        公式(文档5.1.2): 季度积分 = Σ(行为分 × 行为权重 × 时序系数1.5), 上限5000
        等级加成: L5×1.5 / L4×1.2 / L3×1.0 / L2×0.8 / L1×0

        Raises:
            ValueError: 季度无效/该季度已结算(幂等)
        """
        if quarter not in _QUARTER_RANGES:
            raise ValueError(f"无效季度: {quarter}(须为1-4)")

        lock_key = f"credit:account:{user_id}"
        async with get_lock(lock_key):
            account = await self.repo.get_or_create_score(user_id, ROLE_MEMBER)

            existing = await self.repo.get_settlement(user_id, year, quarter)
            if existing is not None:
                raise ValueError(
                    f"该季度已结算(userId={user_id}, {year}Q{quarter}, "
                    f"结算ID={existing.get('settlementId')})")

            # 季度时间范围(Q4跨年: 结束于次年1月1日)
            start_md, end_md = _QUARTER_RANGES[quarter]
            start = f"{year}-{start_md}"
            end_year = year + 1 if quarter == 4 else year
            end = f"{end_year}-{end_md}"

            logs = await self.repo.list_logs_between(user_id, start, end)

            # 基础积分: Σ(行为分 × 权重 × 时序系数), 截断到 [0, 5000]
            raw = sum(
                (l.get("delta", 0) or 0)
                * BEHAVIOR_WEIGHTS.get(l.get("type"), 0.0)
                * QUARTER_TIME_FACTOR
                for l in logs
                if l.get("type") in BEHAVIOR_WEIGHTS
            )
            base_points = max(0, min(QUARTER_POINTS_CAP, round(raw)))

            # 等级加成(结算时点等级)
            level = account.get("creditLevel", LEVEL_L1)
            multiplier = LEVEL_REWARD_MULTIPLIER.get(level, 0.0)
            final_points = round(base_points * multiplier)

            account["creditPoints"] = account.get("creditPoints", 0) + final_points
            await self.repo.save_score(account)

            settlement = {
                "userId": user_id,
                "year": year,
                "quarter": quarter,
                "logCount": len(logs),
                "basePoints": base_points,
                "levelMultiplier": multiplier,
                "finalPoints": final_points,
                "creditPointsAfter": account["creditPoints"],
                "operator": operator,
            }
            settlement_id = await self.repo.add_settlement(settlement)

            await self.repo.add_log({
                "userId": user_id,
                "type": LOG_TYPE_SEASON_SETTLE,
                "scoreBefore": account.get("bambooScore", 0),
                "scoreAfter": account.get("bambooScore", 0),
                "delta": 0,
                "levelBefore": level,
                "levelAfter": level,
                "reason": (f"{year}Q{quarter}季度结算: 基础{base_points}积分"
                           f"×{multiplier}倍={final_points}积分"),
                "operator": operator,
            })

            return {**settlement, "settlementId": settlement_id}

    async def list_quarterly_settlements(self, user_id: int,
                                         limit: int = 20) -> list[dict]:
        """查询用户季度结算记录"""
        return await self.repo.list_settlements(user_id, limit)

    # ============================================================
    # 12. v8.0 季度奖励兑换(文档5.2)
    # ============================================================

    async def exchange_rewards(self, user_id: int, exchange_type: str,
                               points: int, item_id: str = None,
                               operator: str = "user") -> dict:
        """积分兑换(现金/商品/权益/组合)

        规则(文档5.2.1/5.2.2):
            - 现金: 100积分=¥1, 季度上限¥5000, 超¥800部分扣20%个税
            - 商品/权益: 按目录价格兑换(须指定item_id), 无税收
            - 组合: 100积分=¥1.3, 现金性质部分(50%)计税

        Raises:
            ValueError: 类型无效/积分不足/商品无效/超季度上限/角色不符
        """
        if exchange_type not in EXCHANGE_RATES:
            raise ValueError(f"无效兑换类型: {exchange_type}(须为cash/goods/benefit/combo)")
        if points <= 0:
            raise ValueError(f"兑换积分须大于0(got {points})")

        lock_key = f"credit:account:{user_id}"
        async with get_lock(lock_key):
            account = await self.repo.get_or_create_score(user_id, ROLE_MEMBER)

            if account.get("creditPoints", 0) < points:
                raise ValueError(
                    f"积分不足(可用{account.get('creditPoints', 0)}, 需要{points})")

            now = datetime.utcnow().isoformat()
            value, tax, net_value = 0.0, 0.0, 0.0
            item_name = None

            if exchange_type == "cash":
                value = points / 100.0 * EXCHANGE_RATES["cash"]
                # 季度现金上限(含本次)
                quarter_used = await self._quarter_cash_used(user_id, now)
                if quarter_used + value > QUARTER_CASH_CAP:
                    raise ValueError(
                        f"超季度现金兑换上限(已兑¥{quarter_used:.2f}+本次¥{value:.2f}"
                        f" > ¥{QUARTER_CASH_CAP})")
                # 个税: 超¥800部分扣20%
                if value > CASH_TAX_FREE_AMOUNT:
                    tax = (value - CASH_TAX_FREE_AMOUNT) * CASH_TAX_RATE
                net_value = value - tax
                item_name = "现金(钱包发放)"
            elif exchange_type in ("goods", "benefit"):
                item = EXCHANGE_CATALOG.get(item_id)
                if item is None:
                    raise ValueError(f"无效兑换目录ID: {item_id}")
                if item["category"] != exchange_type:
                    raise ValueError(
                        f"目录ID {item_id} 属于{item['category']}, 与兑换类型{exchange_type}不符")
                if item["roles"] and account.get("roleType", ROLE_MEMBER) not in item["roles"]:
                    raise ValueError(f"商品 {item['name']} 仅限B端角色兑换")
                if points != item["points"]:
                    raise ValueError(
                        f"兑换积分不符(目录价{item['points']}分, 提交{points}分)")
                value = item["value"]
                net_value = value  # 商品/权益无税收
                item_name = item["name"]
            else:  # combo
                value = points / 100.0 * EXCHANGE_RATES["combo"]
                # 组合兑换: 现金性质部分(50%)计税(文档: 部分税收)
                cash_portion = value * 0.5
                if cash_portion > CASH_TAX_FREE_AMOUNT:
                    tax = (cash_portion - CASH_TAX_FREE_AMOUNT) * CASH_TAX_RATE
                net_value = value - tax
                item_name = "组合兑换(现金+商品+权益)"

            account["creditPoints"] = account.get("creditPoints", 0) - points
            account["totalRewarded"] = round(
                account.get("totalRewarded", 0.0) + net_value, 2)
            await self.repo.save_score(account)

            exchange = {
                "userId": user_id,
                "exchangeType": exchange_type,
                "points": points,
                "itemId": item_id,
                "itemName": item_name,
                "value": round(value, 2),
                "tax": round(tax, 2),
                "netValue": round(net_value, 2),
                "operator": operator,
            }
            exchange_id = await self.repo.add_exchange(exchange)

            await self.repo.add_log({
                "userId": user_id,
                "type": LOG_TYPE_EXCHANGE,
                "scoreBefore": account.get("bambooScore", 0),
                "scoreAfter": account.get("bambooScore", 0),
                "delta": 0,
                "levelBefore": account.get("creditLevel", LEVEL_L1),
                "levelAfter": account.get("creditLevel", LEVEL_L1),
                "reason": f"积分兑换{exchange_type}: {points}积分→¥{net_value:.2f}"
                          f"({item_name})",
                "operator": operator,
            })

            return {**exchange, "exchangeId": exchange_id}

    async def list_exchanges(self, user_id: int, exchange_type: str = None,
                             limit: int = 50) -> list[dict]:
        """查询用户兑换记录"""
        return await self.repo.list_exchanges(user_id, exchange_type, limit)

    async def _quarter_cash_used(self, user_id: int, now_iso: str) -> float:
        """本季度已现金兑换金额(含税前面值)"""
        now = datetime.fromisoformat(now_iso)
        quarter = (now.month - 1) // 3 + 1
        start_md, end_md = _QUARTER_RANGES[quarter]
        start = f"{now.year}-{start_md}"
        end_year = now.year + 1 if quarter == 4 else now.year
        end = f"{end_year}-{end_md}"
        exchanges = await self.repo.list_exchanges(user_id, "cash", limit=10000)
        return sum(e.get("value", 0) for e in exchanges
                   if start <= (e.get("createdAt") or "") < end)

    # ============================================================
    # 13. v8.0 AI兑换方案推荐(文档5.3)
    # ============================================================

    async def recommend_exchange(self, user_id: int) -> dict:
        """AI兑换方案推荐(规则引擎: Top3方案+推荐理由)

        策略(对齐文档5.3.2示例):
            - 方案1(最优): 可负担的最高净值目录商品/权益
            - 方案2: 全额现金兑换(灵活+税收明细)
            - 方案3: 目标商品+补差价(缺积分按¥0.01/分折算)
        """
        account = await self.repo.get_or_create_score(user_id, ROLE_MEMBER)
        points = account.get("creditPoints", 0)
        role = account.get("roleType", ROLE_MEMBER)
        is_b_role = role in B_ROLE_TYPES

        # 可兑换目录(按角色过滤)
        catalog = [item for item in EXCHANGE_CATALOG.values()
                   if not item["roles"] or role in item["roles"]]
        affordable = [i for i in catalog if i["points"] <= points]

        plans = []

        # 方案1: 可负担的最高价值商品/权益
        if affordable:
            best = max(affordable, key=lambda i: i["value"])
            plans.append({
                "planNo": 1,
                "type": best["category"],
                "itemId": next(k for k, v in EXCHANGE_CATALOG.items() if v is best),
                "itemName": best["name"],
                "points": best["points"],
                "value": best["value"],
                "tax": 0.0,
                "netValue": best["value"],
                "supplementCash": 0.0,
                "reason": ("消费偏好匹配+目录折扣最优+无税收"
                           + ("+B端权益匹配" if best["roles"] else "")),
            })
        else:
            plans.append({
                "planNo": 1,
                "type": "benefit",
                "itemId": "B003",
                "itemName": "擂台赛投票权+5票",
                "points": 1000,
                "value": 10.0,
                "tax": 0.0,
                "netValue": 10.0,
                "supplementCash": max(0.0, (1000 - points) / 100.0),
                "reason": "积分不足时门槛最低的权益兑换+补差价灵活",
            })

        # 方案2: 全额现金(税收明细)
        cash_value = points / 100.0 * EXCHANGE_RATES["cash"]
        cash_tax = (max(0.0, cash_value - CASH_TAX_FREE_AMOUNT)
                    * CASH_TAX_RATE) if cash_value > CASH_TAX_FREE_AMOUNT else 0.0
        cash_cap = QUARTER_CASH_CAP
        plans.append({
            "planNo": 2,
            "type": "cash",
            "itemId": None,
            "itemName": f"现金兑换¥{cash_value:.2f}(钱包发放)",
            "points": points,
            "value": round(cash_value, 2),
            "tax": round(cash_tax, 2),
            "netValue": round(cash_value - cash_tax, 2),
            "supplementCash": 0.0,
            "reason": ("现金灵活+钱包即时到账"
                       + ("+超¥800部分扣20%个税" if cash_tax > 0 else "+无税收")
                       + f"+季度上限¥{cash_cap:.0f}"),
        })

        # 方案3: 目标商品+补差价(取当前积分买不起的最近目标)
        targets = [i for i in catalog if i["points"] > points]
        if targets:
            target = min(targets, key=lambda i: i["points"])
            missing = target["points"] - points
            plans.append({
                "planNo": 3,
                "type": target["category"],
                "itemId": next(k for k, v in EXCHANGE_CATALOG.items() if v is target),
                "itemName": target["name"],
                "points": target["points"],
                "value": target["value"],
                "tax": 0.0,
                "netValue": target["value"],
                "supplementCash": round(missing / 100.0, 2),
                "reason": f"目标商品+缺{missing}积分按¥0.01/分补差价¥{missing / 100.0:.2f}",
            })
        else:
            # 积分已可兑换全部目录: 推荐组合兑换(1.3倍率)
            combo_value = points / 100.0 * EXCHANGE_RATES["combo"]
            plans.append({
                "planNo": 3,
                "type": "combo",
                "itemId": None,
                "itemName": "组合兑换(现金+商品+权益)",
                "points": points,
                "value": round(combo_value, 2),
                "tax": 0.0,
                "netValue": round(combo_value, 2),
                "supplementCash": 0.0,
                "reason": "积分充足+组合兑换1.3倍率+价值最大化",
            })

        return {
            "userId": user_id,
            "roleType": role,
            "creditPoints": points,
            "isBRole": is_b_role,
            "plans": plans,
            "recommendedPlanNo": 1,
            "generatedAt": ts(),
        }

    # ============================================================
    # 14. v8.0 先享后付订单流转(文档4.3)
    # ============================================================

    async def create_paylater_order(self, user_id: int, amount: float,
                                    account_type: str = PAYLATER_ACCOUNT_MEMBER,
                                    order_no: str = "",
                                    source: str = "order") -> dict:
        """创建先享后付订单(AI智能授信审批, 文档4.3.2)

        审批策略:
            - 自动拒绝: 额度不足/单笔超限/月度超限/信用分跌破等级区间/黑名单
            - 人工审批: 大额(>50%额度)/信用分下滑(区间低于等级)/接近月度上限(>80%)
            - 自动通过: 低风险+额度充足

        Raises:
            ValueError: 审批拒绝(业务冲突)
            KeyError: 账户不存在
        """
        if amount <= 0:
            raise ValueError(f"订单金额须大于0(got {amount})")
        if account_type not in (PAYLATER_ACCOUNT_MEMBER, PAYLATER_ACCOUNT_B):
            raise ValueError(f"无效账户类型: {account_type}(须为member/b)")

        lock_key = f"credit:account:{user_id}"
        async with get_lock(lock_key):
            account = await self.repo.get_score(user_id)
            if account is None:
                raise KeyError(f"信用账户不存在(userId={user_id})")

            if account.get("status") == STATUS_BLACKLIST:
                raise ValueError(f"黑名单账户不可使用先享后付(userId={user_id})")

            level = account.get("creditLevel", LEVEL_L1)
            score = account.get("bambooScore", 0)

            # B端额度仅限B端角色
            if account_type == PAYLATER_ACCOUNT_B and account.get("roleType", ROLE_MEMBER) not in B_ROLE_TYPES:
                raise ValueError("B端额度仅限代理商/合作方/分销商/定制客户使用")

            # 额度与上限(文档4.3.1)
            if account_type == PAYLATER_ACCOUNT_MEMBER:
                quota = account.get("paylaterQuota", 0)
                used = account.get("paylaterUsed", 0.0)
            else:
                quota = account.get("bPaylaterQuota", 0)
                used = account.get("bPaylaterUsed", 0.0)

            type_idx = 0 if account_type == PAYLATER_ACCOUNT_MEMBER else 1
            single_limit = LEVEL_PAYLATER_SINGLE_LIMIT.get(level, (0, 0))[type_idx]
            monthly_limit = LEVEL_PAYLATER_MONTHLY_LIMIT.get(level, (0, 0))[type_idx]

            if quota <= 0:
                raise ValueError(f"当前等级{level}无先享后付额度")
            if amount > single_limit:
                raise ValueError(
                    f"单笔金额超限(¥{amount} > {account_type}上限¥{single_limit})")
            if used + amount > quota:
                raise ValueError(
                    f"可用额度不足(可用¥{quota - used}, 本次¥{amount})")

            # 月度累计(本自然月 active/review/repaid 订单)
            now = datetime.utcnow()
            month_prefix = now.strftime("%Y-%m")
            orders = await self.repo.list_paylater_orders(user_id, limit=10000)
            month_used = sum(
                o.get("amount", 0) for o in orders
                if (o.get("createdAt") or "").startswith(month_prefix)
                and o.get("status") in (PAYLATER_STATUS_ACTIVE,
                                        PAYLATER_STATUS_REVIEW,
                                        PAYLATER_STATUS_REPAID))
            if month_used + amount > monthly_limit:
                raise ValueError(
                    f"月度累计超限(本月已用¥{month_used}+本次¥{amount}"
                    f" > {account_type}上限¥{monthly_limit})")

            now_iso = now.isoformat()

            # AI风险审批(文档4.3.2 Step2/3)
            score_zone = level_from_score(score)
            zone_rank = self._level_rank(score_zone)
            level_rank = self._level_rank(level)
            risk_flags = []
            if zone_rank < 3:
                risk_flags.append("信用分低于先享后付门槛(L3)")
            if zone_rank < level_rank:
                risk_flags.append("信用分已跌破等级区间")

            if zone_rank < 3:
                # 高风险: 自动拒绝
                order = self._new_paylater_order(
                    user_id, amount, account_type, order_no, source,
                    PAYLATER_STATUS_REJECTED, level, now_iso,
                    risk_level="high", risk_flags=risk_flags)
                order_id = await self.repo.add_paylater_order(order)
                await self._log_paylater(user_id, order, "自动拒绝")
                raise ValueError(
                    f"先享后付审批拒绝: {','.join(risk_flags)}(userId={user_id})")

            review_flags = list(risk_flags)
            if amount > quota * 0.5:
                review_flags.append("大额订单(>50%额度)")
            if month_used + amount > monthly_limit * 0.8:
                review_flags.append("接近月度上限(>80%)")

            if review_flags:
                # 中风险: 人工审批
                order = self._new_paylater_order(
                    user_id, amount, account_type, order_no, source,
                    PAYLATER_STATUS_REVIEW, level, now_iso,
                    risk_level="mid", risk_flags=review_flags)
                order_id = await self.repo.add_paylater_order(order)
                await self._log_paylater(user_id, order, "转人工审批")
            else:
                # 低风险: 自动通过, 占用额度
                order = self._new_paylater_order(
                    user_id, amount, account_type, order_no, source,
                    PAYLATER_STATUS_ACTIVE, level, now_iso,
                    risk_level="low", risk_flags=[])
                order["approvedAt"] = now_iso
                order_id = await self.repo.add_paylater_order(order)
                if account_type == PAYLATER_ACCOUNT_MEMBER:
                    account["paylaterUsed"] = round(
                        account.get("paylaterUsed", 0.0) + amount, 2)
                else:
                    account["bPaylaterUsed"] = round(
                        account.get("bPaylaterUsed", 0.0) + amount, 2)
                await self.repo.save_score(account)
                await self._log_paylater(user_id, order, "自动通过")

            return {**order, "orderId": order_id}

    async def review_paylater_order(self, order_id: int, approved: bool,
                                    operator: str = "admin") -> dict:
        """人工审批先享后付订单(管理端)

        Raises:
            KeyError: 订单不存在
            ValueError: 订单不在审批状态/审批通过但额度不足
        """
        lock_key = "credit:paylater:order:review"
        async with get_lock(lock_key):
            order = await self.repo.get_paylater_order(order_id)
            if order is None:
                raise KeyError(f"先享后付订单不存在(orderId={order_id})")
            if order.get("status") != PAYLATER_STATUS_REVIEW:
                raise ValueError(
                    f"订单不在审批状态(当前: {order.get('status')})")

            now_iso = datetime.utcnow().isoformat()
            user_id = order["userId"]

            if approved:
                # 审批通过前再校验额度
                async with get_lock(f"credit:account:{user_id}"):
                    account = await self.repo.get_score(user_id)
                    if account is None:
                        raise KeyError(f"信用账户不存在(userId={user_id})")
                    if account_type_key := order.get("accountType"):
                        if account_type_key == PAYLATER_ACCOUNT_MEMBER:
                            quota, used = (account.get("paylaterQuota", 0),
                                           account.get("paylaterUsed", 0.0))
                        else:
                            quota, used = (account.get("bPaylaterQuota", 0),
                                           account.get("bPaylaterUsed", 0.0))
                        amount = order.get("amount", 0)
                        if used + amount > quota:
                            raise ValueError(
                                f"审批通过失败: 可用额度不足(可用¥{quota - used})")
                        order["status"] = PAYLATER_STATUS_ACTIVE
                        order["approvedAt"] = now_iso
                        order["reviewedBy"] = operator
                        if account_type_key == PAYLATER_ACCOUNT_MEMBER:
                            account["paylaterUsed"] = round(used + amount, 2)
                        else:
                            account["bPaylaterUsed"] = round(used + amount, 2)
                        await self.repo.save_score(account)
            else:
                order["status"] = PAYLATER_STATUS_REJECTED
                order["rejectedAt"] = now_iso
                order["reviewedBy"] = operator

            await self.repo.save_paylater_order(order)
            await self._log_paylater(
                user_id, order, "人工审批通过" if approved else "人工审批拒绝")
            return order

    async def repay_paylater_order(self, order_id: int,
                                   repay_channel: str = "wallet") -> dict:
        """先享后付还款(恢复额度+逾期费用+信用分惩罚)

        规则(文档4.3.3):
            - 逾期费用: 日费率0.035%(逾期1天起)
            - 逾期罚息: 日息0.1%(逾期7天起)
            - 逾期影响: 信用分-20/次

        Raises:
            KeyError: 订单不存在
            ValueError: 订单不可还款
        """
        order = await self.repo.get_paylater_order(order_id)
        if order is None:
            raise KeyError(f"先享后付订单不存在(orderId={order_id})")
        if order.get("status") not in (PAYLATER_STATUS_ACTIVE,
                                       PAYLATER_STATUS_OVERDUE):
            raise ValueError(f"订单不可还款(当前状态: {order.get('status')})")

        user_id = order["userId"]
        amount = order.get("amount", 0)
        lock_key = f"credit:account:{user_id}"

        async with get_lock(lock_key):
            now_iso = datetime.utcnow().isoformat()
            due_date = order.get("dueDate") or now_iso
            overdue_days = max(0, self._days_between(due_date, now_iso))

            # 逾期费用(1天起) + 罚息(7天起)
            fees = round(amount * PAYLATER_OVERDUE_DAILY_RATE * overdue_days, 2) \
                if overdue_days >= 1 else 0.0
            penalty = round(
                amount * PAYLATER_OVERDUE_PENALTY_RATE
                * max(0, overdue_days - PAYLATER_PENALTY_START_DAYS + 1), 2) \
                if overdue_days >= PAYLATER_PENALTY_START_DAYS else 0.0
            repay_total = round(amount + fees + penalty, 2)

            # 恢复额度
            account = await self.repo.get_score(user_id)
            if account is None:
                raise KeyError(f"信用账户不存在(userId={user_id})")
            if order.get("accountType") == PAYLATER_ACCOUNT_B:
                account["bPaylaterUsed"] = round(
                    max(0.0, account.get("bPaylaterUsed", 0.0) - amount), 2)
            else:
                account["paylaterUsed"] = round(
                    max(0.0, account.get("paylaterUsed", 0.0) - amount), 2)

            order["status"] = PAYLATER_STATUS_REPAID
            order["repaidAt"] = now_iso
            order["repayChannel"] = repay_channel
            order["overdueDays"] = overdue_days
            order["overdueFees"] = fees
            order["penaltyFees"] = penalty
            order["repayTotal"] = repay_total
            await self.repo.save_paylater_order(order)
            await self.repo.save_score(account)

            # 逾期信用分惩罚(-20/次)
            credit_penalty_applied = False
            if overdue_days >= 1:
                await self._adjust_score_locked(
                    account, -PAYLATER_OVERDUE_SCORE_PENALTY,
                    f"先享后付逾期{overdue_days}天(订单{order_id})",
                    "system")
                credit_penalty_applied = True

            await self._log_paylater(
                user_id, order,
                f"还款¥{repay_total}(逾期{overdue_days}天, 费用¥{fees + penalty})",
                log_type=LOG_TYPE_PAYLATER_REPAY)

            return {
                "orderId": order_id,
                "userId": user_id,
                "amount": amount,
                "overdueDays": overdue_days,
                "overdueFees": fees,
                "penaltyFees": penalty,
                "repayTotal": repay_total,
                "repayChannel": repay_channel,
                "creditPenaltyApplied": credit_penalty_applied,
                "status": PAYLATER_STATUS_REPAID,
                "repaidAt": now_iso,
            }

    async def list_paylater_orders(self, user_id: int, status: str = None,
                                   limit: int = 100) -> list[dict]:
        """查询用户先享后付订单"""
        return await self.repo.list_paylater_orders(user_id, status, limit)

    def _new_paylater_order(self, user_id: int, amount: float,
                            account_type: str, order_no: str, source: str,
                            status: str, level: str, now_iso: str,
                            risk_level: str, risk_flags: list) -> dict:
        """构造先享后付订单(免息期按等级, 到期日=创建日+免息期)"""
        interest_free = LEVEL_PAYLATER_INTEREST_FREE_DAYS.get(level, 0)
        due = (datetime.fromisoformat(now_iso)
               + timedelta(days=interest_free)).isoformat()
        return {
            "userId": user_id,
            "orderNo": order_no or f"PL{int(datetime.fromisoformat(now_iso).timestamp() * 1000)}",
            "accountType": account_type,
            "source": source,
            "amount": round(float(amount), 2),
            "status": status,
            "creditLevelAtCreate": level,
            "interestFreeDays": interest_free,
            "dueDate": due,
            "riskLevel": risk_level,
            "riskFlags": risk_flags,
        }

    async def _log_paylater(self, user_id: int, order: dict, action: str,
                            log_type: str = None) -> None:
        """写先享后付订单动作流水"""
        await self.repo.add_log({
            "userId": user_id,
            "type": log_type or LOG_TYPE_PAYLATER_ORDER,
            "scoreBefore": 0,
            "scoreAfter": 0,
            "delta": 0,
            "levelBefore": order.get("creditLevelAtCreate", LEVEL_L1),
            "levelAfter": order.get("creditLevelAtCreate", LEVEL_L1),
            "reason": f"先享后付订单{order.get('orderNo')}: ¥{order.get('amount')}"
                      f"({order.get('accountType')}) {action}",
            "operator": "system",
        })

    # ============================================================
    # 内部辅助
    # ============================================================

    @staticmethod
    def _upgrade_required_days(level: str, in_repair: bool) -> int:
        """从level升级到下一级所需持续天数(信用修复期内减半)"""
        required = LEVEL_UPGRADE_SUSTAIN_DAYS.get(level, 30)
        if in_repair:
            required = max(1, required // 2)
        return required

    @staticmethod
    def _level_rank(level: str) -> int:
        """等级序号(L1=1, L5=5)"""
        return {"L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5}.get(level, 1)

    @staticmethod
    def _next_level(level: str) -> str:
        """下一更高等级(已到L5返回L5)"""
        ranks = {"L1": "L2", "L2": "L3", "L3": "L4", "L4": "L5"}
        return ranks.get(level, LEVEL_L5)

    @staticmethod
    def _prev_level(level: str) -> str:
        """下一更低等级(已到L1返回L1)"""
        ranks = {"L5": "L4", "L4": "L3", "L3": "L2", "L2": "L1"}
        return ranks.get(level, LEVEL_L1)

    @staticmethod
    def _days_between(start_iso: str, end_iso: str) -> int:
        """两个ISO时间之间的天数(向下取整, 起点晚于终点返回0)"""
        try:
            start = datetime.fromisoformat(str(start_iso))
            end = datetime.fromisoformat(str(end_iso))
        except (TypeError, ValueError):
            return 0
        seconds = (end - start).total_seconds()
        return max(0, int(seconds // 86400))

    @classmethod
    def _is_upgrade(cls, before: str, after: str) -> bool:
        """是否升级"""
        return cls._level_rank(after) > cls._level_rank(before)

    @staticmethod
    def _level_min_score(level: str) -> int:
        """等级最低分"""
        return {"L1": 0, "L2": 400, "L3": 550, "L4": 700, "L5": 800}.get(level, 0)

    @staticmethod
    def _level_max_score(level: str) -> int:
        """等级最高分"""
        return {"L1": 399, "L2": 549, "L3": 699, "L4": 799, "L5": 1000}.get(level, 399)

    @staticmethod
    def _get_level_benefits(level: str) -> dict:
        """等级权益描述"""
        benefits_map = {
            LEVEL_L1: {"paylater": 0, "rewardMultiplier": 0.0,
                        "description": "极差: 无先享后付+无季度奖励+限购"},
            LEVEL_L2: {"paylater": 0, "rewardMultiplier": 0.8,
                        "description": "较差: 无先享后付+季度奖励0.8倍+限购"},
            LEVEL_L3: {"paylater": 2000, "rewardMultiplier": 1.0,
                        "description": "中等: 先享后付¥2000+季度奖励1.0倍+普通客服"},
            LEVEL_L4: {"paylater": 5000, "rewardMultiplier": 1.2,
                        "description": "良好: 先享后付¥5000+季度奖励1.2倍+优先客服"},
            LEVEL_L5: {"paylater": 10000, "rewardMultiplier": 1.5,
                        "description": "优秀: 先享后付¥10000+季度奖励1.5倍+专属客服"},
        }
        return benefits_map.get(level, benefits_map[LEVEL_L1])
