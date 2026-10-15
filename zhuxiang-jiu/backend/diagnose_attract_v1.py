"""v1.0 attract 真实流量/转化证据拉取
(72 号断点定性: v1.0 有 1666 点击但 72 号零采集)
"""
import asyncio
import json
from collections import Counter


async def main():
    from repositories.backend import (
        get_redis_client,
    )
    client = await get_redis_client()

    # 1. click 样本 + 时间/类型分布
    keys = [str(k) for k in await client.keys(
        "zhuxiang:attract:click:*")]
    print("== click 总数:", len(keys))
    by_day = Counter()
    by_code = Counter()
    converted = registered = 0
    samples = []
    for k in keys[:]:
        raw = await client.get(k)
        if not raw:
            continue
        try:
            c = json.loads(raw)
        except Exception:
            continue
        if not isinstance(c, dict):
            continue
        day = str(c.get("clickedAt") or "")[:10]
        by_day[day] += 1
        by_code[str(c.get("code") or "")[:8]] += 1
        if c.get("converted"):
            converted += 1
        if c.get("registered"):
            registered += 1
        if len(samples) < 2:
            samples.append({kk: c.get(kk) for kk in (
                "clickId", "code", "clickedAt",
                "converted", "registered",
                "utmSource", "utmMedium")})
    print("按日分布:", dict(sorted(
        by_day.items())))
    print("按短码 Top8:",
          by_code.most_common(8))
    print("converted:", converted,
          "| registered:", registered)
    print("样本:", samples)

    # 2. short_link 样本
    lk = [str(k) for k in await client.keys(
        "zhuxiang:attract:attract_short_links:*")]
    print("\n== short_links:", len(lk))
    for k in lk[:3]:
        raw = await client.get(k)
        try:
            s = json.loads(raw)
            print(" ", {kk: s.get(kk) for kk in (
                "code", "codeType", "status",
                "landingPath", "createdAt")}
                  if isinstance(s, dict) else s)
        except Exception:
            pass

    # 3. 归并/回写侧: 扫 traffic/promo/lead/attribution key
    for pat in ("*attribution*", "*lead*",
                "zhuxiang:traffic*",
                "zhuxiang:promo*",
                "*attach*"):
        ks = [str(x) for x in await client.keys(pat)]
        ks = [x for x in ks
              if not x.endswith(":seq")][:200]
        buckets = Counter(
            ":".join(x.split(":")[:3])
            for x in ks)
        print(f"\npattern {pat}: total={len(ks)}",
              dict(buckets.most_common(6)))


asyncio.run(main())
