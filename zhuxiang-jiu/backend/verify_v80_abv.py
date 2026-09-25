"""v80 度数过滤 live 冒烟"""
import asyncio

import services.xiaozhu_wine_service as w

print("extract_abv(56度):", w.extract_abv("选一款56度的酒"))


async def main():
    r = await w.XiaozhuWineService().recommend(
        "选一款56度的酒")
    print("reply:", r.get("reply", "")[:120])
    for c in (r.get("cards") or [])[:2]:
        print("  card:", c.get("name"), c.get("price"))

asyncio.run(main())
