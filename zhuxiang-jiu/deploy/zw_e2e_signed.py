"""智运完善·生产端到端验证②: 签收闭环 + 风控前置

场景: 上一轮建的 ZY-E2E-1003/SF10261003ZY0001(状态 picked)
    1. 模拟确认收货 → 物流单逐级推进到 signed + 轨迹收尾
    2. 数据织物签收率变化(真实闭环)
    3. 风控前置评分(四防)——路由挂接同款参数
    4. GET risk/assesses 留痕查询(看板数据源)
"""
import asyncio


async def t():
    # 1. 签收闭环(等价 _sync_logistics_signed 逻辑)
    from services.logistics_service import LogisticsService
    svc = LogisticsService()
    lo = await svc.get_order_by_order_id("ZY-E2E-1003")
    print("① 物流单当前状态:", lo["status"])
    steps = (("picked", "快递员已揽收包裹"),
             ("transporting", "包裹运输中"),
             ("delivering", "包裹派送中"),
             ("signed", "用户确认收货"))
    for status, desc in steps:
        try:
            await svc.add_track_callback(
                lo["waybillNo"], desc, status, desc,
                lo.get("city", ""), operator="member")
        except (KeyError, ValueError):
            continue
    lo2 = await svc.get_order_by_order_id("ZY-E2E-1003")
    print("② 确认收货后状态:", lo2["status"], "(签收闭环:",
          lo2["status"] == "signed", ")")
    tracks = await svc.list_tracks("SF10261003ZY0001")
    print("③ 轨迹:", len(tracks), "条")
    for x in reversed(tracks):
        print("   ", str(x.get("trackTime", ""))[5:16], "|",
              x.get("description", ""))

    # 2. 数据织物签收聚合变化
    from services.zw_fabric_service import ZwFabricService
    cp = await ZwFabricService().carrier_performance()
    sf = cp.get("SF", {})
    print("④ SF 织物聚合: 签收率", sf.get("signRate"),
          "| 样本", sf.get("total"),
          "| 均时效", sf.get("avgSignHours"), "h")

    # 3. 风控前置
    from services.zw_risk_service import ZwRiskService
    risk = await ZwRiskService().risk_assess(
        order_type="retail", weight=6.0, piece_count=2,
        insured_value=1200, receiver_province="新疆")
    print("⑤ 四防评分:", risk["riskScore"], "(", risk["riskLevel"], ")")
    print("   破损", risk["damageScore"], "丢失", risk["lossScore"],
          "延误", risk["delayScore"])
    for s in risk["suggestions"]:
        print("   建议:", s)

    # 4. 留痕查询(看板数据源)
    from services.zw_fabric_service import _ZwStore
    rows = await _ZwStore().list("risk_assess", limit=5)
    print("⑥ 风控留痕:", len(rows), "条(最新 riskId:",
          max((r.get("riskId", 0) for r in rows), default=0), ")")


asyncio.run(t())
