"""全量扫描修复·生产验证 + 演示数据污染盘点"""
import asyncio


async def t():
    from repositories.logistics_repository import LogisticsRepository
    repo = LogisticsRepository()

    all_orders = await repo.list_orders(limit=None)
    demo = [o for o in all_orders
            if str(o.get("waybillNo", "")).startswith("ZW-DEMO")]
    real = [o for o in all_orders if o not in demo]
    print("① 全量扫描:", len(all_orders), "单 | 演示(ZW-DEMO*):",
          len(demo), "| 真实:", len(real))

    # 演示数据对各承运商评分的占比(污染度)
    from collections import Counter
    demo_c = Counter(o.get("carrier") for o in demo)
    real_c = Counter(o.get("carrier") for o in real)
    print("② 演示单承运商分布:", dict(demo_c))
    print("   真实单承运商分布:", dict(real_c))

    # 织物全量聚合正常
    from services.zw_fabric_service import ZwFabricService
    lake = await ZwFabricService().lake_overview()
    print("③ 织物聚合(全量):", lake["orders"], "|",
          lake["timeliness"])

    # 异常全量扫描
    from services.zw_track_service import ZwTrackService
    anoms = await ZwTrackService().detect_anomalies(limit=100)
    print("④ 异常扫描(全量):", len(anoms), "条")


asyncio.run(t())
