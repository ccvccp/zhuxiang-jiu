"""短信真实通道实证: 直发一条保证金模板短信到管理员手机

验证: 凭据/AK授权/签名/模板全链(容器内执行)。
"""
import asyncio
import sys

sys.path.insert(0, "/app")


async def main():
    from services.sms_aliyun import (
        is_margin_configured, send_margin_reminder,
    )
    print("is_margin_configured:",
          is_margin_configured())
    try:
        r = await send_margin_reminder(
            "18661325187", "漠河店", "2026-10-18",
            "30", "40.0", "400.00")
        print("SENT_OK", r)
    except Exception as exc:
        print("SEND_FAIL", type(exc).__name__,
              str(exc)[:300])


asyncio.run(main())
