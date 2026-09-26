"""RPA 发布回执登记(contentId 26——首次真实发布闭环)"""
import asyncio


async def main():
    from services.promo_rpa_channel_service import (
        PromoRpaChannelService,
    )
    svc = PromoRpaChannelService()
    saved = await svc.mark_published(
        26,
        note_url=("https://www.xiaohongshu.com/explore/"
                  "6ab79ece000000001301aeae"),
        note_id="6ab79ece000000001301aeae")
    r = saved.get("receipt") or {}
    print("登记完成:")
    print("  mode:", r.get("mode"))
    print("  url:", r.get("url"))
    print("  publishId:", r.get("publishId"))
    print("  rpaCompletedAt:", r.get("rpaCompletedAt"))

    # 清单应归零
    rows = await svc.list_pending()
    print("\nRPA 待发布清单(登记后):", len(rows), "条")


asyncio.run(main())
