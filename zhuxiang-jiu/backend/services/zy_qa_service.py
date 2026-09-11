"""智启元·AI智能财务大模型 P0 问答与分析中枢(zy_qa_service)

「意图驱动」入口(设计文档 §1): 自然语言问财务 → 意图路由(关键词
确定性) → 查询层数字 → 句子模板拼接(数字 100% 插值, 对齐既有
NL 助手铁律)。

四大分析能力:
    - 杜邦分析: ROE = 净利率 × 资产周转率 × 权益乘数(三因素可解释)
    - 归因分析: 净利变动四因素(量/价/本/税)连环替代法(确定性)
    - 健康度: 五维(偿债/营运/盈利/成长/现金)分段 Sigmoid 评分
    - QA 日志: 全量留痕(问题/域/答案要点)供审计

铁律: LLM 禁入判定链——意图路由与数字全部确定性代码。
"""

import logging
import math
from datetime import datetime, UTC

from services.zy_data_service import ZyDataService, _round2

logger = logging.getLogger(__name__)

# 意图域关键词(命中优先级从上到下)
QA_DOMAINS = (
    ("anomaly", ("异常", "波动", "预警", "风险信号")),
    ("tax", ("税", "增值税", "所得税", "消费税", "税负")),
    ("cash", ("现金流", "资金", "现金", "回款")),
    ("cost", ("成本", "费用", "支出", "毛利")),
    ("revenue", ("收入", "销售", "营业额", "营收", "赚")),
)

DOMAIN_NAME = {
    "revenue": "收入", "cost": "成本", "tax": "税负",
    "cash": "现金流", "anomaly": "异常",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ZyQAService:
    """P0: NL 问答 + 杜邦/归因/健康度分析中枢"""

    def __init__(self, data: ZyDataService = None):
        self.data = data or ZyDataService()

    # ============================================================
    # NL 问答(意图路由 → 确定性查询 → 模板拼接)
    # ============================================================

    async def answer(self, question: str) -> dict:
        """自然语言财务问答(数字全部来自查询层)

        Returns:
            {domain, intent, answer, dataSnapshot, reasoning}
        """
        question = (question or "").strip()
        domain = self._route(question)
        snap = await self.data.snapshot()
        if not snap.get("hasData"):
            return {
                "domain": domain, "intent": "no_data",
                "answer": "暂无已结算财务数据(无已支付订单), "
                          "产生交易后即可问答。",
                "dataSnapshot": {}, "reasoning": "数据织物为空",
            }
        handlers = {
            "revenue": self._answer_revenue,
            "cost": self._answer_cost,
            "tax": self._answer_tax,
            "cash": self._answer_cash,
            "anomaly": self._answer_anomaly,
        }
        result = handlers[domain](snap)
        result["reasoning"] = (f"意图路由→{DOMAIN_NAME[domain]}域; "
                               f"取数 {snap['month']} 月度时序; "
                               f"环比基准 {snap['prevMonth'] or '无'}")
        return result

    @staticmethod
    def _route(question: str) -> str:
        """关键词意图路由(优先级: anomaly > tax > cash > cost > revenue)"""
        for domain, keywords in QA_DOMAINS:
            if any(kw in question for kw in keywords):
                return domain
        return "revenue"   # 默认收入域

    @staticmethod
    def _answer_revenue(snap: dict) -> dict:
        mom = snap["mom"]["netAmount"]
        trend = "上升" if mom > 0 else "下降" if mom < 0 else "持平"
        return {
            "domain": "revenue", "intent": "收入查询",
            "answer": (f"{snap['month']} 月净收入 ¥{snap['netAmount']}"
                       f"(销售 ¥{snap['salesAmount']}, 退款冲减 "
                       f"¥{snap['refundAmount']}), 环比{trend} "
                       f"{abs(mom) * 100 if mom else 0}%; "
                       f"订单 {snap['orderCount']} 单, "
                       f"销量 {snap['quantity']} 件。"),
            "dataSnapshot": {k: snap[k] for k in
                             ("month", "netAmount", "salesAmount",
                              "refundAmount", "orderCount", "quantity")},
        }

    @staticmethod
    def _answer_cost(snap: dict) -> dict:
        gross = snap["netAmount"] - snap["costAmount"]
        gross_rate = (gross / snap["netAmount"]
                      if snap["netAmount"] else 0.0)
        return {
            "domain": "cost", "intent": "成本查询",
            "answer": (f"{snap['month']} 月成本 ¥{snap['costAmount']}, "
                       f"毛利 ¥{_round2(gross)}(毛利率 "
                       f"{_round2(gross_rate * 100)}%), "
                       f"净利 ¥{snap['netProfit']}。"),
            "dataSnapshot": {k: snap[k] for k in
                             ("month", "costAmount", "netProfit")},
        }

    @staticmethod
    def _answer_tax(snap: dict) -> dict:
        tax_rate = (snap["taxAmount"] / snap["netAmount"]
                    if snap["netAmount"] else 0.0)
        return {
            "domain": "tax", "intent": "税负查询",
            "answer": (f"{snap['month']} 月估算税负合计 "
                       f"¥{snap['taxAmount']}(增值税+消费税+所得税), "
                       f"占净收入 {_round2(tax_rate * 100)}%。"
                       f"详情可用 /api/zy/tax/simulate 做交易级模拟。"),
            "dataSnapshot": {k: snap[k] for k in
                             ("month", "taxAmount", "netAmount")},
        }

    @staticmethod
    def _answer_cash(snap: dict) -> dict:
        series = snap.get("series") or []
        recent = series[-3:] if len(series) >= 3 else series
        avg = _round2(sum(r["netAmount"] for r in recent) / len(recent)
                      ) if recent else 0.0
        return {
            "domain": "cash", "intent": "现金流查询",
            "answer": (f"近月现金流入均值 ¥{avg}"
                       f"({snap['month']} 当月 ¥{snap['netAmount']}); "
                       f"90 日资金排程可用 "
                       f"/api/zy/evolution/cash-schedule 查看。"),
            "dataSnapshot": {"month": snap["month"],
                             "netAmount": snap["netAmount"],
                             "recentAvg": avg},
        }

    @staticmethod
    def _answer_anomaly(snap: dict) -> dict:
        series = snap.get("series") or []
        alerts = []
        if len(series) >= 2:
            cur, prev = series[-1], series[-2]
            net_swing = ((cur["netAmount"] - prev["netAmount"])
                         / prev["netAmount"]) if prev["netAmount"] else 0
            if abs(net_swing) > 0.3:
                alerts.append(
                    f"净收入环比波动 {_round2(net_swing * 100)}%"
                    f"(阈值 ±30%)")
            refund_ratio = (cur["refundAmount"] / cur["salesAmount"]
                            if cur["salesAmount"] else 0)
            if refund_ratio > 0.15:
                alerts.append(f"退款率 {_round2(refund_ratio * 100)}%"
                              f"(阈值 15%)")
        return {
            "domain": "anomaly", "intent": "异常查询",
            "answer": ("; ".join(alerts) if alerts
                       else "月度口径暂无显著异常(波动阈值 ±30%, "
                            "退款阈值 15%); 详细检测用 "
                            "/api/zy/evolution/anomalies。"),
            "dataSnapshot": {"alerts": len(alerts)},
        }

    # ============================================================
    # 杜邦分析(ROE 三因素分解)
    # ============================================================

    async def dupont(self, period: str = None) -> dict:
        """杜邦分析: ROE = 净利率 × 资产周转率 × 权益乘数

        口径(简化资产负债表):
            - 净利率 = 净利 / 净收入
            - 资产周转率 = 净收入 / 平均总资产(存货成本累积 +
              现金近似, 简化口径)
            - 权益乘数 = 总资产 / 净资产(= 1/(1-负债率), 负债率取
              应付/总资产简化)
        全因素可解释, 附环比对照。
        """
        snap = await self.data.snapshot()
        if not snap.get("hasData"):
            raise ValueError("暂无财务数据, 无法杜邦分析")
        series = snap["series"]
        row = (next((r for r in series if r["month"] == period), None)
               or series[-1])
        net = row["netAmount"]
        profit = row["netProfit"]
        net_margin = profit / net if net else 0.0
        # 简化资产口径: 近 12 月成本累积(存货) + 当月现金(净收入)
        inventory = _round2(sum(r["costAmount"] for r in series))
        total_assets = max(1.0, inventory + net)
        turnover = net / total_assets
        # 简化负债: 近 12 月税负累积(应交税费)
        liabilities = _round2(sum(r["taxAmount"] for r in series))
        debt_ratio = min(0.9, liabilities / total_assets)
        equity_multiplier = 1 / (1 - debt_ratio)
        roe = net_margin * turnover * equity_multiplier
        return {
            "period": row["month"],
            "roe": _round2(roe),
            "factors": {
                "netMargin": _round2(net_margin),
                "assetTurnover": _round2(turnover),
                "equityMultiplier": _round2(equity_multiplier),
            },
            "basis": {
                "netAmount": net, "netProfit": profit,
                "totalAssets": total_assets,
                "liabilities": liabilities,
            },
            "interpretation": (
                f"净利率 {_round2(net_margin * 100)}% × 周转率 "
                f"{_round2(turnover)} × 权益乘数 "
                f"{_round2(equity_multiplier)} → ROE "
                f"{_round2(roe * 100)}%"),
        }

    # ============================================================
    # 归因分析(净利变动四因素连环替代)
    # ============================================================

    async def attribution(self) -> dict:
        """净利环比变动归因: 量/价/本/税四因素连环替代法(确定性)

        因素口径:
            - 量效应: (Q1-Q0) × P0 × (1-成本率0-税率0)
            - 价效应: Q1 × (P1-P0) × (1-成本率0-税率0)
            - 本效应: -Q1 × P1 × (成本率1-成本率0)
            - 税效应: -Q1 × P1 × (税率1-税率0)
        """
        snap = await self.data.snapshot()
        if not snap.get("hasData"):
            raise ValueError("暂无财务数据, 无法归因")
        series = snap["series"]
        if len(series) < 2:
            return {"period": series[-1]["month"], "comparable": False,
                    "factors": [], "delta": 0.0,
                    "note": "仅一期数据, 无法环比归因"}
        cur, prev = series[-1], series[-2]

        def derive(row: dict) -> dict:
            qty = row["quantity"] or 0
            price = row["netAmount"] / qty if qty else 0.0
            cost_rate = (row["costAmount"] / row["netAmount"]
                         if row["netAmount"] else 0.0)
            tax_rate = (row["taxAmount"] / row["netAmount"]
                        if row["netAmount"] else 0.0)
            return {"qty": qty, "price": price,
                    "costRate": cost_rate, "taxRate": tax_rate,
                    "profit": row["netProfit"]}

        c, p = derive(cur), derive(prev)
        margin0 = 1 - p["costRate"] - p["taxRate"]
        volume_effect = _round2(
            (c["qty"] - p["qty"]) * p["price"] * margin0)
        price_effect = _round2(
            c["qty"] * (c["price"] - p["price"]) * margin0)
        cost_effect = _round2(
            -c["qty"] * c["price"] * (c["costRate"] - p["costRate"]))
        tax_effect = _round2(
            -c["qty"] * c["price"] * (c["taxRate"] - p["taxRate"]))
        delta = _round2(c["profit"] - p["profit"])
        factors = [
            {"factor": "销量", "effect": volume_effect,
             "explain": f"销量 {p['qty']}→{c['qty']} 件贡献"},
            {"factor": "单价", "effect": price_effect,
             "explain": f"均价 ¥{_round2(p['price'])}→"
                        f"¥{_round2(c['price'])} 贡献"},
            {"factor": "成本率", "effect": cost_effect,
             "explain": f"成本率 {_round2(p['costRate'] * 100)}%→"
                        f"{_round2(c['costRate'] * 100)}% 影响"},
            {"factor": "税率", "effect": tax_effect,
             "explain": f"税率 {_round2(p['taxRate'] * 100)}%→"
                        f"{_round2(c['taxRate'] * 100)}% 影响"},
        ]
        return {
            "period": cur["month"], "prevPeriod": prev["month"],
            "comparable": True, "delta": delta,
            "factors": factors,
            "note": "连环替代法(确定性), 四因素效应之和≈净利变动",
        }

    # ============================================================
    # 财务健康度(五维分段 Sigmoid)
    # ============================================================

    async def health(self) -> dict:
        """五维健康度: 偿债/营运/盈利/成长/现金

        口径(各维 0-100, 分段 Sigmoid 映射, 对齐 68号雷达范式):
            - 盈利: 净利率 → 25% 满分档
            - 营运: 资产周转(月) → 0.8 满分档
            - 偿债: 1-负债率(资产负债简化口径)
            - 成长: 净收入环比 → +20% 满分档
            - 现金: 退款率反向 → <5% 满分档
        """
        snap = await self.data.snapshot()
        if not snap.get("hasData"):
            raise ValueError("暂无财务数据, 无法健康度评估")
        series = snap["series"]
        cur = series[-1]
        prev = series[-2] if len(series) >= 2 else cur

        def sigmoid(x: float, k: float, x0: float) -> float:
            try:
                return 100 / (1 + math.exp(-k * (x - x0)))
            except OverflowError:
                return 100.0 if x > x0 else 0.0

        net = cur["netAmount"] or 1.0
        profit_rate = cur["netProfit"] / net
        inventory = sum(r["costAmount"] for r in series) or 1.0
        turnover = cur["netAmount"] / (inventory + cur["netAmount"])
        liabilities = sum(r["taxAmount"] for r in series)
        debt_ratio = min(0.9, liabilities / (inventory + cur["netAmount"]))
        growth = snap["mom"]["netAmount"]
        refund_ratio = (cur["refundAmount"] / cur["salesAmount"]
                        if cur["salesAmount"] else 0.0)
        dims = [
            {"dim": "盈利能力", "raw": _round2(profit_rate * 100),
             "score": _round2(sigmoid(profit_rate, 40, 0.15)),
             "explain": f"净利率 {_round2(profit_rate * 100)}%"},
            {"dim": "营运能力", "raw": _round2(turnover),
             "score": _round2(sigmoid(turnover, 20, 0.3)),
             "explain": f"资产周转 {_round2(turnover)} 次/月"},
            {"dim": "偿债能力", "raw": _round2((1 - debt_ratio) * 100),
             "score": _round2(sigmoid(1 - debt_ratio, 12, 0.5)),
             "explain": f"资产负债率 {_round2(debt_ratio * 100)}%"},
            {"dim": "成长能力", "raw": _round2(growth * 100),
             "score": _round2(sigmoid(growth, 8, 0.05)),
             "explain": f"净收入环比 {_round2(growth * 100)}%"},
            {"dim": "现金质量", "raw": _round2((1 - refund_ratio) * 100),
             "score": _round2(sigmoid(1 - refund_ratio, 20, 0.85)),
             "explain": f"退款率 {_round2(refund_ratio * 100)}%"},
        ]
        total = _round2(sum(d["score"] for d in dims) / len(dims))
        grade = ("A 优秀" if total >= 80 else "B 良好" if total >= 65
                 else "C 关注" if total >= 50 else "D 预警")
        return {
            "month": cur["month"], "totalScore": total,
            "grade": grade, "dimensions": dims,
            "prevMonth": prev["month"],
        }
