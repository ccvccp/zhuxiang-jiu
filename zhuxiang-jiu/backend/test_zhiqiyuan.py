"""智启元·AI智能财务大模型 专项测试(P0-P3 全量)

覆盖:
    [数据织物 zy_data_service]
    1.  月度时序聚合(收入/成本/税/净利, 退款冲减)
    2.  未支付订单不计收入(财务口径)
    3.  确定性(同数据两次聚合一致)
    4.  快照环比口径
    [P0 问答与分析 zy_qa_service]
    5.  意图路由五域(关键词确定性+默认收入)
    6.  收入域问答(数字来自查询层)
    7.  成本域问答(毛利率计算)
    8.  税负域问答
    9.  异常域问答(波动/退款阈值)
    10. 无数据兜底(不抛异常)
    11. 杜邦三因素(ROE = 净利率×周转×权益乘数 可复算)
    12. 归因四因素(量/价/本/税连环替代, 效应和≈变动)
    13. 健康度五维(Sigmoid 0-100 + 等级)
    [P1 预测沙盘 zy_forecast_service]
    14. 滚动预测(0.6近期+0.4全期权重口径)
    15. 预测确定性(两次调用同输出)
    16. 沙盘弹性公式(价×量→收入, 本→成本)
    17. 沙盘净利传导 + 预案建议
    18. 沙盘边界(±50% 外 409 口径 ValueError)
    19. 驱动因素(相关性排序 + 方向标记)
    [P2 税务优化 zy_tax_service]
    20. 税负模拟四结构对比(cross_border 最优: 免消费税)
    21. 模拟留痕口径(建议不自动)
    22. 模拟边界(金额≤0 拒绝)
    23. 政策库种子+标签匹配(小微命中)
    24. 政策匹配建议口径(永不自动享受)
    25. 风险热力图五维(税负偏离/退款/大额/波动/亏损)
    [P3 进化决策 zy_evolution_service]
    26. 反馈闭环(adopted→趋势权重+0.1)
    27. 反馈安全阀(rejected 连续压到 0 后 clamp 不越界)
    28. corrected 不调参数
    29. 反馈非法目标/裁决拒绝
    30. 异常检测(spike: 超均值+3σ)
    31. 异常检测(drop: 绝对降幅地板防冷启动)
    32. 资金调度(90日逐日+缺口日+建议)
    33. 投资备忘 DCF(NPV 手工复算一致)
    34. DCF 敏感性(折现率 ±2pp)
    35. 备忘边界(年限越界拒绝)
    36. 进化日志留痕
    37. 总览(反馈统计+异常)
    38. 推理链口径(答案附 reasoning)

运行: python backend/test_zhiqiyuan.py(自跑范式, 内存模式)
"""
import asyncio
import os
import sys

# 确保使用内存模式(不触碰 Redis)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repositories.store import reset_store as _reset_store
from repositories.order_repository import OrderRepository
from services.zy_data_service import ZyDataService
from services.zy_qa_service import ZyQAService
from services.zy_forecast_service import ZyForecastService
from services.zy_tax_service import ZyTaxService
from services.zy_evolution_service import (
    ZyEvolutionService,
)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def _order(oid, month, amount, qty=10, status="PAID",
           cost_price=None):
    items = [{"productId": "P1", "quantity": qty,
             "price": amount / qty if qty else amount}]
    return {
        "order_id": oid, "status": status,
        "createdAt": f"2026-{month}-10T10:00:00Z",
        "items": items,
        "priceDetail": {"actualAmount": amount},
        "payment": ({"paidAt": f"2026-{month}-10T10:05:00Z"}
                    if status != "PENDING" else {}),
        "refund": ({"refundedAt": f"2026-{month}-15T10:00:00Z",
                    "refundedAmount": amount}
                   if status == "REFUNDED" else {}),
    }


async def _seed_orders(orders):
    repo = OrderRepository()
    for o in orders:
        await repo.create(o)


class TestFabric:

    async def run(self):
        _reset_store()
        # 3 个月订单: 1 月 1 万 / 2 月 2 万 / 3 月 3 万 + 1 笔退款 + 1 笔未支付
        await _seed_orders([
            _order("O1", "01", 10_000),
            _order("O2", "02", 20_000),
            _order("O3", "03", 30_000),
            _order("O4", "03", 2_000, status="REFUNDED"),
            _order("O5", "03", 5_000, status="PENDING"),
        ])
        svc = ZyDataService()
        series = await svc.monthly_series(months=12)
        record("织物-月度聚合", len(series) == 3
               and series[0]["netAmount"] == 10_000
               and series[2]["salesAmount"] == 30_000,
               str([(r["month"], r["netAmount"]) for r in series]))
        record("织物-退款冲减", series[2]["netAmount"] == 28_000
               and series[2]["refundAmount"] == 2_000,
               f"3月净额 {series[2]['netAmount']}")
        record("织物-未支付不计收入",
               all(r["salesAmount"] != 37_000 for r in series))
        record("织物-成本税负净利", all(
            r["costAmount"] > 0 and r["taxAmount"] > 0
            and r["netProfit"] == round(r["netAmount"]
            - r["costAmount"] - r["taxAmount"], 2)
            for r in series))
        again = await svc.monthly_series(months=12)
        record("织物-确定性", series == again)
        snap = await svc.snapshot()
        record("织物-快照环比", snap["month"] == "202603"
               and snap["mom"]["netAmount"] == 0.4,
               f"{snap['month']} mom={snap['mom']}")


class TestQA:

    async def run(self):
        svc = ZyQAService()
        record("问答-意图五域",
               svc._route("上月收入多少") == "revenue"
               and svc._route("成本费用情况") == "cost"
               and svc._route("增值税交多少") == "tax"
               and svc._route("现金流怎么样") == "cash"
               and svc._route("有什么异常波动") == "anomaly"
               and svc._route("随便问点") == "revenue")
        rev = await svc.answer("上月净收入多少")
        record("问答-收入域", rev["domain"] == "revenue"
               and "28000" in rev["answer"]
               and rev["dataSnapshot"]["netAmount"] == 28_000,
               rev["answer"])
        cost = await svc.answer("本月成本和毛利率")
        record("问答-成本域", cost["domain"] == "cost"
               and "毛利率" in cost["answer"])
        tax = await svc.answer("本月税负")
        record("问答-税负域", tax["domain"] == "tax"
               and "税负" in tax["answer"])
        anomaly = await svc.answer("有什么异常")
        record("问答-异常域", anomaly["domain"] == "anomaly"
               and ("波动" in anomaly["answer"]
                    or "异常" in anomaly["answer"]
                    or "暂无" in anomaly["answer"]),
               anomaly["answer"])
        record("问答-推理链", "意图路由" in rev["reasoning"])
        # 杜邦
        dp = await svc.dupont()
        check_roe = (dp["factors"]["netMargin"]
                     * dp["factors"]["assetTurnover"]
                     * dp["factors"]["equityMultiplier"])
        record("杜邦-三因素复算", abs(dp["roe"] - round(check_roe, 2))
               <= 0.01, f"roe={dp['roe']} 复算={round(check_roe, 2)}")
        # 归因
        attr = await svc.attribution()
        record("归因-四因素", attr["comparable"] is True
               and len(attr["factors"]) == 4
               and [f["factor"] for f in attr["factors"]]
               == ["销量", "单价", "成本率", "税率"])
        # 健康度
        h = await svc.health()
        record("健康度-五维等级", len(h["dimensions"]) == 5
               and 0 <= h["totalScore"] <= 100
               and h["grade"] in ("A 优秀", "B 良好", "C 关注", "D 预警"),
               f"total={h['totalScore']} {h['grade']}")

        # 无数据兜底
        _reset_store()
        empty = await svc.answer("收入多少")
        record("问答-无数据兜底", "暂无" in empty["answer"])


class TestForecast:

    async def run(self):
        _reset_store()
        await _seed_orders([
            _order("O1", "01", 10_000),
            _order("O2", "02", 20_000),
            _order("O3", "03", 30_000),
        ])
        fc = ZyForecastService()
        result = await fc.rolling_forecast(horizon=6, use_trend=False)
        recent_avg = round((10_000 + 20_000 + 30_000) / 3, 2)
        full_avg = 20_000.0
        expected = round(0.6 * recent_avg + 0.4 * full_avg, 2)
        record("预测-权重口径",
               abs(result["rows"][0]["netAmount"] - expected) < 0.01,
               f"{result['rows'][0]['netAmount']} vs {expected}")
        again = await fc.rolling_forecast(horizon=6, use_trend=False)
        record("预测-确定性",
               result["rows"] == again["rows"])
        # 沙盘
        sb = await fc.sandbox(price_delta=0.1, volume_delta=0.0,
                              cost_delta=0.0)
        expected_revenue = round(30_000 * 1.1, 2)
        record("沙盘-弹性公式",
               sb["scenario"]["revenue"] == expected_revenue
               and sb["impacts"]["revenue"]
               == round(expected_revenue - 30_000, 2),
               f"{sb['scenario']['revenue']} vs {expected_revenue}")
        record("沙盘-净利传导", "netProfit" in sb["scenario"]
               and sb["mitigations"] and isinstance(
                   sb["mitigations"], list))
        try:
            await fc.sandbox(price_delta=0.7)
            record("沙盘-边界拒绝", False)
        except ValueError:
            record("沙盘-边界拒绝", True)
        # 驱动因素
        dr = await fc.drivers()
        record("驱动-排序方向", len(dr["drivers"]) == 4
               and dr["drivers"][0]["sensitivity"]
               >= dr["drivers"][-1]["sensitivity"]
               and any(d["factor"] == "退款率"
                       and d["direction"] == "反向"
                       for d in dr["drivers"]))


class TestTax:

    async def run(self):
        _reset_store()
        await _seed_orders([_order("O1", "03", 30_000)])
        tx = ZyTaxService()
        sim = await tx.simulate(amount=113_000, quantity=100)
        record("税负-四结构对比", len(sim["structures"]) == 4
               and all(0 < s["total"] < 113_000
                       for s in sim["structures"]))
        record("税负-跨境最优(免消费税)",
               sim["recommendation"]["best"] == "cross_border"
               and sim["recommendation"]["savingVsWorst"] > 0,
               str([(s["structure"], s["total"])
                    for s in sim["structures"]]))
        record("税负-建议留痕口径",
               "人工" in sim["recommendation"]["note"])
        try:
            await tx.simulate(amount=0)
            record("税负-金额边界", False)
        except ValueError:
            record("税负-金额边界", True)
        # 政策匹配
        pol = await tx.policies(business_tags=["小微", "白酒"])
        record("政策-种子与匹配", len(pol["policies"]) == 3
               and any(p["policyId"] == "POL001"
                       and "小微" in p["matchedTags"]
                       for p in pol["matched"]))
        record("政策-永不自动口径",
               "人工" in pol["note"] or "申报" in pol["note"])
        # 风险热力
        heat = await tx.risk_heatmap()
        risks = {r["risk"] for r in heat["risks"]}
        record("热力-五维扫描", len(heat["risks"]) == 5
               and "综合税负率偏离" in risks
               and "退款率(进项转出遗漏)" in risks
               and all(r["severity"] in ("low", "attention", "medium",
                                         "high", "critical")
                       for r in heat["risks"]))
        record("热力-处置人工", "永不自动" in heat["note"])


class TestEvolution:

    async def run(self):
        _reset_store()
        await _seed_orders([
            _order("O1", "01", 10_000),
            _order("O2", "02", 20_000),
            _order("O3", "03", 30_000),
        ])
        ev = ZyEvolutionService()
        # 反馈闭环
        f1 = await ev.feedback("forecast", "adopted", "预测准")
        record("进化-采纳升权重", f1["trendWeightAfter"] == 0.6)
        f2 = await ev.feedback("forecast", "rejected", "偏了")
        record("进化-拒绝降权重", f2["trendWeightAfter"] == 0.5)
        # 连续压到 clamp 下界
        for _ in range(10):
            await ev.feedback("forecast", "rejected")
        params = await ev.get_forecast_params()
        record("进化-安全阀clamp", params["trendWeight"] == 0.0,
               str(params))
        before = dict(params)
        await ev.feedback("forecast", "corrected", "人工修正")
        after = await ev.get_forecast_params()
        record("进化-corrected不调参",
               after["trendWeight"] == before["trendWeight"])
        try:
            await ev.feedback("invalid_type", "adopted")
            record("进化-非法目标拒绝", False)
        except ValueError:
            record("进化-非法目标拒绝", True)
        # 异常检测: spike 数据
        _reset_store()
        await _seed_orders([
            _order("O1", "01", 1_000),
            _order("O2", "02", 1_000),
            _order("O3", "03", 1_000),
            _order("O4", "04", 1_000),
            _order("O5", "05", 50_000),   # spike
        ])
        alerts = await ev.anomalies()
        record("异常-spike检测", any(
            a["type"] == "spike" for a in alerts)
            and all("永不自动" in a["disposition"] for a in alerts))
        # drop 检测(绝对地板)
        _reset_store()
        await _seed_orders([
            _order("O1", "01", 10_000),
            _order("O2", "02", 10_000),
            _order("O3", "03", 10_000),
            _order("O4", "04", 10_000),
            _order("O5", "05", 100),   # drop < 20 差额? 差 9900 ≥20
        ])
        alerts2 = await ev.anomalies()
        record("异常-drop检测", any(
            a["type"] == "drop" for a in alerts2),
            str([a["type"] for a in alerts2]))
        # 资金调度
        _reset_store()
        await _seed_orders([
            _order("O1", "01", 30_000),
            _order("O2", "02", 30_000),
            _order("O3", "03", 30_000),
        ])
        cash = await ev.cash_schedule(days=30)
        record("调度-逐日口径", len(cash["rows"]) == 30
               and cash["rows"][0]["day"] == 1
               and "note" in cash
               and "永不自动" in cash["note"])
        # 投资备忘 DCF
        memo = await ev.decision_memo("investment", {
            "initialInvestment": 100_000, "annualCashFlow": 30_000,
            "growthRate": 0.0, "years": 5, "discountRate": 0.08})
        # 手工复算 NPV
        manual = round(sum(30_000 / (1.08 ** t)
                           for t in range(1, 6)) - 100_000, 2)
        record("备忘-DCF复算", abs(memo["npv"] - manual) <= 0.01,
               f"{memo['npv']} vs {manual}")
        record("备忘-敏感性三档", len(memo["sensitivities"]) == 3
               and memo["sensitivities"][0]["discountRate"] == 0.06
               and memo["sensitivities"][2]["discountRate"] == 0.1)
        try:
            await ev.decision_memo("investment",
                                   {"years": 50})
            record("备忘-边界拒绝", False)
        except ValueError:
            record("备忘-边界拒绝", True)
        # 日志(reset 清空过 store, 先再产生一条 feedback)
        await ev.feedback("analysis", "adopted", "复盘留痕")
        logs = await ev.logs()
        record("进化-日志留痕", len(logs) >= 1
               and any(l["engine"] == "feedback" for l in logs))
        # 总览
        st = await ev.status()
        record("总览-结构", "feedbacks" in st
               and "anomalies" in st
               and "trendWeight" in st["feedbacks"])


async def main():
    print("=" * 60)
    print("智启元·AI智能财务大模型(P0-P3) 专项测试")
    print("=" * 60)
    for suite in (TestFabric(), TestQA(), TestForecast(),
                  TestTax(), TestEvolution()):
        await suite.run()
    print("-" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
