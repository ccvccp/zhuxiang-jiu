"""P2 灰度验证: real 模式 + 无凭证回退 + 强制人工 review"""
import asyncio
import os


async def main():
    print("== 环境口径 ==")
    print("CHANNEL_MODE:", os.environ.get("PROMO_CHANNEL_MODE"))
    print("DAILY_CAP:", os.environ.get("PROMO_DAILY_CAP"))
    print("PASS_SCORE:",
          os.environ.get("PROMO_COMPLIANCE_PASS_SCORE"))

    from services.promo_channel_service import (
        PromoChannelService,
    )
    print("\n== channel_status ==")
    for row in PromoChannelService().channel_status():
        print(" ", {k: row[k] for k in (
            "platform", "mode", "keyConfigured",
            "effectiveMode")})

    print("\n== 发布回退行为(无凭证平台) ==")
    receipt = await PromoChannelService() \
        .publish_to_platform({
            "platform": "weibo", "title": "P2 灰度探针",
            "body": "探针", "hashtags": "#探测#"})
    print(" ", {k: receipt.get(k) for k in (
        "mode", "platform", "error")})

    print("\n== 强制人工 review(101 阈值) ==")
    from repositories.promo_repository import (
        PROMO_COMPLIANCE_PASS_SCORE,
    )
    print(" 生效 PASS_SCORE:",
          PROMO_COMPLIANCE_PASS_SCORE)
    from services.promo_service import PromoService
    gate = PromoService().compliance_gate(
        "竹香型白酒入口绵甜。"
        "（过量饮酒有害健康，18周岁以下请勿饮酒）")
    print(" 满分内容 gate:",
          {k: gate.get(k) for k in (
              "score", "requiresManualReview")})
    ok = (PROMO_COMPLIANCE_PASS_SCORE == 101
          and gate.get("requiresManualReview") is True
          and receipt.get("mode") == "mock_fallback")
    print("\nP2-GATING:", "PASS" if ok else "FAIL")


asyncio.run(main())
