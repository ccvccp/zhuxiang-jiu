"""P1 冒烟: 主动触发雷达扫描验证百度真实热点拉取"""
import asyncio


async def main():
    from services.promo_radar_service import (
        PromoRadarService, _fetch_real,
    )
    # 直连层: 真实拉取(不打库)
    items = _fetch_real("baidu")
    print("baidu 真实拉取:", "None" if items is None
          else f"{len(items)} 条")
    if items:
        for it in items[:5]:
            print("  -", it["title"][:30],
                  "| heat:", it["heat"])
    # 全链: scan 一轮(评分/否决/去重/入库)
    from services.promo_service import PromoService
    r = await PromoService().scan()
    print("\nscan 结果:", r)


asyncio.run(main())
