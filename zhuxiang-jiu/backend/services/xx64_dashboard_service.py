"""64号·信值兑换管理 四区看板
(xx64_dashboard_service, P5)

计划(docs/64号_信值兑换商品服务
AI智能管理模块实施计划.md
§八 P5):
    四区看板(度量/订单/流通/防御)
    +宪法断言+收官

四区(全部观测口径——实时聚合
不落库):
    ① 度量区 metrics: 总订单/
       兑换信值总额/现付总额/
       活跃买家/最新购买力指数
    ② 订单区 orders: 九态分布
       +近期订单
    ③ 流通区 circulation: 转移
       账本聚合(借贷平衡)+积分
       兑换统计(pending/credited/
       cancelled)
    ④ 防御区 defense: 风控事件
       (检测器分布/open)+申诉
       统计(翻转率)+三开关状态

铁律: 观测面不受 XX64_MODE
影响; 数字全部来自仓储层
(可溯源); LLM 不进判定链。
"""

import logging
import os

from core.helpers import ts

from repositories.xx64_repository import (
    Xx64Repository,
)

logger = logging.getLogger("xx64_dash")

MODEL_VERSION = "v1-xx64-dashboard"

SCORER_ID = "value_exchange"

ORDER_STATES_ALL = (
    "initiated", "prechecked",
    "reserved", "paid", "settled",
    "completed", "cancelled",
    "refunded", "disputed")


class Xx64DashboardService:
    """64号四区看板(P5——度量/
    订单/流通/防御+宪法)"""

    def __init__(self):
        self.repo = Xx64Repository()

    # ============================================================
    # 四区看板(观测面)
    # ============================================================

    async def dashboard(self) -> dict:
        """四区看板(实时聚合——
        不受开关影响)"""
        orders = await self.repo.list_orders(
            limit=500)
        ledger = await self.repo.list_ledger(
            limit=500)
        exchanges = await self.repo \
            .list_exchanges(limit=500)
        risks = await self.repo.list_risks(
            limit=500)
        appeals = await self.repo \
            .list_appeals(limit=500)
        anchors = await self.repo \
            .list_anchors(limit=90)

        # ① 度量区
        total_trust = round(sum(
            float(o.get("trustValue") or 0)
            for o in orders), 2)
        total_cash = round(sum(
            float(o.get("cashValue") or 0)
            for o in orders), 2)
        buyers = sorted({
            int(o.get("buyerId") or 0)
            for o in orders
            if int(o.get("buyerId")
                   or 0) > 0})
        latest_anchor = anchors[0] \
            if anchors else {}
        metrics = {
            "totalOrders": len(orders),
            "totalTrustValue":
                total_trust,
            "totalCashValue":
                total_cash,
            "activeBuyers": len(buyers),
            "purchasingPower":
                latest_anchor.get(
                    "purchasingPower"),
            "anchorDate":
                latest_anchor.get(
                    "anchorDate"),
        }

        # ② 订单区(九态分布)
        dist = {s: 0
                for s in ORDER_STATES_ALL}
        for o in orders:
            s = o.get("status")
            if s in dist:
                dist[s] += 1
        recent = sorted(
            orders,
            key=lambda o: -int(
                o.get("orderId") or 0)
        )[:5]
        orders_zone = {
            "statusDistribution": dist,
            "recent": [
                {"orderId": o.get(
                    "orderId"),
                 "product": o.get(
                    "product"),
                 "status": o.get(
                    "status"),
                 "trustValue":
                    o.get("trustValue"),
                 "cashValue":
                    o.get("cashValue")}
                for o in recent],
        }

        # ③ 流通区(账本聚合)
        debit_total = round(sum(
            abs(float(e.get("amount")
                      or 0))
            for e in ledger
            if e.get("direction")
            == "debit"
            and not e.get("rolledBack")
        ), 2)
        credit_total = round(sum(
            float(e.get("amount") or 0)
            for e in ledger
            if e.get("direction")
            == "credit"
            and not e.get("rolledBack")
        ), 2)
        exch_dist = {"pending": 0,
                     "credited": 0,
                     "cancelled": 0}
        for x in exchanges:
            s = x.get("status")
            if s in exch_dist:
                exch_dist[s] += 1
        circulation = {
            "ledgerEntries":
                len(ledger),
            "debitTotal": debit_total,
            "creditTotal":
                credit_total,
            "balanced":
                abs(debit_total
                    - credit_total)
                < 0.005,
            "exchanges": exch_dist,
            "pointsExchanged": sum(
                int(x.get("points")
                    or 0)
                for x in exchanges
                if x.get("status") in (
                    "pending",
                    "credited")),
        }

        # ④ 防御区
        detector_dist = {}
        open_risks = 0
        for r in risks:
            d = r.get("detectorCode") \
                or "unknown"
            detector_dist[d] = \
                detector_dist.get(d, 0) + 1
            if r.get("status") == "open":
                open_risks += 1
        appeal_stats = {
            "total": len(appeals),
            "approved": sum(
                1 for a in appeals
                if a.get("status")
                == "approved"),
            "rejected": sum(
                1 for a in appeals
                if a.get("status")
                == "rejected"),
            "overturnRate": round(
                sum(1 for a in appeals
                    if a.get("status")
                    == "approved")
                / len(appeals), 4)
            if appeals else 0.0,
        }
        defense = {
            "riskEvents": len(risks),
            "openRisks": open_risks,
            "byDetector":
                detector_dist,
            "appeals": appeal_stats,
        }

        # 宪法(三开关+R1-R7)
        constitution = {
            "mode": os.environ.get(
                "XX64_MODE", "off"),
            "learnMode": os.environ.get(
                "XX64_LEARN_MODE",
                "off"),
            "llmMode": os.environ.get(
                "XX64_LLM_MODE", "off"),
            "rules": "R1-R7(30/70 混合"
                     "+互斥+转移+20%"
                     "/40% 限额+100:1"
                     "+非负)",
        }
        return {
            "success": True,
            "zones": {
                "metrics": metrics,
                "orders": orders_zone,
                "circulation":
                    circulation,
                "defense": defense,
            },
            "constitution": constitution,
            "note": "四区看板——度量/"
                    "订单/流通/防御"
                    "(实时聚合可溯源; "
                    "观测面不受开关影响)",
            "generatedAt": ts(),
        }
