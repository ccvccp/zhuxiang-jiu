"""补轨迹: 模拟物流商回调(booked→picked), 验证用户侧查询链路"""
import asyncio

WAYBILL = "SF10261003ZY0001"


async def t():
    from services.logistics_service import LogisticsService
    svc = LogisticsService()
    await svc.add_track_callback(
        WAYBILL, "已下单", "booked", "商家已下单, 等待揽收", "泰安")
    await svc.add_track_callback(
        WAYBILL, "已揽收", "picked", "快递员已揽收包裹", "泰安")

    from repositories.logistics_repository import LogisticsRepository
    repo = LogisticsRepository()
    tracks = await repo.list_tracks(WAYBILL, limit=5)
    print("轨迹:", len(tracks), "条")
    for x in reversed(tracks):
        print("  -", str(x.get("trackTime", ""))[:19], "|",
              x.get("description", ""), "|", x.get("location", ""))


asyncio.run(t())
