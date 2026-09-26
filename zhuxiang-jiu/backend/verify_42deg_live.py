"""42 度指令生产 live 验证(v86 修复后)"""
import asyncio

import services.xiaozhu_wine_service as wv


async def main():
    for deg, want in ((42, 42), (43, 42), (56, 53)):
        r = await wv.XiaozhuWineService().recommend(
            f"选一款{deg}度的酒")
        items = ((r.get("card") or {}).get("items") or [])
        got = [float(i.get("alcohol") or 0)
               for i in items if i.get("alcohol")]
        flap = "数据源波动" in str(r.get("reply"))
        print(f"{deg}度 -> alcohols={got} "
              f"want={want} flap={flap} "
              f"{'OK' if (got and got[0] == want and not flap) else 'FAIL'}")


asyncio.run(main())
