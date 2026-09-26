"""内容 26 过审 → 入队 → 立即出队(rpa_pending 验证)"""
import asyncio
from datetime import datetime, UTC


async def main():
    from services.promo_service import PromoService
    svc = PromoService()

    # 1. 人工审核通过(HITL——用户对话中已批准)
    r = await svc.review_content(
        26, approved=True, reviewer="站长")
    print("审核:", r.get("status"), "| reviewer:",
          r.get("reviewer") or r.get("reviewedBy"))

    # 2. 入队(立即时间——不等黄金时段, 链路验证)
    now = datetime.now(UTC).isoformat()
    q = await svc.publish_content(26, publish_at=now)
    print("入队:", q.get("status"),
          "| scheduledAt:", q.get("scheduledAt"))

    # 3. 出队处理(调度器等效直调)
    published = await svc.process_publish_queue()
    print("\n出队处理:", len(published), "条")
    for p in published:
        receipt = p.get("receipt") or {}
        print("  contentId:", p.get("contentId"),
              "| status:", p.get("status"))
        print("  回执 mode:", receipt.get("mode"),
              "| error:", receipt.get("error"))

    # 4. RPA 待发布清单
    from services.promo_rpa_channel_service import (
        PromoRpaChannelService,
    )
    rows = await PromoRpaChannelService().list_pending()
    print("\nRPA 待发布清单:", len(rows), "条")
    for row in rows:
        print("  contentId:", row["contentId"],
              "| 标题:", row["title"][:36])


asyncio.run(main())
