"""挑品牌相关真实热点(小红书内容生成用)"""
import asyncio


async def main():
    from repositories.promo_repository import (
        PromoRepository,
        HOTSPOT_STATUS_ACTIVE,
    )
    repo = PromoRepository()
    rows = await repo.list_hotspots(
        status=HOTSPOT_STATUS_ACTIVE, limit=100)
    print("active 热点:", len(rows))
    # 品牌相关词(酒/宴/节/礼/聚会/年货/送礼)
    kw = ("酒", "宴", "节", "礼", "聚会", "年货",
          "送礼", "团圆", "婚", "庆")
    hits = [h for h in rows
            if any(k in str(h.get("title", "")) for k in kw)]
    print("品牌相关命中:", len(hits))
    for h in hits[:8]:
        print("  hotspotId:", h.get("hotspotId"),
              "| score:", h.get("score"),
              "| heat:", h.get("heat"),
              "| platform:", h.get("platform"))
        print("    标题:", h.get("title", "")[:44])
    if not hits:
        print("\n最近 active 前 10(参考):")
        for h in rows[:10]:
            print("  ", h.get("hotspotId"),
                  h.get("score"), h.get("title", "")[:40])


asyncio.run(main())
