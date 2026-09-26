"""v87 同音容错 live 验证"""
import asyncio

import services.xiaozhu_wine_service as wv


async def main():
    print("extract_abv('看一款32的。') =",
          wv.extract_abv("看一款32的。"))
    r = await wv.XiaozhuWineService().recommend("看一款32的。")
    print("reply:", str(r.get("reply"))[:80])
    r2 = await wv.XiaozhuWineService().recommend(
        "看一款52度的酒。")
    print("reply:", str(r2.get("reply"))[:80])


asyncio.run(main())
