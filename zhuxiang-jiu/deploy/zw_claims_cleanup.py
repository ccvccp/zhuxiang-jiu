"""清理演示理赔留痕(claims 全部为工作台演示期数据, 备份后删)"""
import asyncio
import json


async def t():
    from services.zw_fabric_service import _ZwStore
    from repositories.backend import is_redis_mode, get_redis_client
    store = _ZwStore()

    rows = await store.list("claims")
    with open("/tmp/zw_claims_backup.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("备份:", len(rows), "条 → /tmp/zw_claims_backup.json")

    if not is_redis_mode():
        print("非 Redis, 退出"); return
    client = await get_redis_client()
    for r in rows:
        cid = r.get("claimId")
        if cid is not None:
            await client.delete(f"zhuxiang:zw:claims:{cid}")
    left = await store.list("claims")
    print("已删除:", len(rows), "| 剩余:", len(left))


asyncio.run(t())
