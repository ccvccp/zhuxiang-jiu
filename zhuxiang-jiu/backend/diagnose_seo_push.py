"""36号补充取证: seo_pushes URL 形态(P0 robots 修复联动)"""
import asyncio
import json


async def main():
    from repositories.backend import (
        get_redis_client,
    )
    client = await get_redis_client()
    keys = [str(k) for k in await client.keys(
        "zhuxiang:promo:promo_seo_pushes:*")]
    print("seo_pushes:", len(keys))
    for k in keys[:12]:
        raw = await client.get(k)
        if not raw:
            continue
        try:
            r = json.loads(raw)
            print(" ", {kk: str(r.get(kk))[:70]
                        for kk in (
                            "pushId", "urls", "status",
                            "success", "at",
                            "pushedCount", "result")}
                  if isinstance(r, dict) else r)
        except Exception:
            print(" ", k, "non-dict")
    # 发布回执形态(最新 published 内容全字段)
    ks2 = [str(k) for k in await client.keys(
        "zhuxiang:promo:promo_contents:*")]
    for k in ks2:
        raw = await client.get(k)
        try:
            r = json.loads(raw)
        except Exception:
            continue
        if isinstance(r, dict) and r.get("status") == "published":
            print("\n最新 published 内容全字段:")
            print(json.dumps(r, ensure_ascii=False,
                             default=str)[:900])
            break


asyncio.run(main())
