"""RPA 发布 SOP 第一步: 拉待发布清单"""
import asyncio


async def main():
    from services.promo_rpa_channel_service import (
        PromoRpaChannelService,
    )
    rows = await PromoRpaChannelService().list_pending()
    print("待 RPA 发布内容:", len(rows), "条")
    for r in rows:
        print("  contentId:", r["contentId"],
              "| platform:", r["platform"])
        print("    标题:", r["title"][:40])
        print("    话题:", r["hashtags"],
              "| 短码:", r["shortCode"],
              "| 合规分:", r["complianceScore"])


asyncio.run(main())
