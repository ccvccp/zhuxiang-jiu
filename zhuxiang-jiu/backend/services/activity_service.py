"""活动管理模块业务逻辑层

核心业务:
    - 创建活动(8类: 促销/抽奖/竞赛/擂台赛/互动/拼团/秒杀/预售)
    - 报名(状态机/幂等防重/取消)
    - 状态流转(草稿→报名中→进行中→已结束→已取消)
    - 擂台赛排名(8类擂台赛/评分/排名更新)
    - 活动统计(报名数/预算使用等)
    - 抽奖发奖(P1-12: 奖品池配置/抽奖执行/服务端概率计算/
      中奖发奖分派/我的奖品/实物发货/签收确认)

锁保护:
    - 创建活动: lock:activity:create:{type}  (生成活动ID串行)
    - 报名: lock:activity:reg:{activity_id}:{user_id}  (幂等防重)
    - 状态流转: lock:activity:status:{activity_id}  (状态原子切换)
    - 擂台赛: lock:activity:leaderboard:{activity_id}  (排名原子更新)
    - 抽奖: lock:activity:lottery:{activity_id}:{user_id}  (日次数+中奖原子)

异常约定:
    - KeyError → 404(活动不存在)
    - ValueError → 409(业务冲突: 重复报名/状态非法流转/已报名取消)
"""

import json
import logging
from datetime import datetime

from core.locks import get_lock
from repositories.activity_repository import (
    ActivityRepository,
    # 活动类型
    TYPE_PROMOTION, TYPE_LOTTERY, TYPE_COMPETITION, TYPE_ARENA,
    TYPE_INTERACTIVE, TYPE_GROUPBUY, TYPE_SECKILL, TYPE_PRESALE,
    # 擂台赛类型
    ARENA_L01, ARENA_L02, ARENA_L03, ARENA_L04,
    ARENA_L05, ARENA_L06, ARENA_L07, ARENA_L08,
    # 状态机
    STATUS_DRAFT, STATUS_REGISTERING, STATUS_ONGOING, STATUS_CANCELLED,
    can_transition,
    # 报名状态
    REG_STATUS_REGISTERED, REG_STATUS_CANCELLED,
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

logger = logging.getLogger("activity_service")


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
                raise ValueError("报名已取消, 无需重复操作")

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

    # ============================================================
    # 10. 抽奖发奖(P1-12, 设计文档 §3.3/§7.1/§7.3)
    # ============================================================

    # 每日抽奖次数上限(设计文档 §3.3.1 简化版: 每日免费 3 次)
    DAILY_DRAW_LIMIT = 3
    # 单奖价值上限(合规红线 §10.1: 单次一等奖 ≤¥5,000)
    MAX_PRIZE_VALUE = 5000.0
    # 概率总和上限(%)
    MAX_TOTAL_PROBABILITY = 100.0

    async def configure_prizes(self, activity_id: int,
                                prizes: list[dict]) -> dict:
        """配置抽奖奖品池(管理端)

        每个奖品: {prizeName, prizeType, prizeValue, probability(%),
                   dailyLimit, totalLimit}

        校验:
            - 活动须为 lottery 类型
            - 概率总和 ≤ 100%
            - 单奖价值 ≤ ¥5,000(合规红线)

        Raises:
            KeyError: 活动不存在
            ValueError: 类型不符 / 概率非法 / 价值超限
        """
        from repositories.activity_repository import PRIZE_COUPON, PRIZE_POINTS
        activity = await self.repo.get_activity(activity_id)
        if activity is None:
            raise KeyError(f"活动不存在(activityId={activity_id})")
        if activity.get("type") != TYPE_LOTTERY:
            raise ValueError("仅抽奖活动可配置奖品池")

        if not prizes:
            raise ValueError("奖品列表不能为空")

        total_prob = 0.0
        normalized = []
        for i, p in enumerate(prizes):
            prob = float(p.get("probability", 0))
            value = float(p.get("prizeValue", 0))
            total_limit = int(p.get("totalLimit", 0))
            if prob < 0 or prob > 100:
                raise ValueError(f"奖品{i + 1}概率非法(0-100)")
            if value > self.MAX_PRIZE_VALUE:
                raise ValueError(f"奖品{p.get('prizeName', i + 1)}价值超限"
                                 f"(须 ≤ ¥{self.MAX_PRIZE_VALUE:.0f}, 合规红线)")
            total_prob += prob
            normalized.append({
                "prizeId": i + 1,
                "prizeName": p.get("prizeName", f"奖品{i + 1}"),
                "prizeType": p.get("prizeType", PRIZE_COUPON),
                "prizeValue": round(value, 2),
                "probability": prob,
                "dailyLimit": int(p.get("dailyLimit", 0)),
                "totalLimit": total_limit,
                "remaining": total_limit,
                "issuedToday": 0,
            })
        if total_prob > self.MAX_TOTAL_PROBABILITY + 0.01:
            raise ValueError(f"概率总和超限({total_prob}% > 100%)")

        await self.repo.save_prizes(activity_id, normalized)
        logger.info("activity_prizes_configured activity=%s prizes=%d "
                    "total_prob=%.1f%%", activity_id, len(normalized),
                    total_prob)
        return {"activityId": activity_id,
                "prizeCount": len(normalized),
                "totalProbability": round(total_prob, 2),
                "prizes": normalized}

    async def get_prize_pool(self, activity_id: int) -> list[dict]:
        """查询奖品池(概率公示, §3.3.3 合规要求)

        Raises:
            KeyError: 活动不存在
        """
        activity = await self.repo.get_activity(activity_id)
        if activity is None:
            raise KeyError(f"活动不存在(activityId={activity_id})")
        return await self.repo.list_prizes(activity_id)

    async def draw_lottery(self, activity_id: int, user_id: int) -> dict:
        """抽奖执行(服务端概率计算, 前端仅展示, §3.3.3)

        流程:
            1. 校验: 活动为 lottery 且 ongoing / 每日次数未超(3 次)
            2. 服务端概率计算: random() 落区间; remaining=0 的奖项概率归零
               并按剩余奖项概率归一
            3. 中奖: 落发奖记录 + 扣减 remaining + 累加 usedBudget
               + 按奖品类型分派发放(§7.3)
            4. 落抽奖记录(日次数统计)

        Raises:
            KeyError: 活动不存在
            ValueError: 非抽奖活动 / 活动未进行中 / 次数用尽 / 无奖品池
        """
        import random
        from datetime import datetime as _dt
        from repositories.activity_repository import (
            PRIZE_POINTS, PRIZE_COUPON, PRIZE_PRODUCT, PRIZE_CASH,
            PRIZE_BENEFIT, PRIZE_BANQUET_WINE, PRIZE_MASCOT,
            PRIZE_STATUS_PENDING, PRIZE_STATUS_ISSUED,
        )

        async with get_lock(f"activity:lottery:{activity_id}:{user_id}"):
            activity = await self.repo.get_activity(activity_id)
            if activity is None:
                raise KeyError(f"活动不存在(activityId={activity_id})")
            if activity.get("type") != TYPE_LOTTERY:
                raise ValueError("非抽奖活动不允许抽奖")
            if activity.get("status") != STATUS_ONGOING:
                raise ValueError(f"活动状态不允许抽奖(当前: "
                                 f"{activity.get('status')})")

            today = _dt.utcnow().strftime("%Y-%m-%d")
            drawn = await self.repo.count_today_draws(activity_id, user_id, today)
            if drawn >= self.DAILY_DRAW_LIMIT:
                raise ValueError(f"今日抽奖次数已用尽"
                                 f"({self.DAILY_DRAW_LIMIT} 次/日)")

            prizes = await self.repo.list_prizes(activity_id)
            if not prizes:
                raise ValueError("活动未配置奖品池")

            # ---- 服务端概率计算 ----
            available = [p for p in prizes if p.get("remaining", 0) > 0]
            total_prob = sum(p["probability"] for p in available)
            won = None
            if available and total_prob > 0:
                roll = random.uniform(0, total_prob)
                cumulative = 0.0
                for p in available:
                    cumulative += p["probability"]
                    if roll < cumulative:
                        won = p
                        break

            # ---- 落抽奖记录 ----
            now = datetime.utcnow().isoformat()
            record_no = await self.repo.next_prize_record_no()
            draw_result = {
                "activityId": activity_id,
                "userId": user_id,
                "drawDate": today,
                "drawnAt": now,
                "won": won is not None,
                "prizeName": won["prizeName"] if won else "",
            }
            await self.repo.add_lottery_record(draw_result)

            if won is None:
                return {"activityId": activity_id, "userId": user_id,
                        "won": False, "prizeName": "",
                        "drawsRemainingToday": self.DAILY_DRAW_LIMIT - drawn - 1,
                        "msg": "谢谢参与"}

            # ---- 中奖: 扣减/落记录/发放分派 ----
            for p in prizes:
                if p.get("prizeId") == won["prizeId"]:
                    p["remaining"] = p.get("remaining", 0) - 1
                    p["issuedToday"] = p.get("issuedToday", 0) + 1
                    break
            await self.repo.update_prizes(activity_id, prizes)

            # 预算累加
            activity["usedBudget"] = round(
                float(activity.get("usedBudget", 0))
                + float(won.get("prizeValue", 0)), 2)
            await self.repo.save_activity(activity)

            # 发放分派(§7.3): 积分/优惠券自动到账, 其余落待发放
            auto_types = {PRIZE_POINTS, PRIZE_COUPON, PRIZE_BENEFIT}
            status = (PRIZE_STATUS_ISSUED
                      if won["prizeType"] in auto_types
                      else PRIZE_STATUS_PENDING)
            record = {
                "recordNo": record_no,
                "activityId": activity_id,
                "activityNo": activity.get("activityNo", ""),
                "userId": user_id,
                "prizeId": won["prizeId"],
                "prizeName": won["prizeName"],
                "prizeType": won["prizeType"],
                "prizeValue": won.get("prizeValue", 0),
                "status": status,
                "couponNo": "",
                "pointsLogId": None,
                "waybillNo": "",
                "sentAt": "",
                "receivedAt": "",
                "createdAt": now,
            }

            if won["prizeType"] == PRIZE_POINTS:
                # 积分自动到账(best-effort, 券额=prizeValue 视为积分值)
                try:
                    from services.points_service import PointsService
                    points = int(won.get("prizeValue", 0))
                    if points > 0:
                        result = await PointsService().earn_points(
                            user_id, points, source="lottery",
                            ref_id=record_no,
                            ref_desc=f"抽奖中奖: {won['prizeName']}")
                        record["pointsLogId"] = result.get("logId")
                except Exception as exc:
                    logger.warning("lottery_points_issue_failed record=%s "
                                   "err=%s", record_no, exc)
            elif won["prizeType"] == PRIZE_COUPON:
                # 优惠券: 券系统未落地, 落补偿券记录(对齐 P1-9 模式)
                try:
                    record["couponNo"] = await self._issue_lottery_coupon(
                        user_id, record_no, won)
                except Exception as exc:
                    logger.warning("lottery_coupon_issue_failed record=%s "
                                   "err=%s", record_no, exc)

            await self.repo.add_prize_record(record)
            logger.info("lottery_won activity=%s user=%s prize=%s(%s) "
                        "record=%s status=%s", activity_id, user_id,
                        won["prizeName"], won["prizeType"], record_no, status)

            return {
                "activityId": activity_id,
                "userId": user_id,
                "won": True,
                "prizeName": won["prizeName"],
                "prizeType": won["prizeType"],
                "prizeValue": won.get("prizeValue", 0),
                "recordNo": record_no,
                "status": status,
                "couponNo": record.get("couponNo", ""),
                "drawsRemainingToday": self.DAILY_DRAW_LIMIT - drawn - 1,
                "msg": f"恭喜中奖: {won['prizeName']}",
            }

    async def _issue_lottery_coupon(self, user_id: int, record_no: str,
                                     prize: dict) -> str:
        """发放抽奖优惠券(券系统未落地前的记录发放, 对齐 P1-9 模式)"""
        import secrets
        from repositories.backend import (
            is_redis_mode, get_redis_client, get_in_memory_store, _k,
        )
        coupon_no = f"CP{secrets.token_hex(5).upper()}"
        record = {
            "couponNo": coupon_no,
            "userId": user_id,
            "recordNo": record_no,
            "amount": float(prize.get("prizeValue", 0)),
            "validDays": 30,
            "source": "lottery",
            "status": "issued",
            "createdAt": datetime.utcnow().isoformat(),
        }
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("activity", "lottery_coupons"),
                              coupon_no, json.dumps(record, ensure_ascii=False))
        else:
            store = get_in_memory_store()
            store.setdefault("activity_lottery_coupons", {})[coupon_no] = record
        return coupon_no

    async def list_my_prizes(self, user_id: int) -> dict:
        """我的奖品(按状态分组)"""
        records = await self.repo.list_prize_records_by_user(user_id)
        grouped: dict = {}
        for r in records:
            grouped.setdefault(r.get("status", "unknown"), []).append(r)
        return {"userId": user_id, "total": len(records), "prizes": records,
                "byStatus": grouped}

    async def deliver_prize(self, record_no: str, waybill_no: str,
                             operator: str = "admin") -> dict:
        """实物奖品发货登记(待发放 → 已发货)

        Raises:
            KeyError: 发奖记录不存在
            ValueError: 状态非法 / 非邮寄类奖品
        """
        from repositories.activity_repository import (
            PRIZE_PRODUCT, PRIZE_BANQUET_WINE, PRIZE_MASCOT,
            PRIZE_STATUS_PENDING, PRIZE_STATUS_SHIPPED,
        )
        record = await self.repo.get_prize_record(record_no)
        if record is None:
            raise KeyError(f"发奖记录不存在(recordNo={record_no})")
        if record.get("status") != PRIZE_STATUS_PENDING:
            raise ValueError(f"记录状态不允许发货(当前: {record.get('status')})")
        shippable = {PRIZE_PRODUCT, PRIZE_BANQUET_WINE, PRIZE_MASCOT}
        if record.get("prizeType") not in shippable:
            raise ValueError("非邮寄类奖品无需发货")
        if not waybill_no:
            raise ValueError("运单号不能为空")

        updates = {"status": PRIZE_STATUS_SHIPPED, "waybillNo": waybill_no,
                   "sentAt": datetime.utcnow().isoformat()}
        await self.repo.update_prize_record(record_no, updates)
        record.update(updates)
        record["operator"] = operator
        logger.info("lottery_prize_shipped record=%s waybill=%s",
                    record_no, waybill_no)
        return record

    async def confirm_prize_received(self, record_no: str,
                                      user_id: int) -> dict:
        """用户签收确认(已发货 → 已签收)

        Raises:
            KeyError: 记录不存在
            ValueError: 状态非法 / 越权确认
        """
        from repositories.activity_repository import (
            PRIZE_STATUS_SHIPPED, PRIZE_STATUS_SIGNED,
        )
        record = await self.repo.get_prize_record(record_no)
        if record is None:
            raise KeyError(f"发奖记录不存在(recordNo={record_no})")
        if record.get("userId") != user_id:
            raise ValueError("仅中奖人可确认签收")
        if record.get("status") != PRIZE_STATUS_SHIPPED:
            raise ValueError(f"记录状态不允许签收(当前: {record.get('status')})")
        updates = {"status": PRIZE_STATUS_SIGNED,
                    "receivedAt": datetime.utcnow().isoformat()}
        await self.repo.update_prize_record(record_no, updates)
        record.update(updates)
        return record
