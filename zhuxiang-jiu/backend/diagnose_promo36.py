"""36号智能推广生产取证: 决策/内容/发布/回执/时间线"""
import asyncio
import json
from collections import Counter


async def main():
    from repositories.backend import (
        get_redis_client,
    )
    client = await get_redis_client()

    async def bucket(pat):
        keys = [str(k) for k in
                await client.keys(pat)]
        out = {}
        for k in keys:
            b = ":".join(k.split(":")[:3])
            out[b] = out.get(b, 0) + 1
        return out, keys

    b, keys = await bucket("zhuxiang:promo:*")
    print("== promo key 桶 ==")
    for k, n in sorted(b.items()):
        print(f"  {k}: {n}")

    async def dump_all(pat, fields, limit=200):
        ks = [str(x) for x in await client.keys(pat)]
        recs = []
        for k in ks[:limit]:
            raw = await client.get(k)
            if not raw:
                continue
            try:
                r = json.loads(raw)
                if isinstance(r, dict):
                    recs.append(r)
            except Exception:
                pass
        return recs

    # 决策分布
    ds = await dump_all("zhuxiang:promo:promo_decisions:*",
                        None)
    dec = Counter(str(d.get("action") or d.get("decision")
                      or "?") for d in ds)
    last = max((str(d.get("decidedAt") or d.get("at")
                    or "") for d in ds), default="")
    print(f"\n== 决策 {len(ds)} 条 ==")
    print("  分布:", dict(dec))
    print("  最近决策时间:", last or "无")

    # 热点: 来源平台+最后扫描
    hs = await dump_all("zhuxiang:promo:promo_hotspots:*", None)
    plat = Counter(str(h.get("platform") or "?")
                   for h in hs)
    titles = [str(h.get("title") or "")[:24]
              for h in hs[:6]]
    last_h = max((str(h.get("scannedAt")
                      or h.get("at") or "")
                  for h in hs), default="")
    print(f"\n== 热点 {len(hs)} 条 ==")
    print("  平台分布:", dict(plat))
    print("  标题样本:", titles)
    print("  最近扫描:", last_h or "无")

    # 内容: 状态/审核/回执
    cs = await dump_all("zhuxiang:promo:promo_contents:*", None)
    stat = Counter(str(c.get("status") or "?") for c in cs)
    print(f"\n== 内容 {len(cs)} 条 ==")
    print("  状态分布:", dict(stat))
    for c in cs[:5]:
        print("   -", str(c.get("title")
                          or c.get("contentId"))[:22],
              "| status:", c.get("status"),
              "| review:", c.get("reviewStatus")
              or c.get("complianceScore"),
              "| pub:", str(
                  c.get("publishReceipt") or "")[:36],
              "| at:", str(c.get("at")
                          or c.get("createdAt"))[:19])

    # 发布队列/链接
    lks = await dump_all(
        "zhuxiang:promo:promo_content_links:*", None)
    print(f"\n== 内容链接 {len(lks)} 条 ==")
    for l in lks[:4]:
        print("   -", str(l.get("shortCode")
                          or l.get("code")),
              "| content:", l.get("contentId"),
              "| at:", str(l.get("at")
                          or l.get("createdAt"))[:19])

    # 雷达扫描近况(调度器日志侧已见 skipped=25)
    print("\n== audience_profiles ==")
    aps = await dump_all(
        "zhuxiang:promo:promo_audience_profiles:*", None)
    for a in aps[:4]:
        print("   -", str(a.get("platform")
                          or "?"),
              "| 使用次数:", a.get("useCount")
              or a.get("matchedCount"))


asyncio.run(main())
