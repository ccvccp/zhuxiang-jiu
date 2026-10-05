# drama 首批回执登记模板(stdin 直传, 零引号)——发布成功+manage 拿到 URL 后填入执行
# 用法: 填好 RECEIPTS 后
#   Get-Content drama_receipt.py -Raw | ssh root@47.236.61.117 'docker exec -i zhuxiang-backend-1 python -'
import asyncio

# (contentId, 作品URL, publishId/作品号)——URL 从各平台 manage 核验取
RECEIPTS = [
    (68, "https://www.douyin.com/video/TODO_68", "TODO_68"),
    (69, "https://channels.weixin.qq.com/web/pages/feed/TODO_69", "TODO_69"),
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
