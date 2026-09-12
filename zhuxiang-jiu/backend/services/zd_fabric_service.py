"""智单·AI智能订单大模型 数据织物层(zd_fabric_service)

「Data Fabric」——智单共享数据底座(对齐 zy_data_service 编排式
零回写红线): 订单域只读聚合 → 统一总览(九态分布/GMV/退款率/
客单价/履约时效/会员×商品×日时序三维聚合), 供 P0 洞察中枢 /
P1 预测沙盘 / P2 退款裁决 / P3 进化闭环全部 Agent 共享。

口径(与 order_service 状态机一致):
    - 九态: PENDING→PAID→SHIPPED→RECEIVED→COMPLETED 主链 +
      CANCELLED/CLOSED + RETURNING→REFUNDED
    - GMV: 已支付订单 priceDetail.actualAmount 合计(支付口径,
      已退款单计入支付、由退款率单独观测)
    - 履约时长: payment.paidAt → logistics.signedAt 小时差
    - 日时序: createdAt 日聚合(单量 + 当日创建且已支付的 GMV)
    - 商品口径: items.subtotal(折前商品额)

铁律: 纯只读聚合 + 全数字来自查询层 + 空数据诚实零值 +
LLM 禁入判定链。
"""

import logging
from datetime import datetime, UTC

from repositories.order_repository import OrderRepository

logger = logging.getLogger(__name__)

# 订单九态(对齐 order_service 状态机)
ORDER_STATUSES = (
    "PENDING", "PAID", "SHIPPED", "RECEIVED", "COMPLETED",
    "CANCELLED", "CLOSED", "RETURNING", "REFUNDED",
)

# 终态集(体检「终态占比」口径)
TERMINAL_STATUSES = ("COMPLETED", "CANCELLED", "CLOSED", "REFUNDED")

# 智单共享表(键前缀 zhuxiang:zd:)
ZD_TABLES = (
    "checkups",      # 订单健康体检报告
    "portraits",     # 会员×商品×时段画像快照
    "anomalies",     # 异常订单扫描留痕
    "feedbacks",     # 反馈闭环留痕
    "memos",         # 决策备忘录
)


def _round2(v) -> float:
    return round(float(v or 0), 2)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value) -> datetime | None:
    """ISO 时间解析(空/异常返回 None; 朴素时间视为 UTC)"""
    text = str(value or "")
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(v)))


class _ZdStore:
    """智单大模型共享表存储(镜像 _ZtStore 范式)

    Redis 键: zhuxiang:zd:{table}:{id} / zhuxiang:zd:seq:{entity}
    进化参数: zhuxiang:zd:params(单条 JSON)
    """

    def __init__(self):
        from repositories.backend import (
            is_redis_mode, get_redis_client, get_in_memory_store)
        self._is_redis = is_redis_mode
        self._get_redis = get_redis_client
        self._store = get_in_memory_store()
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        for table in ZD_TABLES:
            self._store.setdefault(f"zd_{table}", {})

    async def next_id(self, entity: str) -> int:
        if self._is_redis():
            client = await self._get_redis()
            return await client.incr(f"zhuxiang:zd:seq:{entity}")
        key = f"zd_seq_{entity}"
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    async def save(self, table: str, record_id, record: dict) -> None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            await client.set(f"zhuxiang:zd:{table}:{record_id}",
                             _json.dumps(record, ensure_ascii=False))
        else:
            self._ensure_tables()
            self._store[f"zd_{table}"][record_id] = record

    async def get(self, table: str, record_id) -> dict | None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            raw = await client.get(f"zhuxiang:zd:{table}:{record_id}")
            return _json.loads(raw) if raw else None
        self._ensure_tables()
        return self._store[f"zd_{table}"].get(record_id)

    async def list(self, table: str) -> list[dict]:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            rows = []
            async for key in client.scan_iter(
                    match=f"zhuxiang:zd:{table}:*"):
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
        return list(self._store[f"zd_{table}"].values())

    # ============================================================
    # 进化参数(单条 JSON: zhuxiang:zd:params)
    # ============================================================

    async def get_params(self) -> dict | None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            raw = await client.get("zhuxiang:zd:params")
            return _json.loads(raw) if raw else None
        return self._store.get("zd_params")

    async def save_params(self, params: dict) -> None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            await client.set("zhuxiang:zd:params",
                             _json.dumps(params, ensure_ascii=False))
        else:
            self._store["zd_params"] = params


class ZdFabricService:
    """订单数据织物: 只读聚合 + 统一总览(零回写订单域)"""

    def __init__(self, store: _ZdStore = None):
        self.store = store or _ZdStore()
        self.order_repo = OrderRepository()

    async def orders(self, limit: int = 500) -> list[dict]:
        """订单全量(createdAt 倒序, 上限 limit; 空数据诚实返回 [])"""
        limit = max(1, min(2000, int(limit)))
        rows = await self.order_repo.list_all()
        rows.sort(key=lambda o: (str(o.get("createdAt", "")),
                                 str(o.get("orderId", ""))),
                  reverse=True)
        return rows[:limit]

    async def get_order(self, order_id: str) -> dict:
        """单笔订单(不存在抛 KeyError → 路由层转 404)"""
        order = await self.order_repo.get_by_id(order_id)
        if not order:
            raise KeyError(f"订单 {order_id} 不存在")
        return order

    async def fulfillment_durations(self) -> list[dict]:
        """履约时长样本(paidAt→signedAt 小时差, 按 paidAt 升序)"""
        return self._duration_rows(await self.orders(limit=2000))

    @staticmethod
    def _duration_rows(orders: list[dict]) -> list[dict]:
        rows = []
        for order in orders:
            paid = _parse_iso((order.get("payment") or {}).get("paidAt"))
            signed = _parse_iso(
                (order.get("logistics") or {}).get("signedAt"))
            if not paid or not signed:
                continue
            hours = (signed - paid).total_seconds() / 3600
            if hours < 0:
                continue
            rows.append({"orderId": str(order.get("orderId", "")),
                         "paidAt": paid.isoformat(),
                         "hours": _round2(hours)})
        rows.sort(key=lambda r: (r["paidAt"], r["orderId"]))
        return rows

    async def overview(self) -> dict:
        """织物总览: 总量/九态/GMV/退款率/客单价/履约/三维聚合/日时序

        所有比率 round2; 无数据返回诚实零值(hasData=False)。
        """
        orders = await self.orders()
        total = len(orders)
        status_dist = {s: 0 for s in ORDER_STATUSES}
        paid_count = 0
        gmv = 0.0
        refunded = 0
        members: dict = {}
        products: dict = {}
        daily: dict = {}
        for order in orders:
            status = str(order.get("status", ""))
            if status in status_dist:
                status_dist[status] += 1
            if status == "REFUNDED":
                refunded += 1
            amount = _round2(
                (order.get("priceDetail") or {}).get("actualAmount"))
            paid = _parse_iso((order.get("payment") or {}).get("paidAt"))
            member_id = order.get("memberId")
            m = members.setdefault(member_id, {
                "memberId": member_id, "orderCount": 0, "amount": 0.0})
            m["orderCount"] += 1
            created = str(order.get("createdAt", ""))[:10]
            d = (daily.setdefault(created, {
                "date": created, "orders": 0, "gmv": 0.0})
                if created else None)
            if d:
                d["orders"] += 1
            if paid:
                # 支付口径: 已支付单计入 GMV(退款单由退款率单独观测)
                paid_count += 1
                gmv = _round2(gmv + amount)
                m["amount"] = _round2(m["amount"] + amount)
                if d:
                    d["gmv"] = _round2(d["gmv"] + amount)
                for item in order.get("items", []) or []:
                    pid = str(item.get("productId", ""))
                    p = products.setdefault(pid, {
                        "productId": pid,
                        "productName": str(item.get("productName", pid)),
                        "quantity": 0, "amount": 0.0})
                    p["quantity"] += int(item.get("quantity", 0) or 0)
                    p["amount"] = _round2(
                        p["amount"] + float(item.get("subtotal", 0) or 0))
        durations = self._duration_rows(orders)
        member_rows = sorted(
            members.values(),
            key=lambda r: (-r["amount"], str(r["memberId"])))
        for row in member_rows:
            row["share"] = _round2(row["amount"] / gmv) if gmv else 0.0
        product_total = _round2(sum(p["amount"]
                                    for p in products.values()))
        product_rows = sorted(
            products.values(),
            key=lambda r: (-r["amount"], r["productId"]))
        for row in product_rows:
            row["share"] = (_round2(row["amount"] / product_total)
                            if product_total else 0.0)
        daily_rows = [daily[k] for k in sorted(daily)]
        for row in daily_rows:
            row["gmv"] = _round2(row["gmv"])
        avg_hours = (_round2(sum(r["hours"] for r in durations)
                             / len(durations)) if durations else 0.0)
        return {
            "hasData": total > 0,
            "totalOrders": total,
            "paidOrders": paid_count,
            "statusDistribution": status_dist,
            "gmv": gmv,
            "refundRate": _round2(refunded / total) if total else 0.0,
            "refundedOrders": refunded,
            "avgOrderValue": (_round2(gmv / paid_count)
                              if paid_count else 0.0),
            "avgFulfillmentHours": avg_hours,
            "fulfillmentSamples": len(durations),
            "memberAggregates": member_rows,
            "productAggregates": product_rows,
            "dailySeries": daily_rows,
            "note": ("GMV=已支付订单实付合计(退款单计入支付, 由退款率观测); "
                     "履约=支付→签收小时差; 日时序=创建日聚合; "
                     "商品口径=items.subtotal 折前"),
        }
