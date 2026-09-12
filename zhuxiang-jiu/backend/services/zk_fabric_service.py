"""智客·AI智能会员大模型 共享存储与数据织物(zk_fabric_service)

「会员智能运营中枢」底座:
    - _ZkStore: 智客五服务共享存储(内存/Redis 双模式, 镜像 _ZtStore 范式)
    - ZkFabricService: 会员域只读织物——会员档案 + 订单实付额聚合,
      补齐消费画像(订单支付不经 member_service.consume, periodConsume
      不随订单累加; 织物层用「有效订单实付额聚合」补齐, 只读不回写)

铁律: 纯只读聚合 + 零回写既有会员/订单模块 + LLM 禁入判定链;
边界红线: 不计算信值五维(诚信/互助/专业/活跃/成长力)与 TV 资产
(那是 68 号雷达域), 智客只做消费频次/等级生命周期/积分行为/运营决策。
"""

import logging
from datetime import datetime, UTC

from repositories.member_repository import MemberRepository
from repositories.order_repository import OrderRepository

__all__ = [
    "ZK_TABLES", "_ZkStore", "ZkFabricService",
    "_round2", "_now_iso", "_days_since", "order_amount", "status_of",
    "order_paid_at", "VALID_ORDER_STATUSES", "REFUND_ORDER_STATUSES",
]

logger = logging.getLogger(__name__)


def _round2(v) -> float:
    return round(float(v or 0), 2)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _days_since(iso: str) -> float:
    """距今天数(空/非法 → 999=远古, 确定性)"""
    if not (iso or "").strip():
        return 999.0
    try:
        dt = datetime.fromisoformat(str(iso))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - dt).total_seconds() / 86400.0)
    except (TypeError, ValueError):
        return 999.0


# 有效订单状态(计入消费口径: 已支付未退款)
VALID_ORDER_STATUSES = ("PAID", "SHIPPED", "RECEIVED", "COMPLETED")
# 退款口径状态
REFUND_ORDER_STATUSES = ("RETURNING", "REFUNDED")


def order_amount(order: dict) -> float:
    """订单实付额(priceDetail.actualAmount, 缺失诚实归零)"""
    detail = order.get("priceDetail") or {}
    return float(detail.get("actualAmount") or 0)


def status_of(member: dict) -> int:
    """会员状态(None/空安全; 0=禁用, 1=正常)"""
    v = member.get("status", 1)
    return 1 if v is None or v == "" else int(v)


def order_paid_at(order: dict) -> str:
    """订单支付时间(paidAt 优先, createdAt 兜底)"""
    payment = order.get("payment") or {}
    return payment.get("paidAt") or order.get("createdAt") or ""


ZK_TABLES = (
    "zk_healths",        # 会员健康度(五维, id=memberId)
    "zk_portraits",      # RFM 画像分层(id=memberId)
    "zk_churns",         # 流失预警记录(id=memberId)
    "zk_ltvs",           # LTV 预测记录(id=memberId)
    "zk_sandboxes",      # 等级沙盘推演留痕
    "zk_wakeups",        # 沉睡唤醒建议书
    "zk_feedbacks",      # 反馈闭环留痕
    "zk_memos",          # 决策备忘录
    "zk_params",         # 可学习参数(单记录 "params")
)


class _ZkStore:
    """智客大模型共享表存储(镜像 _ZtStore 范式)

    Redis 键: zhuxiang:zk:{table}:{id} / zhuxiang:zk:seq:{entity}
    """

    def __init__(self):
        from repositories.backend import (
            is_redis_mode, get_redis_client, get_in_memory_store)
        self._is_redis = is_redis_mode
        self._get_redis = get_redis_client
        self._store = get_in_memory_store()
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        for table in ZK_TABLES:
            self._store.setdefault(f"zk_{table}", {})

    async def next_id(self, entity: str) -> int:
        if self._is_redis():
            client = await self._get_redis()
            return await client.incr(f"zhuxiang:zk:seq:{entity}")
        key = f"zk_seq_{entity}"
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    async def save(self, table: str, record_id, record: dict) -> None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            await client.set(f"zhuxiang:zk:{table}:{record_id}",
                             _json.dumps(record, ensure_ascii=False))
        else:
            self._ensure_tables()
            self._store[f"zk_{table}"][record_id] = record

    async def get(self, table: str, record_id) -> dict | None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            raw = await client.get(f"zhuxiang:zk:{table}:{record_id}")
            return _json.loads(raw) if raw else None
        self._ensure_tables()
        return self._store[f"zk_{table}"].get(record_id)

    async def list(self, table: str) -> list[dict]:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            rows = []
            async for key in client.scan_iter(
                    match=f"zhuxiang:zk:{table}:*"):
                skey = key.decode() if isinstance(key, bytes) else key
                if ":seq:" in skey:
                    continue
                raw = await client.get(skey)
                if raw:
                    try:
                        rows.append(_json.loads(raw))
                    except (ValueError, TypeError):
                        continue
            return rows
        self._ensure_tables()
        return list(self._store[f"zk_{table}"].values())


class ZkFabricService:
    """会员域数据织物: 会员档案 + 订单实付额只读聚合"""

    def __init__(self, store: _ZkStore = None,
                 member_repo: MemberRepository = None,
                 order_repo: OrderRepository = None):
        self.store = store or _ZkStore()
        self.member_repo = member_repo or MemberRepository()
        self.order_repo = order_repo or OrderRepository()

    # ============================================================
    # 基础读取(只读)
    # ============================================================

    async def members(self, limit: int = 500) -> list[dict]:
        """全量会员(空库诚实返回 [])"""
        rows = await self.member_repo.list_all()
        return rows[:max(1, int(limit))]

    async def member_orders(self, member_id) -> list[dict]:
        """指定会员订单(按创建时间倒序, 无单返回 [])"""
        return await self.order_repo.get_by_member(member_id)

    async def all_orders(self) -> list[dict]:
        """全量订单(织物聚合源)"""
        return await self.order_repo.list_all()

    async def get_member(self, member_id) -> dict:
        """按 ID 取会员

        Raises:
            KeyError: 会员不存在(路由转 404)
        """
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")
        return member

    @staticmethod
    def valid_orders(orders: list[dict]) -> list[dict]:
        """有效订单(已支付未退款)"""
        return [o for o in orders
                if o.get("status") in VALID_ORDER_STATUSES]

    # ============================================================
    # 总览统计(确定性聚合)
    # ============================================================

    async def overview(self) -> dict:
        """会员总览: 量/等级分布/状态分布/消费/积分/注册时序"""
        members = await self.members()
        orders = await self.all_orders()
        valid = self.valid_orders(orders)

        level_dist = {lv: 0 for lv in (1, 2, 3, 4, 5)}
        status_dist = {"active": 0, "disabled": 0}
        reg_series: dict[str, int] = {}
        points_total = 0
        for m in members:
            level_dist[int(m.get("level", 1) or 1)] += 1
            if status_of(m) == 0:
                status_dist["disabled"] += 1
            else:
                status_dist["active"] += 1
            points_total += int(m.get("points", 0) or 0)
            day = str(m.get("created_at") or "")[:10]
            if day:
                reg_series[day] = reg_series.get(day, 0) + 1

        total_consume = _round2(sum(order_amount(o) for o in valid))
        valid_count = len(valid)
        avg_consume = (_round2(total_consume / valid_count)
                       if valid_count else 0.0)
        return {
            "memberTotal": len(members),
            "levelDistribution": level_dist,
            "statusDistribution": status_dist,
            "orderTotal": len(orders),
            "validOrderTotal": valid_count,
            "totalConsume": total_consume,
            "avgConsume": avg_consume,
            "pointsTotal": points_total,
            "registrationSeries": [
                {"date": d, "count": reg_series[d]}
                for d in sorted(reg_series)
            ],
            "consumeScope": "有效订单实付额(PAID/SHIPPED/RECEIVED/"
                            "COMPLETED, priceDetail.actualAmount 聚合)",
            "aggregatedAt": _now_iso(),
        }

    # ============================================================
    # 单会员全景(档案 + 订单聚合)
    # ============================================================

    async def member_detail(self, member_id) -> dict:
        """单会员全景: 档案 + 消费聚合(单量/总额/客单价/最近消费/退款)

        Raises:
            KeyError: 会员不存在(路由转 404)
        """
        member = await self.get_member(member_id)
        orders = await self.member_orders(member_id)
        valid = self.valid_orders(orders)
        refunds = [o for o in orders
                   if o.get("status") in REFUND_ORDER_STATUSES]

        total_consume = _round2(sum(order_amount(o) for o in valid))
        order_count = len(valid)
        avg_price = (_round2(total_consume / order_count)
                     if order_count else 0.0)
        last_paid = max((order_paid_at(o) for o in valid), default="")
        days_since_consume = (_round2(_days_since(last_paid))
                              if last_paid else None)
        return {
            "memberId": member_id,
            "profile": {
                "phone": member.get("phone", ""),
                "nickname": member.get("nickname", ""),
                "level": int(member.get("level", 1) or 1),
                "growthValue": int(member.get("growth_value", 0) or 0),
                "points": int(member.get("points", 0) or 0),
                "status": int(member.get("status", 1) or 1),
                "regSource": member.get("reg_source", ""),
                "createdAt": member.get("created_at", ""),
                "lastLoginAt": member.get("last_login_at", ""),
            },
            "orderAggregation": {
                "orderCount": order_count,
                "totalConsume": total_consume,
                "avgOrderPrice": avg_price,
                "lastConsumeAt": last_paid,
                "daysSinceConsume": days_since_consume,
                "refundCount": len(refunds),
                "refundAmount": _round2(
                    sum(order_amount(o) for o in refunds)),
            },
            "formula": ("总消费=Σ有效订单实付额; 客单价=总消费/有效单量; "
                        "最近消费日=有效单最大支付时间(paidAt 缺失取 "
                        "createdAt)"),
            "aggregatedAt": _now_iso(),
        }
