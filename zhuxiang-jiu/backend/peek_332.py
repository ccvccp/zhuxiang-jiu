"""sid=332 两轮回复内容核查"""
import asyncio
import os

os.environ.setdefault("STORE_MODE", "redis")
os.environ.setdefault("LOCK_MODE", "redis")


async def main():
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    turns = await Xiaozhu48Repository().list_turns(332)
    for t in (turns or [])[-6:]:
        print("intent:", t.get("intent"),
              "| raw:", str(t.get("rawText"))[:40])
        print("  reply:", str(t.get("reply"))[:110])
        print()


asyncio.run(main())
