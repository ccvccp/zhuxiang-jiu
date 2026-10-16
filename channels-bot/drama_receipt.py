# drama 首批回执登记模板(stdin 直传, 零引号)——发布成功+manage 拿到 URL 后填入执行
# 用法: 填好 RECEIPTS 后
#   Get-Content drama_receipt.py -Raw | ssh root@47.236.61.117 'docker exec -i zhuxiang-backend-1 python -'
import asyncio

# (contentId, 作品URL, publishId/作品号)——URL 从各平台 manage 核验取
# 10-07 00:16/00:18 深夜补发双成功(manage 已核验已发布), 口径对齐 68(页面级 URL)
RECEIPTS = [
    (70, "https://creator.douyin.com/creator-micro/content/manage", "drama-cctv-gala-soliloquy"),
    (77, "https://creator.douyin.com/creator-micro/content/manage", "drama-heritage-museum-soliloquy"),
]


async def main():
    import sys
    sys.path.insert(0, "/app")
    from services.promo_rpa_channel_service import PromoRpaChannelService
    svc = PromoRpaChannelService()
    for cid, url, nid in RECEIPTS:
        if "TODO" in url:
            print(cid, "SKIP(未填 URL)")
            continue
        r = await svc.mark_published(cid, url, nid)
        print(cid, "->", (r.get("receipt") or {}).get("mode"),
              (r.get("receipt") or {}).get("url"))


asyncio.run(main())
