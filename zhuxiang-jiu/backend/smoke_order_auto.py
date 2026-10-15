"""下单自动回写生产冒烟(复用冒烟归因链 clickId=1695/member=15)"""
import asyncio


async def main():
    from routes.order_routes import _attract_auto_order
    from repositories.attract_repository import (
        AttractRepository,
    )
    repo = AttractRepository()
    before = await repo.get_attribution(1695)
    print("冒烟前归因:", before)

    await _attract_auto_order(
        15, "ORD-SMOKE-P1",
        {"priceDetail": {"actualAmount": 268.0}})

    after = await repo.get_attribution(1695)
    print("冒烟后归因:", after)
    ok = (after and after.get("orderId") == "ORD-SMOKE-P1"
          and abs(float(after.get("orderAmount") or 0)
                  - 268.0) < 1e-6)
    print("ORDER-AUTO:", "PASS" if ok else "FAIL")

    # 72 号 conversions(指纹冒烟 UA 链)
    from services.attract72_registry import (
        fingerprint_of,
    )
    import json
    from repositories.backend import get_redis_client
    client = await get_redis_client()
    raw = await client.get(
        "zhuxiang:attract72:memory:"
        + fingerprint_of("SmokeHookUA/1.0"))
    mem = json.loads(raw) if raw else {}
    print("72号记忆:", mem.get("clicksTotal"),
          "clicks /", mem.get("registered"),
          "reg /", mem.get("conversions"), "conv")
    print("CONV-72:", "PASS"
          if int(mem.get("conversions") or 0) >= 1
          else "FAIL")


asyncio.run(main())
