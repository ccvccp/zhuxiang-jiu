"""v81 指名直入调试"""
import asyncio
import os

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"


async def main():
    from services.xiaozhu_wine_service import match_scene
    print("scene =", match_scene("推荐竹香经典"))
    from services.xiaozhu_wine_service import (
        XiaozhuWineService,
    )
    r = await XiaozhuWineService().recommend(
        "推荐竹香经典")
    print("result keys:", list(r.keys()))
    print("reply:", str(r.get("reply"))[:80])
    print("cards n =", len(r.get("cards") or []))
    for c in (r.get("cards") or [])[:2]:
        print("  -", c.get("name"))


asyncio.run(main())
