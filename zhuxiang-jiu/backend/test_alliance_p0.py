"""37号·AI智能网站同盟模块·P0 端到端测试(入盟→上货→交易→分润→评价)

覆盖(设计文档 §2.1-§2.6 P0):
    1. 入盟网关: 超级会员门槛/AI预审三档/人工审核签约/状态机全链路
       (signed→probation→active→suspend/terminate)/清退冷却90天
    2. 商品中心: 三道门禁(溯源: 酒类须放行批次+非酒类须凭证;
       合规: 禁用词)/商户非在售态拒绝/库存原子扣减
    3. 交易分润: 15%抽佣五方拆账/舍入平账/T+1结算幂等/冲正/
       role总账双写/wallet货款入账
    4. 评价: 结算后一单一评/星级聚合/折叠重算
    5. 报表: 全景/类目维度
    6. 分润配置: 比例校验/更新

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_alliance_p0.py
"""

import asyncio
import os
import sys


# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.alliance_service import AllianceService
from repositories.alliance_repository import (
    AllianceRepository,
    CATEGORY_WINE, CATEGORY_TEA, CATEGORY_VENUE,
    STATUS_PENDING, STATUS_MANUAL_REVIEWING, STATUS_SIGNED,
    STATUS_PROBATION, STATUS_ACTIVE, STATUS_SUSPENDED,
    STATUS_TERMINATED, STATUS_REJECTED,
    PRODUCT_STATUS_ACTIVE, PRODUCT_STATUS_OFFLINE,
    SETTLEMENT_STATUS_SETTLED, SETTLEMENT_STATUS_REVERSED,
    DEFAULT_SHARE_RATES,
    REVIEW_MIN_SCORE, REVIEW_MAX_SCORE,
)
from repositories.member_repository import MemberRepository
from repositories.trace_prod_repository import TraceProdRepository

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


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


async def _create_member(level: int = 5, member_id_hint: int = None):
    """造一个指定等级会员(实名字段用于 AI 预审)"""
    repo = MemberRepository()
    phone = f"138{level:02d}{(member_id_hint or 0):08d}"[-11:]
    return await repo.create({"phone": phone, "name": f"Lv{level}会员",
                              "level": level, "realnameVerified": True})


async def _active_merchant(svc: AllianceService, category: str = CATEGORY_TEA,
                           level: int = 5, referrer: int = None,
                           member_id_hint: int = None) -> dict:
    """快速通道: 造会员→申请→审核→签约→激活→转正, 返回 active 商户"""
    member = await _create_member(level, member_id_hint)
    app = await svc.apply(member["id"], category, "测试茶庄",
                          credentials=["产地凭证"],
                          referrer_member_id=referrer)
    await svc.audit_application(app["applicationId"], approved=True)
    merchant = await svc.repo.find_merchant_by_member(member["id"])
    await svc.activate_merchant(merchant["merchantId"])
    return await svc.confirm_merchant(merchant["merchantId"])


class TestOnboarding:
    async def run(self):
        svc = AllianceService()
        await svc.list_categories()

        # 门槛: 等级不足(Lv2 < 4)
        low_member = await _create_member(level=2)
        try:
            await svc.apply(low_member["id"], CATEGORY_TEA, "小茶庄")
            record("入盟-等级不足409", False)
        except ValueError as e:
            record("入盟-等级不足409", "等级不足" in str(e), str(e))

        # 类目无效
        member = await _create_member(level=5, member_id_hint=1)
        try:
            await svc.apply(member["id"], "coffee", "咖啡铺")
            record("入盟-无效类目409", False)
        except ValueError:
            record("入盟-无效类目409", True)

        # 正常申请(Lv5+实名+资质齐) → AI 预审高分管道
        app = await svc.apply(member["id"], CATEGORY_TEA, "竹里茶庄",
                              credentials=["产地凭证"],
                              referrer_member_id=888)
        record("申请-AI预审高分进人工审",
               app["status"] == STATUS_MANUAL_REVIEWING
               and app["aiReview"]["score"] >= 80,
               f"score={app['aiReview']['score']} status={app['status']}")
        record("申请-AI报告含因子明细",
               len(app["aiReview"]["factors"]) == 5)

        # AI 预审低分直拒(Lv4 未实名 + 无资质)
        weak = await _create_member(level=4, member_id_hint=2)
        repo = MemberRepository()
        await repo.update_fields(weak["id"], {"realnameVerified": False})
        rejected_app = await svc.apply(weak["id"], CATEGORY_VENUE,
                                       "无证会所", credentials=[])
        record("申请-AI低分直拒",
               rejected_app["status"] == STATUS_REJECTED
               and rejected_app["aiReview"]["score"] < 60,
               f"score={rejected_app['aiReview']['score']}")

        # 重复在盟申请拒绝(同会员同类目再申请)
        try:
            await svc.apply(member["id"], CATEGORY_TEA, "重复申请")
            record("申请-重复在盟409", False)
        except ValueError:
            record("申请-重复在盟409", True)

        # 人工审核: 通过 → 签约建档
        audited = await svc.audit_application(app["applicationId"],
                                              approved=True, reviewer="运营A")
        record("审核-通过签约",
               audited["status"] == STATUS_SIGNED)
        merchant = await svc.repo.find_merchant_by_member(member["id"])
        record("审核-签约建档",
               merchant is not None
               and merchant["status"] == STATUS_SIGNED
               and merchant["category"] == CATEGORY_TEA
               and merchant["referrerMemberId"] == 888)
        # 重复审核 → 409
        try:
            await svc.audit_application(app["applicationId"], approved=False)
            record("审核-重复审核409", False)
        except ValueError:
            record("审核-重复审核409", True)

        # 状态机: signed→probation→active
        await svc.activate_merchant(merchant["merchantId"])
        m = await svc.get_merchant(merchant["merchantId"])
        record("状态机-激活试用", m["status"] == STATUS_PROBATION)
        await svc.confirm_merchant(merchant["merchantId"])
        m = await svc.get_merchant(merchant["merchantId"])
        record("状态机-试用转正", m["status"] == STATUS_ACTIVE)

        # 非法转移: active 直接 signed → 409
        try:
            svc._transition(m, STATUS_SIGNED)
            record("状态机-非法转移409", False)
        except ValueError:
            record("状态机-非法转移409", True)


class TestProducts:
    async def run(self):
        svc = AllianceService()
        merchant = await _active_merchant(svc, CATEGORY_TEA)

        # 非酒类: 缺溯源凭证 → 409
        try:
            await svc.create_product(merchant["merchantId"], "明前龙井",
                                     "高山云雾茶", 380.0, 50)
            record("商品-缺溯源凭证409", False)
        except ValueError as e:
            record("商品-缺溯源凭证409", "溯源凭证" in str(e), str(e))

        # 非酒类: 带凭证上架成功
        product = await svc.create_product(
            merchant["merchantId"], "明前龙井", "高山云雾茶, 回甘持久",
            380.0, 50, trace_credentials=["批次HC2026", "产地西湖"])
        record("商品-茶类上架成功",
               product["status"] == PRODUCT_STATUS_ACTIVE
               and product["sku"].startswith("AL-TE")
               and product["trace"]["credentials"])
        record("商品-价格库存正确",
               product["price"] == 380.0 and product["stock"] == 50)

        # 合规门禁: 禁用词
        try:
            await svc.create_product(
                merchant["merchantId"], "史上最好的茶", "", 100.0, 10,
                trace_credentials=["批次x"])
            record("商品-禁用词409", False)
        except ValueError as e:
            record("商品-禁用词409", "禁用词" in str(e), str(e))

        # 酒类: 未绑定批次 → 409
        wine_merchant = await _active_merchant(svc, CATEGORY_WINE,
                                               member_id_hint=3)
        try:
            await svc.create_product(wine_merchant["merchantId"], "陈年佳酿",
                                     "窖藏老酒", 880.0, 10)
            record("商品-酒类缺批次409", False)
        except ValueError as e:
            record("商品-酒类缺批次409", "溯源批次号" in str(e), str(e))

        # 酒类: 批次未放行 → 409(造 producing 批次)
        trace_repo = TraceProdRepository()
        await trace_repo.save_batch({
            "batchNo": "BATCH-UNRELEASED", "batchId": 1,
            "status": "producing", "currentStageSeq": 3,
            "lifeCodes": [], "createdAt": ""})
        try:
            await svc.create_product(
                wine_merchant["merchantId"], "陈年佳酿", "窖藏老酒",
                880.0, 10, trace_batch_no="BATCH-UNRELEASED")
            record("商品-批次未放行409", False)
        except ValueError as e:
            record("商品-批次未放行409", "未放行" in str(e), str(e))

        # 酒类: 已放行批次 → 上架成功且溯源验证
        await trace_repo.update_batch("BATCH-UNRELEASED",
                                      {"status": "released"})
        wine = await svc.create_product(
            wine_merchant["merchantId"], "陈年佳酿", "窖藏老酒",
            880.0, 10, trace_batch_no="BATCH-UNRELEASED")
        record("商品-酒类放行批次上架",
               wine["trace"]["traceVerified"] is True
               and wine["trace"]["batchNo"] == "BATCH-UNRELEASED")

        # 商户暂停态不可上架
        await svc.suspend_merchant(wine_merchant["merchantId"], "测试暂停")
        try:
            await svc.create_product(
                wine_merchant["merchantId"], "再上架", "", 100.0, 1,
                trace_batch_no="BATCH-UNRELEASED")
            record("商品-暂停态上架409", False)
        except ValueError:
            record("商品-暂停态上架409", True)
        # 暂停联动: 在售商品自动下架
        suspended_wine = await svc.get_product(wine["productId"])
        record("商品-商户暂停联动下架",
               suspended_wine["status"] == PRODUCT_STATUS_OFFLINE)

        # 下架商品重复下架 → 409
        try:
            await svc.offline_product(wine["productId"])
            record("商品-重复下架409", False)
        except ValueError:
            record("商品-重复下架409", True)


class TestTradeAndSettlement:
    async def run(self):
        svc = AllianceService()
        merchant = await _active_merchant(svc, CATEGORY_TEA,
                                          member_id_hint=5, referrer=888)
        # 商户会员预开钱包(货款入账通道; 仓储层直建避开成长值联动门槛)
        from repositories.wallet_repository import (
            WalletRepository, STATUS_ACTIVE,
        )
        await WalletRepository().save_account(merchant["memberId"], {
            "userId": merchant["memberId"], "status": STATUS_ACTIVE,
            "balance": 0.0, "rewardBalance": 0.0,
            "totalDeposit": 0.0, "totalWithdraw": 0.0, "createdAt": ""})

        # 分润预览: 100 元 → 15 佣金五方拆账
        preview = await svc.preview_shares(100.0)
        record("分润-15%抽佣",
               preview["commission"] == 15.0
               and preview["merchantProceeds"] == 85.0,
               f"实际{preview}")
        record("分润-五方拆账比例",
               preview["shares"] == {"platform": 6.0,
                                     "category_service": 3.0,
                                     "referrer": 2.25,
                                     "city_store": 2.25,
                                     "development_fund": 1.5},
               f"实际{preview['shares']}")
        record("分润-五方合计=佣金",
               abs(sum(preview["shares"].values()) - 15.0) < 1e-6)

        # 下单: 原子扣库存
        product = await svc.create_product(
            merchant["merchantId"], "金骏眉", "红茶", 200.0, 5,
            trace_credentials=["批次JJ"])
        order = await svc.place_order(product["productId"], 66601,
                                      quantity=2)
        record("交易-下单成功", order["status"] == "paid"
               and order["amount"] == 400.0)
        record("交易-库存扣减", (await svc.get_product(
            product["productId"]))["stock"] == 3)

        # 库存不足 → 409
        try:
            await svc.place_order(product["productId"], 66601, quantity=10)
            record("交易-库存不足409", False)
        except ValueError:
            record("交易-库存不足409", True)

        # 未结算不可评价
        try:
            await svc.submit_review(order["orderId"], 66601, 5)
            record("交易-未结算评价409", False)
        except ValueError:
            record("交易-未结算评价409", True)

        # 结算: 拆账+总账+货款
        settlement = await svc.settle_order(order["orderId"])
        record("结算-佣金与货款",
               settlement["orderAmount"] == 400.0
               and settlement["commission"] == 60.0
               and settlement["merchantProceeds"] == 340.0,
               f"实际commission={settlement['commission']}")
        record("结算-五方总账双写",
               len(settlement["ledgerEntries"]) == 5
               and all(e["created"] for e in settlement["ledgerEntries"]),
               f"实际{settlement['ledgerEntries']}")
        record("结算-货款wallet入账",
               bool(settlement["walletTxNo"])
               and settlement["walletTxNo"] not in
               ("PENDING_NO_WALLET", "FAILED"),
               f"实际{settlement['walletTxNo']}")

        # 总账落库验证(role profit_ledger, source_module=alliance)
        from repositories.role_repository import RoleRepository
        ledgers = await RoleRepository().list_ledgers(limit=1000)
        alliance_ledgers = [l for l in ledgers
                            if l.get("sourceModule") == "alliance"]
        record("结算-role总账落库",
               len(alliance_ledgers) == 5
               and abs(sum(l["amount"] for l in alliance_ledgers)
                       - 60.0) < 1e-6,
               f"实际{len(alliance_ledgers)}条")

        # 结算幂等
        again = await svc.settle_order(order["orderId"])
        record("结算-幂等", again["settlementId"]
               == settlement["settlementId"])

        # 冲正
        reversed_settle = await svc.reverse_settlement(
            order["orderId"], reason="测试退款")
        record("结算-冲正",
               reversed_settle["status"] == SETTLEMENT_STATUS_REVERSED)
        try:
            await svc.reverse_settlement(order["orderId"])
            record("结算-重复冲正409", False)
        except ValueError:
            record("结算-重复冲正409", True)

        # T+1 调度: 新单未过窗口 → skip
        order2 = await svc.place_order(product["productId"], 66602,
                                       quantity=1)
        scheduled = await svc.run_scheduled_settlement()
        record("结算-T+1未过窗口跳过",
               order2["orderId"] not in scheduled["settled"],
               f"实际{scheduled}")

        # 分润配置更新: 比例和≠1 → 409
        try:
            await svc.update_share_settings(
                share_rates={"platform": 0.5, "category_service": 0.2,
                             "referrer": 0.1, "city_store": 0.1,
                             "development_fund": 0.2})
            record("配置-比例和≠1拒绝409", False)
        except ValueError:
            record("配置-比例和≠1拒绝409", True)
        # 合法更新生效
        await svc.update_share_settings(
            share_rates={"platform": 0.5, "category_service": 0.2,
                         "referrer": 0.1, "city_store": 0.1,
                         "development_fund": 0.1})
        new_preview = await svc.preview_shares(100.0)
        record("配置-更新生效",
               new_preview["shares"]["platform"] == 7.5,
               f"实际{new_preview['shares']}")
        # 还原默认
        await svc.update_share_settings(
            share_rates=dict(DEFAULT_SHARE_RATES))


class TestReviews:
    async def run(self):
        svc = AllianceService()
        merchant = await _active_merchant(svc, CATEGORY_TEA,
                                          member_id_hint=7)
        product = await svc.create_product(
            merchant["merchantId"], "普洱", "云南普洱", 150.0, 10,
            trace_credentials=["批次PE"])
        order = await svc.place_order(product["productId"], 77701)
        await svc.settle_order(order["orderId"])

        # 非购买者评价 → 409
        try:
            await svc.submit_review(order["orderId"], 99999, 5)
            record("评价-非购买者409", False)
        except ValueError:
            record("评价-非购买者409", True)

        # 评分越界 → 409
        try:
            await svc.submit_review(order["orderId"], 77701, 6)
            record("评价-评分越界409", False)
        except ValueError:
            record("评价-评分越界409", True)

        # 正常评价(5星)
        review = await svc.submit_review(order["orderId"], 77701, 5,
                                         "茶香浓郁")
        record("评价-提交成功",
               review["score"] == 5 and not review["folded"])

        # 一单一评
        try:
            await svc.submit_review(order["orderId"], 77701, 4)
            record("评价-重复评价409", False)
        except ValueError:
            record("评价-重复评价409", True)

        # 星级聚合
        rating = await svc.get_merchant_rating(merchant["merchantId"])
        record("评价-星级聚合",
               rating["ratingAvg"] == 5.0
               and rating["ratingCount"] == 1
               and rating["distribution"]["5"] == 1,
               f"实际{rating}")

        # 折叠 → 重算聚合(无未折叠评价 → 均分0)
        await svc.fold_review(review["reviewId"], "测试折叠")
        rating2 = await svc.get_merchant_rating(merchant["merchantId"])
        record("评价-折叠重算聚合",
               rating2["ratingCount"] == 0 and rating2["ratingAvg"] == 0.0,
               f"实际{rating2}")
        try:
            await svc.fold_review(review["reviewId"])
            record("评价-重复折叠409", False)
        except ValueError:
            record("评价-重复折叠409", True)


class TestTerminationAndReport:
    async def run(self):
        svc = AllianceService()
        merchant = await _active_merchant(svc, CATEGORY_TEA,
                                          member_id_hint=9)
        # 上架商品后终止 → 商品联动下架 + 90 天冷却
        product = await svc.create_product(
            merchant["merchantId"], "毛峰", "绿茶", 120.0, 10,
            trace_credentials=["批次MF"])
        terminated = await svc.terminate_merchant(merchant["merchantId"],
                                                  "主动退出")
        record("退出-终止成功",
               terminated["status"] == STATUS_TERMINATED)
        record("退出-商品联动下架",
               (await svc.get_product(product["productId"]))["status"]
               == PRODUCT_STATUS_OFFLINE)

        # 冷却期内重新申请 → 409
        try:
            await svc.apply(merchant["memberId"], CATEGORY_TEA, "再入盟",
                            credentials=["产地凭证"])
            record("退出-冷却期409", False)
        except ValueError as e:
            record("退出-冷却期409", "冷却期" in str(e), str(e))

        # 报表
        overview = await svc.report_overview()
        record("报表-全景结构",
               {"merchants", "products", "orders",
                "settlements"} <= set(overview))
        category_rows = await svc.report_category()
        record("报表-八类目维度", len(category_rows) == 8
               and all({"category", "merchants", "products", "gmv",
                        "commission"} <= set(r) for r in category_rows))
        tea_row = next(r for r in category_rows
                       if r["category"] == CATEGORY_TEA)
        record("报表-茶类目数据", tea_row["merchants"] >= 1)


async def main():
    test_classes = [
        ("入盟网关与状态机", TestOnboarding),
        ("商品三道门禁", TestProducts),
        ("交易与15%分润", TestTradeAndSettlement),
        ("评价信用", TestReviews),
        ("退出冷却与报表", TestTerminationAndReport),
    ]
    print("=" * 62)
    print("37号·AI智能网站同盟模块 P0 端到端测试")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, str(e))

    print("\n" + "-" * 62)
    for line in RESULTS:
        print(line)
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) and 1 or 0)
