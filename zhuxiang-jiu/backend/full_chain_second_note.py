"""RPA 全链验证 step2: 热点198第二篇生成→过审→入队→出队"""
import asyncio
from datetime import datetime, UTC


async def main():
    from services.promo_service import PromoService
    svc = PromoService()

    rows = await svc.generate_contents(
        198, platforms=("xiaohongshu",))
    print("生成:", len(rows), "条")
    c = rows[0] if rows else None
    if not c:
        return
    cid = c.get("contentId")
    print("contentId:", cid)
    print("标题:", c.get("title"))
    print("正文:", str(c.get("body"))[:120])
    print("合规分:", c.get("complianceScore"),
          "| agentTrace:", c.get("agentTrace"))

    r = await svc.review_content(cid, approved=True,
                                  reviewer="站长")
    print("过审:", r.get("status"))
    now = datetime.now(UTC).isoformat()
    q = await svc.publish_content(cid, publish_at=now)
    print("入队:", q.get("status"))
    pub = await svc.process_publish_queue()
    for p in pub:
        rec = p.get("receipt") or {}
        print("出队:", p.get("contentId"),
              "| receipt:", rec.get("mode"))

    from services.promo_rpa_channel_service import (
        PromoRpaChannelService,
    )
    pend = await PromoRpaChannelService().list_pending()
    print("\nRPA 清单:", len(pend))
    for row in pend:
        print("  contentId:", row["contentId"],
              "| coverUrl:", row["coverUrl"])
        print("  标题:", row["title"][:40])


asyncio.run(main())
