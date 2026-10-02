"""引擎断层修复·生产实证
    1. 织物时效样本: 修复前 8(全演示单) → 修复后应含真实签收单
    2. carrier_performance 新增 recentSignHours(近30天)
    3. etaWeight 进化闭环: 调参后 ETA basis 变化(参数被消费)
"""
import asyncio


async def t():
    from services.zw_fabric_service import ZwFabricService
    fab = ZwFabricService()

    lake = await fab.lake_overview()
    print("① 织物时效:", lake["timeliness"],
          "(修复前样本8/62.75h 全演示单)")

    cp = await fab.carrier_performance()
    sf = cp.get("SF", {})
    print("② SF 聚合: 全期", sf.get("avgSignHours"), "h | 近30天",
          sf.get("recentSignHours"), "h (样本", sf.get("recentSample"),
          ") | 签收率", sf.get("signRate"))

    # ③ etaWeight 进化闭环: 调参 → ETA 消费
    from services.zw_analysis_service import ZwAnalysisService
    from services.zw_track_service import ZwTrackService
    an, tr = ZwAnalysisService(), ZwTrackService()

    p0 = await an.get_params()
    print("\n③ etaWeight 当前:", p0["etaWeight"])

    # 造一笔在途单测 ETA
    from services.logistics_service import LogisticsService
    lo = await LogisticsService().create_order(
        order_id="ZY-ETA-TEST-1", order_type="retail", carrier="SF",
        service_type="standard",
        sender={"name": "竹香九商城", "phone": "4000000000",
                "address": "山东泰安"},
        receiver={"name": "ETA测试", "phone": "13800000001",
                  "address": "济南市测试路", "province": "山东",
                  "city": "济南"},
        weight=1.5, piece_count=1, waybill_no="SF-ETA-TEST-0001")
    e1 = await tr.eta_predict("SF-ETA-TEST-0001")
    print("  ETA(参数默认):", e1["basis"], "| 剩余", e1["remainingHours"], "h")

    fb = await an.feedback(target_type="eta", verdict="adopted",
                           note="引擎闭环实证")
    p1 = await an.get_params()
    print("  反馈 adopted → etaWeight:", p0["etaWeight"], "→",
          p1["etaWeight"])
    e2 = await tr.eta_predict("SF-ETA-TEST-0001")
    print("  ETA(调参后):", e2["basis"], "| 剩余", e2["remainingHours"], "h")
    consumed = e2["basis"] != e1["basis"]
    print("  进化参数被 ETA 消费:", consumed)

    # 回滚参数(实证不留痕)
    await an.feedback(target_type="eta", verdict="rejected", note="回滚")
    p2 = await an.get_params()
    print("  回滚后 etaWeight:", p2["etaWeight"])


asyncio.run(t())
