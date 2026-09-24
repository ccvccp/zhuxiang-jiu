"""容器内: 智图 POI 生产数据量查询(map.nearby 接入前勘察)"""
import asyncio

from services.zt_resource_service import ZtResourceService


async def main():
    pois = await ZtResourceService().fabric.list_pois()
    print(f"POI总数: {len(pois)}")
    for p in pois[:5]:
        print(f"  {p.get('poiCode')} | {p.get('name')}"
              f" | {p.get('poiType')}"
              f" | ({p.get('longitude')},{p.get('latitude')})"
              f" | open={p.get('open')}")

asyncio.run(main())
