"""v81 商品定位前置 live 冒烟"""
import asyncio

import services.xiaozhu_service as xs
import services.xiaozhu_wine_service as wv


async def main():
    r1 = await wv.XiaozhuWineService().recommend(
        "推荐竹香珍藏")
    print("[指名直入]", r1.get("reply", "")[:90])
    r2 = await xs.XiaozhuService()._product_miss_reply("茅台")
    print("[miss回答]", r2.get("reply", "")[:90])
    r3 = await wv.XiaozhuWineService().recommend(
        "推荐一款好喝的")
    print("[泛词继续过滤]", r3.get("reply", "")[:60])


asyncio.run(main())
