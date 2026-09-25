"""复测失败诊断: wine 钩子异常抓取"""
import asyncio
import logging
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

logging.basicConfig(level=logging.DEBUG,
                    format="%(levelname)s %(name)s %(message)s")


async def main():
    from repositories.store import reset_store
    reset_store()
    from services.xiaozhu_service import XiaozhuService
    sid = (await XiaozhuService().open_session(1))["sessionId"]
    r = await XiaozhuService().handle_text(
        sid, "小竹，选一款42度的酒")
    print("reply:", str(r.get("reply"))[:80])
    print("intent:", r.get("intent"))
    return 0


sys.exit(asyncio.run(main()))
