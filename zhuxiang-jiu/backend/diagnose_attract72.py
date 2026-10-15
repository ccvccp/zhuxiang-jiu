"""72号 AI 智能引流生产诊断
证据链: redis key 全扫(含 v1.0 attract) + model_status
+ 13 表存量 + v1.0 短链流量证据
"""
import asyncio
from collections import Counter


async def main():
    from repositories.backend import (
        is_redis_mode, get_redis_client,
    )
    print("redis mode:", is_redis_mode())

    if is_redis_mode():
        client = await get_redis_client()
        keys = await client.keys("*attract*")
        keys = [str(k) for k in keys]
        print("== attract* key 总数:", len(keys), "==")
        buckets = Counter()
        for k in keys:
            parts = k.split(":")
            # zhuxiang:<domain>:<table>:<id> → 取前 3 段做桶
            buckets[":".join(parts[:3])] += 1
        for b, n in buckets.most_common(40):
            print(f"  {b}: {n}")

    # 模型总检查口
    from services.attract72_p6_service import (
        Attract72P6Service,
    )
    try:
        st = await Attract72P6Service().model_status()
        print("\n== model_status ==")
        for k, v in (st or {}).items():
            print(f"  {k}: {v}")
    except Exception as exc:
        print("model_status ERR:", exc)

    # 13 表存量
    from repositories.attract72_repository import (
        Attract72Repository,
    )
    repo = Attract72Repository()
    print("\n== 72号 13 表存量 ==")
    for t in repo._ALL_TABLES:
        try:
            recs = await repo._list(t, limit=10000)
            print(f"  {t}: {len(recs)}")
        except Exception as exc:
            print(f"  {t}: ERR {exc}")


asyncio.run(main())
