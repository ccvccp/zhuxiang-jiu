"""查 sid=300 最近轮次: rawText/intent/reply 摘要

用法: docker exec -e LOCK_MODE=asyncio -e STORE_MODE=asyncio \
  zhuxiang-backend-1 python /tmp/peek_turns.py [sid]
"""
import asyncio
import os
import sys

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")


async def main():
    sid = sys.argv[1] if len(sys.argv) > 1 else "300"
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    repo = Xiaozhu48Repository()
    turns = await repo.list_turns(int(sid))
    for t in (turns or [])[-12:]:
        print(f"[{t.get('ts','')[11:19]}] "
              f"intent={t.get('intent'):<12} "
              f"raw={str(t.get('rawText'))[:28]!r} "
              f"reply={str(t.get('reply'))[:36]!r}")

asyncio.run(main())
