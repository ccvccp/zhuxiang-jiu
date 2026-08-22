"""活动管理模块业务逻辑层

核心业务:
    - 创建活动(8类: 促销/抽奖/竞赛/擂台赛/互动/拼团/秒杀/预售)
    - 报名(状态机/幂等防重/取消)
    - 状态流转(草稿→报名中→进行中→已结束→已取消)
    - 擂台赛排名(8类擂台赛/评分/排名更新)
    - 活动统计(报名数/预算使用等)

锁保护:
    - 创建活动: lock:activity:create:{type}  (生成活动ID串行)
    - 报名: lock:activity:reg:{activity_id}:{user_id}  (幂等防重)
    - 状态流转: lock:activity:status:{activity_id}  (状态原子切换)
    - 擂台赛: lock:activity:leaderboard:{activity_id}  (排名原子更新)

异常约定:
    - KeyError → 404(活动不存在)
    - ValueError → 409(业务冲突: 重复报名/状态非法流转/已报名取消)
"""

from datetime import datetime
from typing import Optional

from core.locks import get_lock
from core.helpers import ts
from repositories.activity_repository import (
    ActivityRepository,
    # 活动类型
    TYPE_PROMOTION, TYPE_LOTTERY, TYPE_COMPETITION, TYPE_ARENA,
    TYPE_INTERACTIVE, TYPE_GROUPBUY, TYPE_SECKILL, TYPE_PRESALE,
    # 擂台赛类型
    ARENA_L01, ARENA_L02, ARENA_L03, ARENA_L04,
    ARENA_L05, ARENA_L06, ARENA_L07, ARENA_L08,
    # 状态机
    STATUS_DRAFT, STATUS_REGISTERING, STATUS_ONGOING, STATUS_ENDED, STATUS_CANCELLED,
    STATUS_TRANSITIONS, can_transition,
    # 报名状态
    REG_STATUS_REGISTERED, REG_STATUS_CANCELLED,
    # 奖品类型
    PRIZE_COUPON, PRIZE_POINTS, PRIZE_PRODUCT, PRIZE_CASH,
    PRIZE_BENEFIT, PRIZE_BANQUET_WINE, PRIZE_MASCOT,
)


# 8类擂台赛名称
ARENA_NAMES = {
    ARENA_L01: "引流擂台赛",
    ARENA_L02: "体验擂台赛",
    ARENA_L03: "销售擂台赛",
    ARENA_L04: "金点子擂台赛",
    ARENA_L05: "内容擂台赛",
    ARENA_L06: "品鉴擂台赛",
    ARENA_L07: "服务擂台赛",
    ARENA_L08: "传承擂台赛",
}


class ActivityService:
    """活动管理业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: ActivityRepository = ActivityRepository()):
        self.repo = repo

    # ============================================================
    # 1. 创建活动
    # ============================================================

    async def create_activity(self, name: str, type_: str, sub_type: str = "",
                                description: str = "", start_time: str = "",
                                end_time: str = "", budget: float = 0.0,
                                rules: dict = None, applicable_scope: dict = None,
                                created_by: int = 0) -> dict:
        """创建活动(初始状态: 草稿)

        Returns:
            活动详情

        Raises:
            ValueError: 无效活动类型
        """
        valid_types = {TYPE_PROMOTION, TYPE_LOTTERY, TYPE_COMPETITION, TYPE_ARENA,
                       TYPE_INTERACTIVE, TYPE_GROUPBUY, TYPE_SECKILL, TYPE_PRESALE}
        if type_ not in valid_types:
            raise ValueError(f"无效活动类型: {type_}")

        lock_key = f"activity:create:{type_}"

        async with get_lock(lock_key):
            activity_id = await self.repo.next_activity_id()
            now = datetime.utcnow().isoformat()
            activity_no = f"HD{now[:10].replace('-', '')}{activity_id:06d}"

            activity = {
                "id": activity_id,
                "activityNo": activity_no,
                "name": name,
                "type": type_,
                "subType": sub_type,
                "description": description,
                "startTime": start_time,
                "endTime": end_time,
                "status": STATUS_DRAFT,
                "budget": budget,
                "usedBudget": 0.0,
                "rules": rules or {},
                "applicableScope": applicable_scope or {},
                "registrationCount": 0,
                "createdBy": created_by,
                "approvedBy": 0,
                "createdAt": now,
                "updatedAt": now,
            }
            await self.repo.save_activity(activity)
            return activity

    # ============================================================
    # 2. 查询活动列表
    # ============================================================

    async def list_activities(self, status: str = None, type_: str = None,
                              limit: int = 50) -> list[dict]:
        """查询活动列表(默认仅查非草稿状态)"""
        if status is None:
            # 默认过滤掉草稿
            activities = await self.repo.list_activities(status=status, type_=type_, limit=limit)
            return [a for a in activities if a.get("status") != STATUS_DRAFT]
        return await self.repo.list_activities(status=status, type_=type_, limit=limit)

    async def list_admin_activities(self, status: str = None, limit: int = 50) -> list[dict]:
        """管理端查询活动列表(含草稿)"""
        return await self.repo.list_admin_activities(status=status, limit=limit)

    # ============================================================
    # 3. 查询活动详情
    # ============================================================

    async def get_activity(self, activity_id: int) -> dict:
        """查询活动详情

        Raises:
            KeyError: 活动不存在
        """
        activity = await self.repo.get_activity(activity_id)
        if activity is None:
            raise KeyError(f"活动不存在(activityId={activity_id})")
        # 附加报名数
        activity["registrationCount"] = await self.repo.count_registrations(activity_id)
        return activity

    # ============================================================
    # 4. 报名
    # ============================================================

    async def register(self, activity_id: int, user_id: int,
                       participate_data: dict = None) -> dict:
        """活动报名(幂等防重)

        规则:
            - 仅报名中(STATUS_REGISTERING)状态可报名
            - 同一用户对同一活动仅可报名一次(幂等)
            - 取消后可重新报名(更新状态)

        Returns:
            报名结果

        Raises:
            KeyError: 活动不存在
            ValueError: 活动状态不允许报名/已报名未取消
        """
        lock_key = f"activity:reg:{activity_id}:{user_id}"

        async with get_lock(lock_key):
            activity = await self.repo.get_activity(activity_id)
            if activity is None:
                raise KeyError(f"活动不存在(activityId={activity_id})")

            if activity.get("status") != STATUS_REGISTERING:
                raise ValueError(f"活动状态不允许报名(当前: {activity.get('status')})")

            # 幂等校验: 是否已报名
            existing = await self.repo.get_registration(activity_id, user_id)
            if existing and existing.get("status") == REG_STATUS_REGISTERED:
                raise ValueError(f"用户已报名(activityId={activity_id}, userId={user_id})")

            now = datetime.utcnow().isoformat()
            if existing and existing.get("status") == REG_STATUS_CANCELLED:
                # 取消后重新报名, 复用记录
                await self.repo.update_registration_status(existing["id"], REG_STATUS_REGISTERED)
                existing["status"] = REG_STATUS_REGISTERED
                existing["participateData"] = participate_data or {}
                existing["updatedAt"] = now
                return existing

            reg_id = await self.repo.add_registration({
                "activityId": activity_id,
                "userId": user_id,
                "participateTime": now,
                "participateData": participate_data or {},
                "status": REG_STATUS_REGISTERED,
            })

            return {
                "id": reg_id,
                "activityId": activity_id,
                "userId": user_id,
                "participateTime": now,
                "participateData": participate_data or {},
                "status": REG_STATUS_REGISTERED,
                "createdAt": now,
            }

    # ============================================================
    # 5. 取消报名
    # ============================================================

    async def cancel_registration(self, activity_id: int, user_id: int) -> dict:
        """取消报名

        Raises:
            KeyError: 报名记录不存在
            ValueError: 活动状态不允许取消/已取消
        """
        lock_key = f"activity:reg:{activity_id}:{user_id}"

        async with get_lock(lock_key):
            activity = await self.repo.get_activity(activity_id)
            if activity is None:
                raise KeyError(f"活动不存在(activityId={activity_id})")

            if activity.get("status") not in (STATUS_REGISTERING, STATUS_ONGOING):
                raise ValueError(f"活动状态不允许取消(当前: {activity.get('status')})")

            reg = await self.repo.get_registration(activity_id, user_id)
            if reg is None:
                raise KeyError(f"报名记录不存在(activityId={activity_id}, userId={user_id})")

            if reg.get("status") == REG_STATUS_CANCELLED:
                raise ValueError(f"报名已取消, 无需重复操作")

            await self.repo.update_registration_status(reg["id"], REG_STATUS_CANCELLED)
            reg["status"] = REG_STATUS_CANCELLED
            reg["updatedAt"] = datetime.utcnow().isoformat()
            return reg

    # ============================================================
    # 6. 活动状态流转
    # ============================================================

    async def transition_status(self, activity_id: int, target_status: str,
                                 operator: int = 0) -> dict:
        """活动状态流转(草稿→报名中→进行中→已结束)

        Raises:
            KeyError: 活动不存在
            ValueError: 非法状态流转
        """
        lock_key = f"activity:status:{activity_id}"

        async with get_lock(lock_key):
            activity = await self.repo.get_activity(activity_id)
            if activity is None:
                raise KeyError(f"活动不存在(activityId={activity_id})")

            current_status = activity.get("status", STATUS_DRAFT)
            if not can_transition(current_status, target_status):
                raise ValueError(
                    f"非法状态流转: {current_status} → {target_status}"
                )

            activity["status"] = target_status
            if target_status == STATUS_REGISTERING and operator:
                activity["approvedBy"] = operator
            await self.repo.save_activity(activity)

            return {
                "activityId": activity_id,
                "statusBefore": current_status,
                "statusAfter": target_status,
                "operator": operator,
                "updatedAt": activity.get("updatedAt"),
            }

    # ============================================================
    # 7. 擂台赛排名
    # ============================================================

    async def submit_arena_score(self, activity_id: int, user_id: int,
                                   score: float, real_name: str = "") -> dict:
        """提交擂台赛分数(自动排名)

        Returns:
            提交结果(含当前排名)

        Raises:
            KeyError: 活动不存在
            ValueError: 非擂台赛活动/活动未在进行中
        """
        lock_key = f"activity:leaderboard:{activity_id}"

        async with get_lock(lock_key):
            activity = await self.repo.get_activity(activity_id)
            if activity is None:
                raise KeyError(f"活动不存在(activityId={activity_id})")

            if activity.get("type") != TYPE_ARENA:
                raise ValueError(f"非擂台赛活动不允许提交分数(type={activity.get('type')})")

            if activity.get("status") not in (STATUS_ONGOING, STATUS_REGISTERING):
                raise ValueError(f"活动状态不允许提交分数(当前: {activity.get('status')})")

            # 查找已存在的排名记录
            leaderboard = await self.repo.list_leaderboard(activity_id, limit=10000)
            existing_entry = None
            for e in leaderboard:
                if e.get("userId") == user_id:
                    existing_entry = e
                    break

            if existing_entry:
                # 更新分数
                await self.repo.update_leaderboard_entry(
                    existing_entry["id"], score, existing_entry.get("rank", 0)
                )
                existing_entry["score"] = score
            else:
                # 新增
                entry_id = await self.repo.add_leaderboard_entry({
                    "activityId": activity_id,
                    "userId": user_id,
                    "realName": real_name,
                    "score": score,
                    "rank": 0,  # 待重新计算
                })
                existing_entry = {
                    "id": entry_id,
                    "activityId": activity_id,
                    "userId": user_id,
                    "realName": real_name,
                    "score": score,
                    "rank": 0,
                }
                leaderboard.append(existing_entry)

            # 重新计算排名(按分数降序)
            leaderboard.sort(key=lambda e: e.get("score", 0), reverse=True)
            for idx, entry in enumerate(leaderboard):
                new_rank = idx + 1
                if entry.get("rank") != new_rank:
                    await self.repo.update_leaderboard_entry(
                        entry["id"], entry.get("score", 0), new_rank
                    )
                    entry["rank"] = new_rank

            return {
                "activityId": activity_id,
                "userId": user_id,
                "score": score,
                "rank": existing_entry.get("rank"),
                "totalParticipants": len(leaderboard),
            }

    async def get_leaderboard(self, activity_id: int, limit: int = 100) -> list[dict]:
        """查询擂台赛排名(按 rank 升序)

        Raises:
            KeyError: 活动不存在
            ValueError: 非擂台赛活动
        """
        activity = await self.repo.get_activity(activity_id)
        if activity is None:
            raise KeyError(f"活动不存在(activityId={activity_id})")
        if activity.get("type") != TYPE_ARENA:
            raise ValueError(f"非擂台赛活动无排名(type={activity.get('type')})")
        return await self.repo.list_leaderboard(activity_id, limit)

    # ============================================================
    # 8. 活动统计
    # ============================================================

    async def get_stats(self, activity_id: int) -> dict:
        """活动统计

        Raises:
            KeyError: 活动不存在
        """
        activity = await self.repo.get_activity(activity_id)
        if activity is None:
            raise KeyError(f"活动不存在(activityId={activity_id})")

        reg_count = await self.repo.count_registrations(activity_id)
        regs = await self.repo.list_registrations(activity_id, limit=10000)
        cancelled_count = sum(1 for r in regs if r.get("status") == REG_STATUS_CANCELLED)

        # 擂台赛排名数据
        leaderboard_count = 0
        if activity.get("type") == TYPE_ARENA:
            leaderboard = await self.repo.list_leaderboard(activity_id, limit=10000)
            leaderboard_count = len(leaderboard)

        budget = activity.get("budget", 0)
        used_budget = activity.get("usedBudget", 0)
        return {
            "activityId": activity_id,
            "name": activity.get("name"),
            "type": activity.get("type"),
            "status": activity.get("status"),
            "budget": budget,
            "usedBudget": used_budget,
            "budgetUsageRate": round(used_budget / budget, 4) if budget > 0 else 0,
            "registrationCount": reg_count,
            "cancelledCount": cancelled_count,
            "leaderboardCount": leaderboard_count,
            "startTime": activity.get("startTime"),
            "endTime": activity.get("endTime"),
        }

    # ============================================================
    # 9. 活动审核
    # ============================================================

    async def audit_activity(self, activity_id: int, approve: bool,
                              auditor: int = 0, reason: str = "") -> dict:
        """活动审核(草稿→报名中 or 拒绝)

        Raises:
            KeyError: 活动不存在
            ValueError: 活动状态不允许审核
        """
        lock_key = f"activity:status:{activity_id}"

        async with get_lock(lock_key):
            activity = await self.repo.get_activity(activity_id)
            if activity is None:
                raise KeyError(f"活动不存在(activityId={activity_id})")

            if activity.get("status") != STATUS_DRAFT:
                raise ValueError(f"仅草稿状态可审核(当前: {activity.get('status')})")

            if approve:
                activity["status"] = STATUS_REGISTERING
                activity["approvedBy"] = auditor
            else:
                activity["status"] = STATUS_CANCELLED
                activity["auditReason"] = reason
            await self.repo.save_activity(activity)

            return {
                "activityId": activity_id,
                "approved": approve,
                "status": activity.get("status"),
                "auditor": auditor,
                "reason": reason,
            }
