"""RPA 全链验证 step1: 雷达扫描 + 挑品牌相关热点"""
import asyncio


async def main():
    from services.promo_service import PromoService
    from repositories.promo_repository import (
        PromoRepository, HOTSPOT_STATUS_ACTIVE,
    )
    r = await PromoService().scan()
    print("扫描:", {k: r.get(k) for k in (
        "scanned", "new", "discarded", "skipped")})
    repo = PromoRepository()
    rows = await repo.list_hotspots(
        status=HOTSPOT_STATUS_ACTIVE, limit=100)
    kw = ("酒", "宴", "节", "礼", "聚会", "团圆", "婚", "国风",
          "送礼", "庆典")
    hits = [h for h in rows
            if any(k in str(h.get("title", "")) for k in kw)]
    print("active:", len(rows), "| 品牌相关:", len(hits))
    for h in hits[:5]:
        print("  ", h.get("hotspotId"), "|", h.get("platform"),
              "| score:", h.get("score"),
              "|", h.get("title", "")[:40])


asyncio.run(main())
