"""公众号通道生产部署验证(容器内一次性)"""
import asyncio
import os

os.environ.setdefault("STORE_MODE", "redis")


async def main():
    from services.promo_channel_service import (
        PromoChannelService, wechat_mp_send_mode,
        wechat_mp_monthly_cap, channel_key,
    )
    svc = PromoChannelService()
    rows = {r["platform"]: r for r in svc.channel_status()}
    print("wechat_mp status:", rows.get("wechat_mp"))
    print("send_mode:", wechat_mp_send_mode(),
          "| monthly_cap:", wechat_mp_monthly_cap(),
          "| key_configured:", bool(
              channel_key("wechat_mp")))


asyncio.run(main())
