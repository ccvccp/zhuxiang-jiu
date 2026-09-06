"""64号·信值兑换管理 积分兑换管道
(xx64_points_service, P1)

计划(docs/64号_信值兑换商品服务AI智能管理模块
实施计划.md §4.1/§八 P1):
    R6 积分入口:
        - 100 积分 = 1 信值(整数倍)
        - T+1 冻结观察(frozen——
          次日入可用; 防即时套利
          冲击)
        - 每日 ≤3 次限频

管道三态:
    pending(冻结观察中)
    → credited(T+1 入账)
    / cancelled(观察期内取消)

铁律:
    - 积分账户扣减先行(不足拒绝)
    - 积分流水留痕(兑换消耗)
    - 兑换所得信值冻结期不参与
      兑换余额(R6 观察——防套利)
"""

import logging
import os
from datetime import datetime, UTC, \
    timedelta

from core.helpers import ts

from repositories.xx64_repository import (
    Xx64Repository,
)

logger = logging.getLogger("xx64_points")

MODEL_VERSION = "v1-xx64-points"

SCORER_ID = "value_exchange"

# 兑换三态
EXCHANGE_STATES = (
    "pending",    # 冻结观察中
    "credited",   # T+1 已入账
    "cancelled",  # 观察期取消
)


def current_mode() -> str:
    """模块开关(XX64_MODE——同底座)"""
    return os.environ.get(
        "XX64_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"XX64_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


class Xx64PointsService:
    """64号积分兑换管道(P1——
    100:1+T+1 冻结+日限频)"""

    def __init__(self):
        self.repo = Xx64Repository()

    # ============================================================
    # ① 积分→信值兑换
    # ============================================================

    async def exchange(self,
                      user_id: int,
                      trust_id: int,
                      points: int,
                      exchanged_by: str = "member"
                      ) -> dict:
        """积分→信值兑换(100:1 整数倍
        ——冻结观察 pending)

        Raises:
            ValueError: off 态/积分
                非正/非整数倍/日限频/
                积分不足
            KeyError: 积分账户不存在
        """
        require_active_mode()
        from services.xx64_registry import (
            POINTS_DAILY_LIMIT,
            POINTS_FROZEN_HOURS,
            POINTS_PER_TRUST,
            points_to_trust,
        )
        user_id = int(user_id or 0)
        trust_id = int(trust_id or 0)
        points = int(points or 0)
        if user_id <= 0 or trust_id <= 0:
            raise ValueError(
                "userId/trustId 必填")
        if points <= 0:
            raise ValueError(
                "兑换积分须为正")
        trust_gain = points_to_trust(
            points)  # 非 100 倍数抛错

        # P3 风控同步前置(PTS-SHOCK
        # ——assist 态量级 high 拦截当笔
        # 可申诉秒级复核; shadow 仅观察)
        from services.xx64_risk_service import (
            Xx64RiskService,
        )
        gate = await Xx64RiskService() \
            .sync_gate_exchange(
                user_id, trust_id)
        if gate["blocked"]:
            raise ValueError(
                f"风控拦截(风险事件 "
                f"{gate['riskId']}"
                f"——积分冲击量级命中; "
                f"可经申诉通道秒级复核)")

        # 日限频(当日有效兑换计数
        # ——cancelled 不占频次)
        today = datetime.now(UTC) \
            .strftime("%Y-%m-%d")
        records = await self.repo \
            .list_exchanges(
                user_id=user_id,
                limit=50)
        today_count = sum(
            1 for r in records
            if str(r.get("createdAt")
                   or "").startswith(today)
            and r.get("status") in (
                "pending", "credited"))
        if today_count \
                >= POINTS_DAILY_LIMIT:
            raise ValueError(
                f"今日已兑换 {today_count} 次"
                f"(日限频 {POINTS_DAILY_LIMIT}"
                f"——R6 防高频套利)")

        # 积分账户扣减先行
        from repositories.points_repository import (
            PointsRepository,
        )
        repo_points = PointsRepository()
        account = await \
            repo_points.get_account(
                user_id)
        if account is None:
            raise KeyError(
                f"积分账户 {user_id} 不存在"
                f"(先获取积分)")
        balance = int(
            account.get("totalPoints")
            or 0)
        if balance < points:
            raise ValueError(
                f"积分不足(余额 {balance} "
                f"< 兑换 {points})")

        # 扣减+流水
        account.update({
            "totalPoints":
                balance - points,
            "totalSpent": int(
                account.get(
                    "totalSpent") or 0)
            + points,
            "version": int(
                account.get("version")
                or 0) + 1,
            "updatedAt": ts()})
        await repo_points.save_account(
            account)
        log_id = await \
            repo_points.next_log_id()
        await repo_points.add_log({
            "id": log_id,
            "userId": user_id,
            "source": "xx64_exchange",
            "points": -points,
            "status": 1,
            "remark": f"64号积分兑换信值"
                      f"({points}→"
                      f"{trust_gain})",
            "createdAt": ts(),
        })

        # 冻结观察记录
        release_at = (
            datetime.now(UTC)
            + timedelta(
                hours=POINTS_FROZEN_HOURS)
        ).isoformat()
        exchange_id = await \
            self.repo.next_exchange_id()
        record = {
            "exchangeId": exchange_id,
            "buyerId": user_id,
            "trustId": trust_id,
            "points": points,
            "pointsValue": trust_gain,
            "exchangeRate":
                1 / POINTS_PER_TRUST,
            "status": "pending",
            "frozenHours":
                POINTS_FROZEN_HOURS,
            "releaseAt": release_at,
            "exchangedBy": str(
                exchanged_by or "member"),
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_exchange(
            record)
        await self._track("points", {
            "action": "exchange",
            "exchangeId": exchange_id,
            "userId": user_id,
            "trustId": trust_id,
            "points": points,
            "pointsValue": trust_gain,
            "releaseAt": release_at,
        })
        return {
            "success": True,
            "exchangeId": exchange_id,
            "status": "pending",
            "points": points,
            "pointsValue": trust_gain,
            "releaseAt": release_at,
            "note": f"积分 {points} 已兑换"
                    f" {trust_gain} 信值——"
                    f"冻结观察 "
                    f"{POINTS_FROZEN_HOURS}h"
                    f"(R6 防即时套利)",
            "createdAt": record[
                "createdAt"],
        }

    # ============================================================
    # ② T+1 入账(到期批量结算)
    # ============================================================

    async def settle_pending(self) -> dict:
        """到期兑换入账(pending→
        credited——冻结期届满)

        不受开关影响(结算管理面)。
        """
        records = await self.repo \
            .list_exchanges(limit=200)
        now_iso = ts()
        settled = 0
        for r in records:
            if r.get("status") \
                    != "pending":
                continue
            release_at = str(
                r.get("releaseAt") or "")
            if release_at and \
                    release_at > now_iso:
                continue
            r.update({
                "status": "credited",
                "creditedAt": now_iso,
                "updatedAt": now_iso})
            await self.repo.save_exchange(
                r, create=False)
            settled += 1
        await self._track("points", {
            "action": "settle_batch",
            "settled": settled,
        })
        return {
            "success": True,
            "settled": settled,
            "note": "T+1 冻结到期入账完成"
                    "(pending→credited)",
            "settledAt": now_iso,
        }

    # ============================================================
    # ③ 观察期取消
    # ============================================================

    async def cancel_exchange(self,
                              exchange_id: int,
                              cancelled_by: str = "member"
                              ) -> dict:
        """观察期取消(pending→
        cancelled+积分返还)

        Raises:
            KeyError: 兑换不存在
            ValueError: 状态机拒绝
        """
        record = await self.repo \
            .get_exchange(int(exchange_id))
        if not record:
            raise KeyError(
                f"兑换 {exchange_id} 不存在")
        if record.get("status") \
                != "pending":
            raise ValueError(
                f"兑换状态 "
                f"{record.get('status')} "
                f"不可取消(须 pending)")

        # 积分返还
        user_id = int(
            record.get("buyerId") or 0)
        points = int(
            record.get("points") or 0)
        from repositories.points_repository import (
            PointsRepository,
        )
        repo_points = PointsRepository()
        account = await \
            repo_points.get_or_create_account(
                user_id)
        account.update({
            "totalPoints":
                int(account.get(
                    "totalPoints") or 0)
                + points,
            "totalEarned": int(
                account.get(
                    "totalEarned") or 0)
            + points,
            "version": int(
                account.get("version")
                or 0) + 1,
            "updatedAt": ts()})
        await repo_points.save_account(
            account)
        log_id = await \
            repo_points.next_log_id()
        await repo_points.add_log({
            "id": log_id,
            "userId": user_id,
            "source":
                "xx64_exchange_cancel",
            "points": points,
            "status": 1,
            "remark": f"64号兑换取消返还"
                      f"({points})",
            "createdAt": ts(),
        })

        record.update({
            "status": "cancelled",
            "cancelledBy": str(
                cancelled_by
                or "member"),
            "cancelledAt": ts(),
            "updatedAt": ts()})
        await self.repo.save_exchange(
            record, create=False)
        await self._track("points", {
            "action": "cancel",
            "exchangeId":
                int(exchange_id),
            "points": points,
            "cancelledBy":
                cancelled_by,
        })
        return {
            "success": True,
            "exchangeId":
                int(exchange_id),
            "status": "cancelled",
            "refundedPoints": points,
            "note": "观察期取消——积分已"
                    "全额返还",
            "cancelledAt": ts(),
        }

    # ============================================================
    # ④ 换算预览(观测面)
    # ============================================================

    async def preview(self,
                     trust_id: int,
                     needed_trust: float = None
                     ) -> dict:
        """换算预览(积分余额→可兑
        信值+缺口换算——观测面)"""
        from services.xx64_registry import (
            POINTS_PER_TRUST,
        )
        # 按档案域统计(冻结观察中信值
        # 不计入可用——按 trustId 过滤)
        records = await self.repo \
            .list_exchanges(limit=200)
        records = [
            r for r in records
            if int(r.get("trustId")
                   or 0) == int(trust_id)]
        pending = sum(
            float(r.get("pointsValue")
                  or 0)
            for r in records
            if r.get("status")
            == "pending")
        credited = sum(
            float(r.get("pointsValue")
                  or 0)
            for r in records
            if r.get("status")
            == "credited")
        return {
            "success": True,
            "trustId": int(trust_id),
            "rate": f"1 信值 = "
                    f"{POINTS_PER_TRUST}"
                    f" 积分",
            "pendingValue": round(
                pending, 2),
            "creditedValue": round(
                credited, 2),
            "neededPoints": round(
                (needed_trust or 0)
                * POINTS_PER_TRUST, 0)
            if needed_trust else None,
            "note": "换算预览——100:1"
                    "整数倍; 冻结期信值"
                    "不参与兑换余额",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "orderId": int(
                    detail.get(
                        "exchangeId")
                    or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "xx64_track_failed %s: %s",
                event_type, exc)
