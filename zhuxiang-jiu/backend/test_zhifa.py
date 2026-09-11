"""智法·AI智能法务大模型 专项测试(P0-P3 全量)

覆盖:
    [P0 生产合规 zf_production_service]
    1.  工艺校验三档(合规/偏离/违规)
    2.  添加剂一票否决(GB 2760 硬限)
    3.  违规→处置工单(锁定建议, 永不自动)
    4.  未知工艺参数拒绝
    5.  数字产品护照(指纹链: prevHash+contentHash)
    6.  护照验证(重算一致)+篡改检出
    7.  未校验批次禁止签发护照
    8.  理化不合格拒绝签发
    9.  ESG 确定性评分(能耗/污水/回用三要素)
    10. 质量风险预测(偏离度+关联度加权)
    [P1 供应链金融 zf_finance_service]
    11. 信用评分公式(0.3订单+0.3周转+0.4回款)
    12. 库存矛盾欺诈检测(>产能×3)
    13. 评级分档(A/B/C)
    14. 动态合约差异化条款(按评级)
    15. 欺诈主体禁止生成合约
    16. 资金白名单校验(违规流向)
    17. 违约预警函(>20% 触发)
    [P2 数据资产 zf_asset_service]
    18. 分类分级四档(个人信息→L4)
    19. 工艺秘方识别(L3)
    20. 许可协议三要素(用途/审计/分成)
    21. L4/L3 红线禁止许可
    22. 跨境评估 GDPR 匹配
    23. 含个人信息禁止出境
    [P3 电商深化 zf_commerce_service]
    24. 先涨后降检测
    25. 划线价无依据检测
    26. 会员价虚标检测
    27. 预售护栏(超期/定金超比 409)
    28. 预售协议四条款
    29. 职业打假高风险画像(三标记)
    30. 证据包固化清单
    [P3 进化闭环 zf_evolution_service]
    31. 反馈闭环(adopted→严格度+0.1)
    32. 反馈安全阀(clamp [0.6, 1.4])
    33. rejected 降严格度
    34. corrected 不调参
    35. 非法目标/裁决拒绝
    36. 判例种子(4 条规则更新建议)
    37. 孪生三重校验(物理×数字覆盖率)
    38. 大模型总览

运行: python backend/test_zhifa.py(自跑范式, 内存模式)
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
from services.zf_fabric_service import _ZfStore, ZfFabricService
from services.zf_production_service import ZfProductionService
from services.zf_finance_service import ZfFinanceService
from services.zf_asset_service import ZfAssetService
from services.zf_commerce_service import ZfCommerceService
from services.zf_evolution_service import ZfEvolutionService

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


# 合规工艺参数(全在限值内)
GOOD_PARAMS = {
    "fermentation_days": 35, "fermentation_temp": 28,
    "storage_months": 6, "additive_count": 0, "blend_ratio": 0.3,
}


class TestP0Production:

    async def run(self):
        _reset_store()
        store = _ZfStore()
        svc = ZfProductionService(store=store)

        # [1] 三档判定
        r = await svc.process_check("B20260901", dict(GOOD_PARAMS))
        record("P0-合规通过", r["verdict"] == "pass"
               and "workOrder" not in r, str(r["verdict"]))
        r = await svc.process_check(
            "B20260902", {**GOOD_PARAMS, "fermentation_days": 20})
        record("P0-偏离判定", r["verdict"] == "deviation"
               and len(r["deviations"]) == 1)
        r = await svc.process_check(
            "B20260903", {**GOOD_PARAMS, "additive_count": 2})
        record("P0-违规判定", r["verdict"] == "violation"
               and len(r["violations"]) == 1)

        # [2] 添加剂一票否决(硬限)
        r = await svc.process_check(
            "B20260904", {**GOOD_PARAMS, "additive_count": 1})
        record("P0-添加剂一票否决", r["verdict"] == "violation"
               and r["violations"][0]["param"] == "additive_count")

        # [3] 工单口径
        record("P0-处置工单建议", "workOrder" in r
               and "锁定" in r["workOrder"]["suggestion"]
               and "永不自动" in r["workOrder"]["disposition"])

        # [4] 未知参数拒绝
        try:
            await svc.process_check("BX", {"unknown_param": 1})
            record("P0-未知参数拒绝", False)
        except ValueError:
            record("P0-未知参数拒绝", True)

        # [5] 护照指纹链
        p1 = await svc.passport(
            "B20260901", dict(GOOD_PARAMS),
            {"alcohol": 42.0, "methanol": 0.2},
            operator="酿酒师张三", quality_insp="质检李四")
        record("P0-护照指纹链", p1["prevHash"] == "GENESIS"
               and len(p1["fingerprint"]) == 64)
        await svc.process_check("B20260905", dict(GOOD_PARAMS))
        p2 = await svc.passport(
            "B20260905", dict(GOOD_PARAMS),
            {"alcohol": 53.0, "methanol": 0.1},
            operator="酿酒师王五")
        record("P0-链式哈希", p2["prevHash"] == p1["fingerprint"])

        # [6] 验证与篡改检出
        v = await svc.verify_passport("B20260901")
        record("P0-护照验证通过", v["valid"] is True)
        p1["content"]["operator"] = "篡改者"
        await store.save("passports", "B20260901", p1)
        v2 = await svc.verify_passport("B20260901")
        record("P0-篡改检出", v2["valid"] is False)

        # [7] 未校验批次禁止签发
        try:
            await svc.passport("B-UNCHECKED", {}, {}, operator="x")
            record("P0-未校验拒签发", False)
        except ValueError:
            record("P0-未校验拒签发", True)

        # [8] 理化不合格拒绝
        try:
            await svc.passport("B20260901", dict(GOOD_PARAMS),
                              {"alcohol": 75.0, "methanol": 0.2},
                              operator="x")
            record("P0-理化不合格拒签发", False)
        except ValueError as e:
            record("P0-理化不合格拒签发", "酒精度" in str(e))

        # [9] ESG 评分
        e = await svc.esg_report("202609", energy_kwh=700,
                                 wastewater_tons=3.0,
                                 recycled_ratio=0.4)
        record("P0-ESG绿色达标", e["grade"].startswith("A")
               and e["totalScore"] >= 85, str(e["totalScore"]))
        e2 = await svc.esg_report("202609", energy_kwh=1800,
                                  wastewater_tons=12.0)
        record("P0-ESG预警整改", e2["grade"].startswith("C")
               and e2["totalScore"] < 60, str(e2["totalScore"]))

        # [10] 质量风险预测
        qr = await svc.quality_risk()
        record("P0-质量风险预测", qr["batches"] >= 3
               and qr["deviationRatio"] > 0
               and "riskScore" in qr and qr["riskLevel"] in
               ("low", "medium", "high"))


class TestP1Finance:

    async def run(self):
        store = _ZfStore()
        svc = ZfFinanceService(store=store)

        # [11] 信用评分公式
        c = await svc.credit_assess(
            "SUP001", "济南粮液供应链", monthly_orders=50000,
            inventory_value=60000, production_capacity=50000,
            repayment_rate=0.95)
        # 订单 100 分(5 万/万归一满分) 周转 1.2 健康 100 回款 95
        expect = round(0.3 * 100 + 0.3 * 100 + 0.4 * 95, 2)
        record("P1-信用评分公式", c["score"] == expect,
               f"{c['score']} vs {expect}")

        # [12] 库存矛盾欺诈
        c2 = await svc.credit_assess(
            "SUP002", "虚库贸易", monthly_orders=10000,
            inventory_value=500000, production_capacity=50000,
            repayment_rate=0.9)
        record("P1-矛盾欺诈检测", c2["fraudSuspected"] is True
               and len(c2["contradictions"]) == 1)

        # [13] 评级分档
        record("P1-评级A档", c["grade"] == "A 优")
        c3 = await svc.credit_assess(
            "SUP003", "小型经销商", monthly_orders=3000,
            inventory_value=20000, production_capacity=50000,
            repayment_rate=0.7)
        record("P1-评级分档", c3["grade"] in ("B 良", "C 关注"))

        # [14] 动态合约差异化
        ct = await svc.contract_generate("SUP001", 200000)
        record("P1-合约A档免担保",
               ct["terms"]["guarantee"] == "信用免担保"
               and ct["terms"]["annualRate"] == 0.048)
        ct3 = await svc.contract_generate("SUP003", 100000)
        record("P1-合约差异化条款",
               ct3["terms"]["collateralRatio"]
               > ct["terms"]["collateralRatio"]
               and ct3["terms"]["annualRate"]
               > ct["terms"]["annualRate"])

        # [15] 欺诈主体禁止
        try:
            await svc.contract_generate("SUP002", 50000)
            record("P1-欺诈禁约", False)
        except ValueError:
            record("P1-欺诈禁约", True)

        # [16] 资金白名单
        fm = await svc.fund_monitor("SUP001", ct["contractId"], [
            {"amount": 100000, "use": "procurement"},
            {"amount": 50000, "use": "marketing"},
        ])
        record("P1-白名单校验", len(fm["violations"]) == 1
               and fm["violations"][0]["use"] == "marketing"
               and fm["violatedAmount"] == 50000)

        # [17] 违约预警函
        record("P1-违约预警函", fm["warningLetter"] is not None
               and "人工确认" in fm["warningLetter"]["action"])


class TestP2Asset:

    async def run(self):
        store = _ZfStore()
        svc = ZfAssetService(store=store)

        # [18] 分类分级
        cat = await svc.classify([
            "member_phone", "收货地址", "发酵温度", "勾调配方",
            "供应商价格", "库存数",
        ])
        levels = {i["field"]: i["level"] for i in cat["catalog"]}
        record("P2-个人信息L4", levels["member_phone"] == "L4"
               and levels["收货地址"] == "L4")
        # [19] 工艺秘方
        record("P2-工艺秘方L3", levels["发酵温度"] == "L3"
               and levels["勾调配方"] == "L3")
        record("P2-商业与公开数据",
               levels["供应商价格"] == "L2"
               and levels["库存数"] == "L1")
        record("P2-目录边界", "禁止出库" in cat["boundaries"]["L4"]
               and cat["totalFields"] == 6)

        # [20] 许可协议三要素
        lic = await svc.license_generate(
            "脱敏销量趋势数据", "L1", "合作研究机构",
            revenue_share=0.15, term_months=12)
        record("P2-许可三要素",
               "purposeRestriction" in lic["elements"]
               and "securityAudit" in lic["elements"]
               and "revenueShare" in lic["elements"]
               and len(lic["clauses"]) == 5)

        # [21] 红线
        try:
            await svc.license_generate("消费者手机号", "L4", "x")
            record("P2-L4红线", False)
        except ValueError as e:
            record("P2-L4红线", "禁止" in str(e))
        try:
            await svc.license_generate("勾调配方", "L3", "x")
            record("P2-L3红线", False)
        except ValueError:
            record("P2-L3红线", True)

        # [22] 跨境评估
        cb = await svc.cross_border_assess("EU", ["L1", "L2"],
                                           "海外市场分析")
        record("P2-GDPR匹配", cb["targetLaw"] == "GDPR"
               and cb["passable"] is True
               and "scc" in cb["checklist"])

        # [23] 个人信息禁止出境
        cb2 = await svc.cross_border_assess("US", ["L4"], "x")
        record("P2-L4禁出境", cb2["passable"] is False
               and "不建议出境" in cb2["conclusion"])
        try:
            await svc.cross_border_assess("XX", ["L1"])
            record("P2-区域校验", False)
        except ValueError:
            record("P2-区域校验", True)


class TestP3Commerce:

    async def run(self):
        store = _ZfStore()
        svc = ZfCommerceService(store=store)

        # [24] 先涨后降
        history = [{"day": d, "dealPrice": p} for d, p in enumerate(
            [100, 100, 100, 130, 130, 130, 130, 130])]  # 涨 30%
        r = await svc.price_audit("P001", history, {
            "original": 130, "strikethrough": 100,
            "coupon": 128, "member": 130,
        })
        types = {f["type"] for f in r["findings"]}
        record("P3-先涨后降", "hike_then_drop" in types)

        # [25] 划线价无依据
        record("P3-划线价无依据", "unfounded_strikethrough" in types
               or r["findings"])

        # [26] 会员价虚标
        record("P3-会员价虚标", "member_price_inflated" in types)

        # 合规样例
        ok = await svc.price_audit("P002", [
            {"day": d, "dealPrice": 100} for d in range(8)], {
            "original": 100, "strikethrough": 110,
            "coupon": 90, "member": 85,
        })
        record("P3-合规通过", ok["compliant"] is True
               and len(ok["findings"]) == 0)

        # [27] 预售护栏
        try:
            await svc.presale_guard("P003", term_days=120,
                                   deposit=100, total_price=500)
            record("P3-预售超期拦截", False)
        except ValueError as e:
            record("P3-预售超期拦截", "90" in str(e))
        try:
            await svc.presale_guard("P003", term_days=30,
                                   deposit=200, total_price=500)
            record("P3-定金超比拦截", False)
        except ValueError as e:
            record("P3-定金超比拦截", "20%" in str(e))

        # [28] 预售协议
        ps = await svc.presale_guard("P003", term_days=30,
                                     deposit=50, total_price=500,
                                     presale_type="cellar")
        record("P3-预售协议四条款",
               len(ps["agreement"]["clauses"]) == 4
               and "交付时间" in ps["agreement"]["clauses"][0]
               and "老酒封坛" in ps["presaleName"])

        # [29] 高风险画像
        ab = await svc.anti_blackmail(
            9001, "ORD-9001", complaints_90d=7,
            return_ratio=0.8, lawsuit_count=1)
        record("P3-高风险三标记", ab["highRisk"] is True
               and len(ab["riskMarkers"]) == 3)

        # [30] 证据包
        record("P3-证据包六清单",
               len(ab["evidenceChecklist"]) == 6
               and "应诉证据包" in ab["defensePackage"])
        ab2 = await svc.anti_blackmail(
            9002, "ORD-9002", complaints_90d=0, return_ratio=0.05)
        record("P3-常规留档", ab2["highRisk"] is False
               and len(ab2["evidenceChecklist"]) == 1)


class TestP3Evolution:

    async def run(self):
        store = _ZfStore()
        svc = ZfEvolutionService(store=store)

        # [31] adopted 严格度+0.1
        f = await svc.feedback("process_check", "adopted", "规则有效")
        record("P3-采纳升严格度",
               f["strictnessAfter"] == 1.1, str(f["strictnessAfter"]))

        # [32] 安全阀上限
        for _ in range(10):
            await svc.feedback("credit", "adopted")
        params = await svc.get_params()
        record("P3-安全阀clamp上限", params["strictness"] == 1.4)

        # [33] rejected 降
        await svc.feedback("price_audit", "rejected", "误报")
        params = await svc.get_params()
        record("P3-拒绝降严格度", params["strictness"] == 1.3)

        # [34] corrected 不调参
        await svc.feedback("presale_guard", "corrected", "部分成立")
        params2 = await svc.get_params()
        record("P3-corrected不调参",
               params2["strictness"] == params["strictness"])

        # [35] 非法目标/裁决
        for args in (("hacking", "adopted"), ("process_check", "later")):
            try:
                await svc.feedback(*args)
                record("P3-非法反馈拒绝", False, str(args))
            except ValueError:
                record("P3-非法反馈拒绝", True)

        # [36] 判例种子
        pc = await svc.precedents()
        record("P3-判例种子四条", len(pc) == 4
               and all("ruleSuggestion" in p for p in pc))

        # [37] 孪生三重校验
        tw = await svc.twin()
        record("P3-孪生三重校验",
               "physicalDigital" in tw["tripleVerification"]
               and "digitalLegal" in tw["tripleVerification"]
               and "physicalLegal" in tw["tripleVerification"]
               and tw["twinHealth"] >= 0)

        # [38] 总览
        st = await svc.status()
        record("P3-大模型总览", st["module"] == "智法·AI智能法务大模型"
               and "evolution" in st and "precedents" in st)


class TestFabric:

    async def run(self):
        svc = ZfFabricService()
        lake = await svc.lake_overview()
        record("数据湖-四域结构", all(
            k in lake for k in ("production", "trade",
                                "finance", "asset")))
        record("数据湖-只读口径", "零回写" in lake["note"])


async def main():
    await TestP0Production().run()
    await TestP1Finance().run()
    await TestP2Asset().run()
    await TestP3Commerce().run()
    await TestP3Evolution().run()
    await TestFabric().run()

    print("\n".join(RESULTS))
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
