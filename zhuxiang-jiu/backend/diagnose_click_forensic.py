"""click 流量法证: 完整字段 + IP/UA 分布定性(真人 or 测试)"""
import asyncio
import json
from collections import Counter


async def main():
    from repositories.backend import (
        get_redis_client,
    )
    client = await get_redis_client()
    keys = [str(k) for k in await client.keys(
        "zhuxiang:attract:click:*")]
    ips = Counter()
    uas = Counter()
    raw_sample = None
    n = 0
    for k in keys:
        raw = await client.get(k)
        if not raw:
            continue
        try:
            c = json.loads(raw)
        except Exception:
            continue
        if not isinstance(c, dict):
            continue
        n += 1
        if raw_sample is None:
            raw_sample = c
        ips[str(c.get("ip") or "?")] += 1
        uas[str(c.get("userAgent") or "?")[:40]] += 1
    print("有效 click:", n)
    print("\n== 原始样本(完整字段) ==")
    print(json.dumps(raw_sample, ensure_ascii=False,
                     indent=1, default=str)[:1200])
    print("\n== IP 分布 Top6 ==")
    for ip, cnt in ips.most_common(6):
        print(f"  {ip}: {cnt}")
    print("唯一 IP 数:", len(ips))
    print("\n== UA 分布 Top6 ==")
    for ua, cnt in uas.most_common(6):
        print(f"  {ua!r}: {cnt}")


asyncio.run(main())
