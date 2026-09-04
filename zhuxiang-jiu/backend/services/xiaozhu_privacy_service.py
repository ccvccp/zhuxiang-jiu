"""49号·小竹可信函数调用深化 P2 隐私预算

计划(docs/49号_小竹可信函数调用深化实施计划.md §六 P2):
    voice48_privacy_budget 表: 会员日预算(默认 1.0)+
    偏好(0.5-2.0)+当日累计+dayKey 日切重置

铁律③ 隐私预算感知:
    - 工具描述声明 privacy_cost; FC 网关累计扣减(原子)
    - 超限 → 429 语义话术("隐私预算不足(剩余 X, 需 Y)——
      请在设置中调整隐私偏好或明日再试")
    - 只读零成本工具永不降级(零成本不检查不扣减)

预算均等红线(计划 §三 3.2):
    预算只按用户自主偏好分级, 绝不与信值等级挂钩
    (46号公平性桥接审计口径)。
"""

import logging
from datetime import UTC, datetime

from core.helpers import ts

from repositories.xiaozhu_repository import (
    Xiaozhu48Repository,
)

logger = logging.getLogger("xiaozhu_privacy")

# 默认日预算与偏好边界(计划 §六 P2)
DEFAULT_DAILY_BUDGET = 1.0
PREFERENCE_MIN = 0.5
PREFERENCE_MAX = 2.0


def _today_key() -> str:
    """日切键(UTC 日期——预算按自然日重置)"""
    return datetime.now(UTC).strftime("%Y-%m-%d")


class XiaozhuPrivacyService:
    """隐私预算(查询/扣减/偏好调整——会员自主)"""

    def __init__(self,
                 repo: Xiaozhu48Repository = None):
        self.repo = repo or Xiaozhu48Repository()

    # --------------------------------------------------------
    # 账户
    # --------------------------------------------------------

    async def _account(self, member_id: int) -> dict:
        """取或建预算账户(日切重置: dayKey 非今日 → 清零)"""
        rec = await self.repo.get_privacy_budget(member_id)
        today = _today_key()
        if rec is None:
            rec = {
                "memberId": member_id,
                "dailyBudget": DEFAULT_DAILY_BUDGET,
                "preference": 1.0,
                "usedToday": 0.0,
                "dayKey": today,
                "history": [],
                "ts": ts(),
            }
        elif rec.get("dayKey") != today:
            # 日切: 累计入史(留 7 日) → 清零
            history = list(rec.get("history") or [])
            if rec.get("usedToday"):
                history.append(
                    {"dayKey": rec.get("dayKey"),
                     "used": round(float(
                         rec.get("usedToday") or 0), 2)})
                history = history[-7:]
            rec.update({"usedToday": 0.0,
                        "dayKey": today,
                        "history": history, "ts": ts()})
        return rec

    async def budget_view(self,
                          member_id: int) -> dict:
        """预算视图(余额/偏好/近 7 日消耗——"我的隐私预算")"""
        rec = await self._account(member_id)
        # 惰性持久化(日切后的新账户态)
        await self.repo.save_privacy_budget(rec)
        limit = self._effective_limit(rec)
        remaining = round(max(0.0, limit
                              - float(rec.get("usedToday")
                                       or 0)), 2)
        return {
            "success": True,
            "memberId": member_id,
            "dailyBudget": float(
                rec.get("dailyBudget")
                or DEFAULT_DAILY_BUDGET),
            "preference": float(
                rec.get("preference") or 1.0),
            "effectiveLimit": round(limit, 2),
            "usedToday": round(float(
                rec.get("usedToday") or 0), 2),
            "remaining": remaining,
            "history": rec.get("history") or [],
            "dayKey": rec.get("dayKey"),
            "note": "预算只按您的自主偏好分级(与信值等级"
                    "无关); 只读工具零成本永不降级",
        }

    @staticmethod
    def _effective_limit(rec: dict) -> float:
        """实际限额 = 默认日预算 × 偏好"""
        return (float(rec.get("dailyBudget")
                      or DEFAULT_DAILY_BUDGET)
                * float(rec.get("preference") or 1.0))

    # --------------------------------------------------------
    # 扣减(原子——网关管道内调用)
    # --------------------------------------------------------

    async def check_and_spend(self, member_id: int,
                              cost: float) -> dict:
        """预算检查+原子扣减(失败抛 ValueError——429 语义)

        只读零成本(cost==0)不检查不扣减——永不降级红线。
        """
        cost = round(float(cost or 0), 2)
        if cost <= 0:
            return {"spent": 0.0, "remaining": None,
                    "zeroCost": True}
        rec = await self._account(member_id)
        limit = self._effective_limit(rec)
        used = float(rec.get("usedToday") or 0)
        remaining = round(limit - used, 2)
        if cost > remaining:
            raise ValueError(
                f"隐私预算不足(剩余 {remaining:.2f}, "
                f"需 {cost:.2f})——请在设置中调整隐私"
                f"偏好或明日再试")
        rec["usedToday"] = round(used + cost, 2)
        rec["ts"] = ts()
        await self.repo.save_privacy_budget(rec)
        logger.info("voice49_privacy_spent member=%s "
                    "cost=%s used=%s/%s", member_id, cost,
                    rec["usedToday"], round(limit, 2))
        return {"spent": cost,
                "remaining": round(
                    limit - rec["usedToday"], 2),
                "zeroCost": False}

    # --------------------------------------------------------
    # 偏好调整(会员自主——预算均等红线)
    # --------------------------------------------------------

    async def set_preference(self, member_id: int,
                             preference: float) -> dict:
        """调整偏好(0.5-2.0——用户自主, 与信值等级无关)"""
        try:
            pref = round(float(preference), 2)
        except (TypeError, ValueError):
            raise ValueError("偏好需为数值") from None
        if not PREFERENCE_MIN <= pref <= PREFERENCE_MAX:
            raise ValueError(
                f"偏好需在 {PREFERENCE_MIN}-{PREFERENCE_MAX}"
                f" 区间(当前 {pref})")
        rec = await self._account(member_id)
        rec["preference"] = pref
        rec["ts"] = ts()
        await self.repo.save_privacy_budget(rec)
        logger.info("voice49_privacy_pref member=%s "
                    "pref=%s", member_id, pref)
        return await self.budget_view(member_id)
