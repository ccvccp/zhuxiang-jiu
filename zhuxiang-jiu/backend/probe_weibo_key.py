"""P2 探针: 微博 key 形态 + channel_status 现状"""
import os

k = os.environ.get("PROMO_CHANNEL_WEIBO_KEY", "")
print("weibo key: len=", len(k),
      "| prefix=", (k[:6] + "...") if k else "EMPTY",
      "| looks-token=",
      bool(k) and not k.startswith(("mock", "test", "demo",
                                    "placeholder")))

from services.promo_channel_service import (  # noqa: E402
    PromoChannelService,
)
for row in PromoChannelService().channel_status():
    print(row)
