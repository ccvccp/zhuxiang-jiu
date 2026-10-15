"""容器内: map.nearby 生产验证(指令表+播报)"""
import asyncio

from services.xiaozhu_service import COMMANDS
from services.xiaozhu_map_service import XiaozhuMapService

print(f"指令数: {len(COMMANDS)}")
print(f"map.nearby 注册: "
      f"{any(c['action'] == 'map.nearby' for c in COMMANDS)}")


async def main():
    r = await XiaozhuMapService().nearby(
        "附近哪有卖竹香酒的", None)
    print("播报:", r["reply"][:120])

asyncio.run(main())
