"""64号·信值兑换管理 订单底座服务
(xx64_service, P0)

计划(docs/64号_信值兑换商品服务AI智能管理模块
实施计划.md §4.1/§八 P0):
    ① 订单创建+预校验四查(R1 结构/
       R4 单次/R5 窗口/R7 非负)
    ② 锁值先行(reserved——冻结信值
       防并发双花)
    ③ 限额基准快照机制(窗口累计比对
       基准取窗口内最大余额快照——
       防拆单压基数)
    ④ 订单九态状态机(initiated
       →prechecked→reserved→…)
    ⑤ 取消(解锁)

铁律(计划 §二/§八):
    - 刚性规则服务端强制校验
      (前端跳过无效)
    - 余额快照随流水落库可审计
    - 负信值账户禁止兑换
    - 信值余额经 45号档案 score
      纯读取(两账分离——64号维护
      消费转移账本)

信值余额口径(P0):
    45号档案 score(0-100 信值分)
    ×信值单位换算(1 分=1 信值,
    简化口径——P4 锚定层复核)。
"""

import hashlib
import logging
import os

from core.helpers import ts

from repositories.xx64_repository import (
    Xx64Repository,
)

logger = logging.getLogger("xx64_service")

MODEL_VERSION = "v1-xx64-service"

SCORER_ID = "value_exchange"

# 余额换算(45号 score → 信值单位)
TRUST_UNIT_PER_SCORE = 1.0


def current_mode() -> str:
    """模块开关(XX64_MODE, 默认 off)"""
    return os.environ.get(
        "XX64_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"XX64_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


def _fingerprint(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:32]


async def get_trust_balance(trust_id: int
                           ) -> dict:
    """信值余额读取(45号档案 score
    纯读取——两账分离)

    Returns:
        {balance, source, profile}
    Raises:
        KeyError: 档案不存在
    """
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    profile = await (
        TrustValue45Repository()
        .get_profile(int(trust_id)))
    if not profile:
        raise KeyError(
            f"45号信值档案 {trust_id} 不存在"
            f"(先建档)")
    score = float(
        profile.get("score") or 0)
    if profile.get("frozen"):
        raise ValueError(
            f"45号档案 {trust_id} 已冻结"
            f"(禁止兑换)")
    return {
        "balance": round(
            score * TRUST_UNIT_PER_SCORE, 2),
        "score": score,
        "grade": profile.get("grade"),
        "source": "trust45",
    }


class Xx64Service:
    """64号订单底座(P0——预校验+
    锁值+九态状态机)"""

    def __init__(self):
        self.repo = Xx64Repository()

    # ============================================================
    # 观测面
    # ============================================================

    @staticmethod
    def registry() -> dict:
        """刚性规则自描述(观测面
        不受开关影响)"""
        from services.xx64_registry import (
            registry_view,
        )
        view = registry_view()
        view.update({
            "scorer": {
                "scorerId": SCORER_ID,
                "factors": 8,
                "decisions": ("observe",
                               "optimize",
                               "urgent"),
            },
            "note": "P0 底座: 刚性规则"
                    "R1-R7+订单状态机+预校验"
                    "+锁值(P1 支付结算"
                    "引擎完整交付)",
        })
        return view

    # ============================================================
    # 预校验(刚性四查——服务端强制)
    # ============================================================

    async def precheck(self, trust_id: int,
                       price: float
                       ) -> dict:
        """预校验四查(不落库——纯计算;
        创建订单时内联调用)

        四查:
            R1_STRUCT: 30% 结构可达
                (余额 ≥ 价格×30%)
            R4_SINGLE: 单次 ≤ 余额×20%
            R5_WINDOW: 窗口累计 ≤
                最大快照×40%
            R7_NONNEG: 余额非负(45号
                档案存在且未冻结)

        Returns:
            {passed, checks, trustValue,
             cashValue, singleQuota,
             windowUsed, cumulativeQuota,
             balanceSnapshot}
        Raises:
            KeyError: 45号档案不存在
            ValueError: 价格非法
        """
        price = float(price or 0)
        if price <= 0:
            raise ValueError(
                "商品/服务价格须为正")

        from services.xx64_registry import (
            CASH_PORTION,
            WINDOW_DAYS,
            cumulative_quota,
            single_quota,
            trust_portion,
        )
        # 45号余额(快照基准)
        bal = await get_trust_balance(
            trust_id)
        balance = bal["balance"]
        trust_value = trust_portion(price)
        cash_value = round(
            price * CASH_PORTION, 2)
        s_quota = single_quota(balance)

        # 窗口累计(30 日滚动——
        # 已 reserve/pay 订单信值额)
        window_used, max_snapshot = \
            await self._window_usage(
                trust_id, balance)

        checks = {
            "R7_NONNEG": balance >= 0,
            "R1_STRUCT":
                balance >= trust_value,
            "R4_SINGLE":
                trust_value <= s_quota,
            "R5_WINDOW":
                window_used + trust_value
                <= cumulative_quota(
                    max_snapshot),
        }
        passed = all(checks.values())
        return {
            "passed": passed,
            "checks": checks,
            "price": round(price, 2),
            "trustValue": trust_value,
            "cashValue": cash_value,
            "balance": balance,
            "balanceSnapshot": balance,
            "singleQuota": s_quota,
            "windowUsed": window_used,
            "cumulativeQuota":
                cumulative_quota(
                    max_snapshot),
            "windowMaxSnapshot":
                max_snapshot,
            "windowDays": WINDOW_DAYS,
            "frozen": not passed,
            "note": "预校验四查——"
                    + ("全部通过" if passed
                       else "存在不满足项"
                       "(见 checks)"),
        }

    async def _window_usage(self,
                            trust_id: int,
                            current_balance: float
                            ) -> tuple:
        """窗口用量+最大快照(滚动
        30 日——基准快照机制)"""
        from datetime import datetime, UTC, \
            timedelta
        from services.xx64_registry import (
            WINDOW_DAYS,
        )
        cutoff = (
            datetime.now(UTC)
            - timedelta(days=WINDOW_DAYS)
        ).isoformat()
        orders = await self.repo.list_orders(
            buyer_id=None,  # 全量过滤
            limit=500)
        window_orders = [
            o for o in orders
            if int(o.get("trustId") or 0)
            == int(trust_id)
            and str(o.get("createdAt")
                    or "") >= cutoff
            and o.get("status") in (
                "reserved", "paid",
                "settled", "completed")]
        used = sum(
            float(o.get("trustValue") or 0)
            for o in window_orders)
        snapshots = [
            float(o.get(
                "balanceSnapshot") or 0)
            for o in window_orders]
        snapshots.append(
            float(current_balance))
        max_snapshot = max(snapshots)
        return (round(used, 2),
                round(max_snapshot, 2))

    # ============================================================
    # 订单创建+预校验+锁值
    # ============================================================

    async def create_order(self,
                          buyer_id: int,
                          seller_id: int,
                          trust_id: int,
                          price: float,
                          product: str = "",
                          use_trust: bool = True,
                          created_by: str = "member"
                          ) -> dict:
        """创建订单(预校验四查→
        prechecked→锁值 reserved)

        状态机: initiated→prechecked
        →reserved(锁值成功) /
        blocked(预校验不满足)
        (blocked 不入状态机——
        以 initiated+precheck 留痕)

        Raises:
            KeyError: 45号档案不存在
            ValueError: off 态/参数
                非法/预校验不满足
        """
        require_active_mode()
        buyer_id = int(buyer_id or 0)
        seller_id = int(seller_id or 0)
        trust_id = int(trust_id or 0)
        if buyer_id <= 0 or seller_id <= 0:
            raise ValueError(
                "买方/卖方 memberId 必填")
        if buyer_id == seller_id:
            raise ValueError(
                "买方卖方不可同一主体"
                "(自买自卖无效)")
        if not use_trust:
            raise ValueError(
                "P0 仅支持信值支付订单"
                "(纯现付订单不经 64号)")

        check = await self.precheck(
            trust_id, price)
        if not check["passed"]:
            failed = [
                k for k, v in
                check["checks"].items()
                if not v]
            raise ValueError(
                f"预校验不满足: "
                f"{'/'.join(failed)}——"
                f"信值 {check['trustValue']}"
                f"/余额 {check['balance']}"
                f"/单次上限 "
                f"{check['singleQuota']}"
                f"/窗口已用 "
                f"{check['windowUsed']}"
                f"/窗口累计上限 "
                f"{check['cumulativeQuota']}")

        from services.xx64_registry import (
            ORDER_STATES,
        )
        order_id = await \
            self.repo.next_order_id()
        fingerprint = _fingerprint(
            order_id, buyer_id,
            seller_id, price)
        record = {
            "orderId": order_id,
            "buyerId": buyer_id,
            "sellerId": seller_id,
            "trustId": trust_id,
            "product": str(product or
                           "未命名商品/服务"),
            "price": round(float(price), 2),
            "trustValue":
                check["trustValue"],
            "cashValue":
                check["cashValue"],
            "balanceSnapshot":
                check["balance"],
            "singleQuota":
                check["singleQuota"],
            "windowUsedAtCreation":
                check["windowUsed"],
            "cumulativeQuotaAtCreation":
                check["cumulativeQuota"],
            "status": "reserved",
            "exclusive": True,
            "reserved": True,
            "precheck": check,
            "fingerprint": fingerprint,
            "createdBy": str(
                created_by or "member"),
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_order(record)
        await self._track("order", {
            "action": "create_reserve",
            "orderId": order_id,
            "buyerId": buyer_id,
            "sellerId": seller_id,
            "trustId": trust_id,
            "trustValue":
                check["trustValue"],
            "balanceSnapshot":
                check["balance"],
            "checks": check["checks"],
            "createdBy": created_by,
        })
        return {
            "success": True,
            "orderId": order_id,
            "status": "reserved",
            "price": record["price"],
            "trustValue":
                record["trustValue"],
            "cashValue":
                record["cashValue"],
            "balanceSnapshot":
                record["balanceSnapshot"],
            "singleQuota":
                record["singleQuota"],
            "exclusive": True,
            "precheck": check,
            "fingerprint": fingerprint,
            "note": "订单已创建+信值已锁"
                    "(reserved)——30% 信值"
                    "+70% 现付; 整单互斥"
                    "其他优惠(P1 支付"
                    "原子转移接管)",
            "createdAt": record["createdAt"],
        }

    # ============================================================
    # 取消(解锁)
    # ============================================================

    async def cancel_order(self,
                          order_id: int,
                          cancelled_by: str = "member"
                          ) -> dict:
        """取消订单(解锁信值——
        cancelled)

        状态机: reserved→cancelled
        (锁值释放——窗口用量回退)

        Raises:
            KeyError: 订单不存在
            ValueError: off 态/状态机拒绝
        """
        require_active_mode()
        order = await self.repo.get_order(
            int(order_id))
        if not order:
            raise KeyError(
                f"订单 {order_id} 不存在")
        from services.xx64_registry import (
            ORDER_TRANSITIONS,
        )
        if "cancelled" not in \
                ORDER_TRANSITIONS.get(
                    order.get("status"), ()):
            raise ValueError(
                f"订单状态 "
                f"{order.get('status')} "
                f"不可取消(合法迁移: "
                f"{'/'.join(
                    ORDER_TRANSITIONS.get(
                        order.get(
                            'status'),
                        ()))})")

        order.update({
            "status": "cancelled",
            "reserved": False,
            "cancelledBy": str(
                cancelled_by or "member"),
            "updatedAt": ts()})
        await self.repo.save_order(
            order, create=False)
        await self._track("order", {
            "action": "cancel_release",
            "orderId": int(order_id),
            "trustValue": order.get(
                "trustValue"),
            "cancelledBy": cancelled_by,
        })
        return {
            "success": True,
            "orderId": int(order_id),
            "status": "cancelled",
            "released":
                order.get("trustValue"),
            "note": "订单已取消——信值"
                    "锁值释放(窗口用量回退)",
            "cancelledAt": ts(),
        }

    # ============================================================
    # 观测面(订单/限额)
    # ============================================================

    async def get_order(self, order_id: int
                       ) -> dict:
        """订单详情(观测面)

        Raises:
            KeyError: 订单不存在
        """
        order = await self.repo.get_order(
            int(order_id))
        if not order:
            raise KeyError(
                f"订单 {order_id} 不存在")
        return {
            "success": True,
            "order": order,
            "note": "订单详情——九态+限额"
                    "快照+预校验记录",
        }

    async def list_orders(self,
                         buyer_id: int = None,
                         seller_id: int = None,
                         status: str = None,
                         limit: int = 100
                         ) -> dict:
        """订单列表(观测面)"""
        records = await self.repo.list_orders(
            buyer_id=buyer_id,
            seller_id=seller_id,
            status=status,
            limit=int(limit or 100))
        by_status: dict = {}
        for r in records:
            s = r.get("status") or "?"
            by_status[s] = \
                by_status.get(s, 0) + 1
        return {
            "success": True,
            "total": len(records),
            "byStatus": by_status,
            "orders": records,
            "note": "订单列表——九态分布",
        }

    async def quota_status(self,
                          trust_id: int
                          ) -> dict:
        """限额状态(单次/窗口累计
        ——观测面)

        Raises:
            KeyError: 45号档案不存在
        """
        check = await self.precheck(
            trust_id, 0.01)  # 最小价格探针
        from services.xx64_registry import (
            WINDOW_DAYS,
        )
        return {
            "success": True,
            "trustId": int(trust_id),
            "balance": check["balance"],
            "singleQuota":
                check["singleQuota"],
            "windowUsed":
                check["windowUsed"],
            "cumulativeQuota":
                check["cumulativeQuota"],
            "windowRemaining": round(
                max(check[
                        "cumulativeQuota"]
                    - check["windowUsed"],
                    0), 2),
            "windowDays": WINDOW_DAYS,
            "windowMaxSnapshot":
                check["windowMaxSnapshot"],
            "note": "限额状态——单次 20%/"
                    "窗口 40%(基准快照机制)",
        }

    async def model_status(self) -> dict:
        """模型状态(44号 get_weights_view
        复用——第38档案)"""
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(
            SCORER_ID)
        view.update({
            "module": "xx64",
            "mode": current_mode(),
            "scorerId": SCORER_ID,
            "decisions": ["observe",
                          "optimize",
                          "urgent"],
            "note": "44号学习闭环复用——"
                    "第38档案",
        })
        return {"success": True,
                "status": view}

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
                    detail.get("orderId")
                    or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "xx64_track_failed %s: %s",
                event_type, exc)
