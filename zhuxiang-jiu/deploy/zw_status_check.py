"""智运·AI智能物流大模型 生产体检(直调服务层)

对照交付总结(docs/智运物流大模型_升级上线交付总结.md)预期:
    1. 模式层: 生产档应为 assist(SOP 要求 .env ZW_MODE=assist)
    2. 护栏三指标状态
    3. 数据织物: lake_overview(订单/签收率/在途/成本——数据真实性)
    4. 物流商表现(carrier_performance)
    5. P3 孪生总览 / 大模型总览
    6. 轨迹: 延误/异常存量
"""
import asyncio


async def t():
    from services.zw_mode_service import ZwModeService
    from services.zw_fabric_service import ZwFabricService
    from services.zw_analysis_service import ZwAnalysisService
    from services.zw_track_service import ZwTrackService

    # 1. 模式层
    ms = ZwModeService()
    mode = await ms.current_mode()
    print("== 模式层 ==")
    for k in ("mode", "source", "guardPaused", "pausedReason"):
        print(f"  {k}: {mode.get(k)}")

    # 2. 数据织物
    fab = ZwFabricService()
    lake = await fab.lake_overview()
    print("\n== 数据织物 lake_overview ==")
    print(" ", {k: v for k, v in lake.items()
                if isinstance(v, (int, float, str))})

    cp = await fab.carrier_performance()
    print("\n== 物流商表现 ==")
    for name, d in list(cp.items())[:6]:
        print(f"  - {name}: {d}")

    # 3. P3 总览
    an = ZwAnalysisService()
    st = await an.status()
    print("\n== 大模型总览 ==")
    print(" ", {k: v for k, v in st.items()
                if isinstance(v, (int, float, str))})

    tw = await an.twin()
    print("\n== 孪生 ==")
    print(" ", {k: v for k, v in tw.items()
                if isinstance(v, (int, float, str))})

    # 4. 轨迹异常/延误
    tr = ZwTrackService()
    try:
        dl = await tr.delay_overview() if hasattr(
            tr, "delay_overview") else None
        print("\n== 延误总览 ==", dl)
    except Exception as ex:
        print("延误总览异常:", ex)
    try:
        anoms = await tr.anomalies() if hasattr(tr, "anomalies") else None
        print("== 异常清单 ==", len(anoms) if anoms is not None
              else "方法名不同")
    except Exception as ex:
        print("异常清单:", ex)


asyncio.run(t())
