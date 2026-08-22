"""信用管理模块业务逻辑层

核心业务:
    - 信用分调整(加分/扣分/人工调整/0-1000上下限)
    - 5级信用等级自动评定与升降级
    - 先享后付额度查询与扣减/恢复
    - 黑名单/冻结/恢复(状态机)
    - 信用统计与信用报告(全维度画像)

锁保护:
    - 信用调整: lock:credit:account:{user_id}  (RMW原子操作)
    - 黑名单/恢复: lock:credit:account:{user_id}  (状态切换)

异常约定:
    - KeyError → 404(账户不存在)
    - ValueError → 409(业务冲突: 已黑名单/额度不足等)
"""

from datetime import datetime
from typing import Optional

from core.locks import get_lock
from core.helpers import ts
from repositories.credit_repository import (
    CreditRepository,
    # 信用等级
    LEVEL_L1, LEVEL_L2, LEVEL_L3, LEVEL_L4, LEVEL_L5,
    LEVEL_PAYLATER_QUOTA, LEVEL_PAYLATER_INTEREST_FREE_DAYS,
    LEVEL_REWARD_MULTIPLIER, level_from_score, clamp_score,
    # 流水类型
    LOG_TYPE_EARN, LOG_TYPE_DEDUCT, LOG_TYPE_ADJUST,
    LOG_TYPE_UPGRADE, LOG_TYPE_DOWNGRADE,
    LOG_TYPE_BLACKLIST, LOG_TYPE_RESTORE,
    # 账户状态
    STATUS_NORMAL, STATUS_FROZEN, STATUS_BLACKLIST,
    # 角色
    ROLE_MEMBER,
)


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

        规则:
            - 竹信分上下限: 0 ≤ 竹信分 ≤ 1000
            - 黑名单账户不可调整
            - 自动评定等级与升降级

        Returns:
            调整结果(含变化前后/是否升级)

        Raises:
            ValueError: 黑名单账户不可调整
        """
        lock_key = f"credit:account:{user_id}"

        async with get_lock(lock_key):
            account = await self.repo.get_or_create_score(user_id, role_type)

            if account.get("status") == STATUS_BLACKLIST:
                raise ValueError(f"黑名单账户不可调整信用分(userId={user_id})")

            score_before = account.get("bambooScore", 0)
            level_before = account.get("creditLevel", LEVEL_L1)

            # 计算新分数(限制在0-1000)
            new_score = clamp_score(score_before + delta)
            actual_delta = new_score - score_before

            account["bambooScore"] = new_score
            if actual_delta > 0:
                account["totalEarned"] = account.get("totalEarned", 0) + actual_delta
                log_type = LOG_TYPE_EARN if delta > 0 else LOG_TYPE_ADJUST
            elif actual_delta < 0:
                log_type = LOG_TYPE_DEDUCT if delta < 0 else LOG_TYPE_ADJUST
            else:
                log_type = LOG_TYPE_ADJUST

            # 自动评定等级
            new_level = level_from_score(new_score)
            account["creditLevel"] = new_level

            # 等级变化时同步先享后付额度
            level_changed = new_level != level_before
            if level_changed:
                account["paylaterQuota"] = LEVEL_PAYLATER_QUOTA.get(new_level, 0)
                # 降级时已用额度不能超过新额度
                if account.get("paylaterUsed", 0) > account["paylaterQuota"]:
                    account["paylaterUsed"] = account["paylaterQuota"]

            await self.repo.save_score(account)

            # 写入调整流水
            log_id = await self.repo.add_log({
                "userId": user_id,
                "type": log_type,
                "scoreBefore": score_before,
                "scoreAfter": new_score,
                "delta": actual_delta,
                "levelBefore": level_before,
                "levelAfter": new_level,
                "reason": reason,
                "operator": operator,
            })

            # 等级变化时记录升降级流水
            if level_changed:
                is_upgrade = self._is_upgrade(level_before, new_level)
                await self.repo.add_log({
                    "userId": user_id,
                    "type": LOG_TYPE_UPGRADE if is_upgrade else LOG_TYPE_DOWNGRADE,
                    "scoreBefore": score_before,
                    "scoreAfter": new_score,
                    "delta": 0,
                    "levelBefore": level_before,
                    "levelAfter": new_level,
                    "reason": f"信用{'升级' if is_upgrade else '降级'}({level_before}→{new_level})",
                    "operator": operator,
                })

            return {
                "logId": log_id,
                "userId": user_id,
                "scoreBefore": score_before,
                "scoreAfter": new_score,
                "delta": actual_delta,
                "levelBefore": level_before,
                "levelAfter": new_level,
                "levelChanged": level_changed,
                "isUpgrade": level_changed and self._is_upgrade(level_before, new_level),
            }

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
            account["creditLevel"] = target_level
            account["paylaterQuota"] = LEVEL_PAYLATER_QUOTA.get(target_level, 0)
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
            account["creditLevel"] = target_level
            account["paylaterQuota"] = LEVEL_PAYLATER_QUOTA.get(target_level, 0)
            # 降级后已用额度不能超过新额度
            if account.get("paylaterUsed", 0) > account["paylaterQuota"]:
                account["paylaterUsed"] = account["paylaterQuota"]
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
            account["creditLevel"] = LEVEL_L1
            account["paylaterQuota"] = 0
            account["paylaterUsed"] = 0
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
            account["creditLevel"] = new_level
            account["paylaterQuota"] = LEVEL_PAYLATER_QUOTA.get(new_level, 0)
            account["paylaterUsed"] = 0
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
    # 内部辅助
    # ============================================================

    @staticmethod
    def _level_rank(level: str) -> int:
        """等级序号(L1=1, L5=5)"""
        return {"L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5}.get(level, 1)

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
