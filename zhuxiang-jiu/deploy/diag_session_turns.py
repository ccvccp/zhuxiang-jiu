"""查看 session 236 轮次落库——判定响应是否正常生成"""
import asyncio
import sys

sys.path.insert(0, "/app")


async def main():
    from services.xiaozhu_service import XiaozhuService
    try:
        view = await XiaozhuService().get_session(236)
        turns = view.get("turns") or []
        print(f"session 236: turns={len(turns)}")
        for t in turns[-8:]:
            print(f"  [{t.get('createdAt','')}] intent={t.get('intent')} "
                  f"raw={t.get('rawText')!r} reply={str(t.get('reply'))[:60]!r}")
    except Exception as exc:
        print("ERR:", type(exc).__name__, exc)


asyncio.run(main())
