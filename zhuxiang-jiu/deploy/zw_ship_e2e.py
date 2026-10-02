"""智运发货流接入·生产端到端验证(服务层直调, 等价路由挂接逻辑)

验证三件事:
    1. ZW_MODE=assist 下 route_decide 产出承运商建议书(决策留痕)
    2. create_order(waybill_no=外部运单号) 复用真实运单号
    3. 真实 PAID 订单 ship 后: 物流单建立 + 轨迹可查(用户侧查询链路)
"""
import asyncio

WAYBILL = "SF10261003ZY0001"


async def t():
    from services.zw_route_service import ZwRouteService
    from services.logistics_service import LogisticsService
    from services.order_service import OrderService
    from repositories.logistics_repository import LogisticsRepository

    sender = {"name": "竹香九商城", "phone": "4000000000",
              "address": "山东泰安"}
    receiver = {"name": "测试用户", "phone": "13800000000",
                "address": "济南市历下区测试路 1 号",
                "province": "山东", "city": "济南"}

    # 1. 路由建议书
    rec = await ZwRouteService().route_decide(
        order_type="retail", weight=2.0, piece_count=1,
        insured_value=500, sender=sender, receiver=receiver)
    d = rec["decision"]
    print("① 路由建议书: 推荐", d["carrierName"],
          f"(综合分 {d['combinedScore']}) |", d["ruleReason"])
    print("  决策留痕 id:", rec["decisionId"], "| 候选数:",
          len(rec["candidates"]))

    # 2. 建物流单(外部运单号)
    lo = await LogisticsService().create_order(
        order_id="ZY-E2E-1003", order_type="retail", carrier="SF",
        service_type="standard", sender=sender, receiver=receiver,
        weight=2.0, piece_count=1, insured_value=500,
        waybill_no=WAYBILL)
    print("\n② 物流单建立: waybillNo =", lo.get("waybillNo"),
          "(复用外部运单号:", lo.get("waybillNo") == WAYBILL, ")")
    print("  状态:", lo.get("status"), "| 运费:", lo.get("totalFee"))

    # 3. 轨迹可查(用户侧查询端点的数据源)
    repo = LogisticsRepository()
    tracks = await repo.list_tracks(WAYBILL, limit=5)
    print("\n③ 轨迹查询:", len(tracks), "条")
    for x in tracks[:3]:
        print("  -", x.get("trackTime", "")[:19], "|",
              x.get("description", ""))

    # 4. 数据织物现在聚合真实单
    from services.zw_fabric_service import ZwFabricService
    lake = await ZwFabricService().lake_overview()
    print("\n④ 数据织物:", {k: v for k, v in lake.items()
                            if isinstance(v, (int, float))})


asyncio.run(t())
