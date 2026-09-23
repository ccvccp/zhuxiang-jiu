"""容器内: 窗口化 asrFailRate 计算(只读, 无 guard_check 暂停副作用)

与 scheduler 口径逐字一致(7天窗/ts UTC/无ts保守计入)。
"""
import asyncio
import json
from datetime import UTC, datetime, timedelta

from repositories.xiaozhu_repository import (
    Xiaozhu48Repository,
)


async def main():
    turns = await Xiaozhu48Repository() \
        .scan_turns(limit=2000)
    cutoff = (datetime.now(UTC)
               - timedelta(days=7))

    def inw(t: dict) -> bool:
        raw = str(t.get("ts") or "")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt >= cutoff

    turns = [t for t in turns if inw(t)]
    total = len(turns)
    failed = sum(
        1 for t in turns
        if (t.get("intent") or "") == "asr_failed")
    print(json.dumps({
        "total": total, "asrFailed": failed,
        "rate": round(failed / total, 4)
        if total else 0.0}))


asyncio.run(main())
