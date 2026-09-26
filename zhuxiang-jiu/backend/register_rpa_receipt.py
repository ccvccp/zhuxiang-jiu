"""RPA 回执登记(contentId 28——无人干预闭环)"""
import asyncio


async def main():
    from services.promo_rpa_channel_service import (
        PromoRpaChannelService,
    )
    svc = PromoRpaChannelService()
    saved = await svc.mark_published(
        28,
        note_url=("https://www.xiaohongshu.com/explore/"
                  "6ab7a9ab000000001500d657"),
        note_id="6ab7a9ab000000001500d657")
    r = saved.get("receipt") or {}
    print("登记: mode=", r.get("mode"), "| url=", r.get("url"))
    rows = await svc.list_pending()
    print("RPA 清单(登记后):", len(rows), "条")


asyncio.run(main())
