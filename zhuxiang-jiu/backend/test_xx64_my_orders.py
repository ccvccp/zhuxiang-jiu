"""64号·会员面 my-orders(订单+申诉状态联查)专项测试

覆盖(申诉前端入口数据源——观测面):
    1. 鉴权: 无 X-Role 403 / member 200
    2. 隔离: 仅返回本人(buyer_id)订单
    3. 联查: 有申诉订单附最新申诉进度/
       无申诉订单 appeal=null
    4. 联动: 提交申诉后 my-orders 即时可见
       in-flight 申诉(recalculated)
    5. 最新语义: 同订单多申诉取 appealId 最大

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_xx64_my_orders.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XX64_MODE"] = "off"
os.environ["XX64_LLM_MODE"] = "off"
os.environ["XX64_LEARN_MODE"] = "off"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_all():
    from repositories.store import reset_store
    reset_store()


async def seed_profile(trust_id, score=500.0):
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    await TrustValue45Repository() \
        .save_profile({
            "trustId": int(trust_id),
            "role": "person",
            "name": f"P{trust_id}",
            "idDigest": f"d-{trust_id}",
            "factors": {},
            "score": float(score),
            "rawScore": float(score),
            "grade": "A",
            "fused": False,
            "frozen": False,
            "createdAt": "2026-01-01T00:00:00",
            "updatedAt": "2026-01-01T00:00:00"})


async def add_order(repo, buyer, seller,
                    trust, product, price,
                    tv, status="paid"):
    from datetime import datetime, UTC
    now = datetime.now(UTC).isoformat()
    oid = await repo.next_order_id()
    await repo.save_order({
        "orderId": oid,
        "buyerId": buyer,
        "sellerId": seller,
        "trustId": trust,
        "product": product,
        "price": float(price),
        "trustValue": float(tv),
        "cashValue": round(float(price) - float(tv), 2),
        "status": status,
        "useTrust": True,
        "precheck": {},
        "createdAt": now,
        "updatedAt": now,
        "snapshot": {},
    })
    return oid


async def main():
    reset_all()
    await seed_profile(1)
    await seed_profile(2)
    from repositories.xx64_repository import (
        Xx64Repository,
    )
    repo = Xx64Repository()
    # 买家 1 两单 + 买家 2 一单
    o1 = await add_order(repo, 1, 2, 1, "gH", 100, 30)
    await add_order(repo, 1, 2, 1, "gJ", 200, 60)
    await add_order(repo, 2, 1, 2, "gK", 300, 90)

    from httpx import ASGITransport, AsyncClient
    from main import app
    member = {"X-Role": "member"}
    admin = {"X-Role": "admin"}

    async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://t") as client:

        # ① 鉴权
        resp = await client.get(
            "/api/xx64/my-orders?buyer_id=1")
        record("无Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # ② 隔离+联查(无申诉)
        resp = await client.get(
            "/api/xx64/my-orders?buyer_id=1",
            headers=member)
        body = resp.json() or {}
        orders = body.get("orders") or []
        record("本人订单返回(member 200)",
               resp.status_code == 200
               and body.get("total") == 2,
               str((resp.status_code,
                    body.get("total"))))
        record("隔离(不含他人订单)",
               all(int(o.get("buyerId"))
                   == 1 for o in orders)
               and len(orders) == 2,
               str([o.get("orderId")
                    for o in orders]))
        record("无申诉单 appeal=null",
               all(o.get("appeal") is None
                   for o in orders))

        # ③ 提交申诉(不受开关)后即时可见
        resp = await client.post(
            "/api/xx64/appeals",
            json={"orderId": o1,
                  "reason": "my-orders 联查测试"},
            headers=member)
        ap1 = (resp.json() or {}) \
            .get("appealId")
        record("申诉提交 200",
               resp.status_code == 200
               and ap1 > 0,
               str(resp.status_code))

        resp = await client.get(
            "/api/xx64/my-orders?buyer_id=1",
            headers=member)
        orders = (resp.json() or {}) \
            .get("orders") or []
        by_id = {int(o.get("orderId")): o
                 for o in orders}
        rec = by_id.get(int(o1))
        record("申诉进度即时联查",
               rec and rec.get("appeal")
               and rec["appeal"].get("status")
               == "recalculated"
               and int(rec["appeal"]
                       .get("appealId")) == ap1,
               str(rec and rec.get("appeal")))

        # ④ 终审后状态翻转联查
        resp = await client.post(
            f"/api/xx64/appeals/{ap1}/review",
            json={"decision": "approve",
                  "reviewNote": "人工核实"},
            headers=admin)
        record("终审 approve 200",
               resp.status_code == 200)
        resp = await client.get(
            "/api/xx64/my-orders?buyer_id=1",
            headers=member)
        orders = (resp.json() or {}) \
            .get("orders") or []
        by_id = {int(o.get("orderId")): o
                 for o in orders}
        rec = by_id.get(int(o1))
        record("终审后状态联查(approved)",
               rec and rec.get("appeal")
               and rec["appeal"].get("status")
               == "approved"
               and rec["appeal"]
               .get("decision") == "approve",
               str(rec and rec.get("appeal")))

        # ⑤ 最新申诉语义(同订单二诉取新)
        resp = await client.post(
            "/api/xx64/appeals",
            json={"orderId": o1,
                  "reason": "再次申诉(终审后)"},
            headers=member)
        ap2 = (resp.json() or {}) \
            .get("appealId")
        record("终审后可再诉",
               resp.status_code == 200
               and ap2 > ap1,
               str((resp.status_code, ap2)))
        resp = await client.get(
            "/api/xx64/my-orders?buyer_id=1",
            headers=member)
        orders = (resp.json() or {}) \
            .get("orders") or []
        by_id = {int(o.get("orderId")): o
                 for o in orders}
        rec = by_id.get(int(o1))
        record("同订单多申诉取最新",
               rec and int(rec["appeal"]
                           .get("appealId"))
               == ap2,
               str(rec and rec.get("appeal")))

        # ⑥ admin 口径可用
        resp = await client.get(
            "/api/xx64/my-orders?buyer_id=2",
            headers=admin)
        body = resp.json() or {}
        record("admin 口径 200",
               resp.status_code == 200
               and body.get("total") == 1,
               str((resp.status_code,
                    body.get("total"))))

        # ⑦ buyer_id 必填
        resp = await client.get(
            "/api/xx64/my-orders",
            headers=member)
        record("缺 buyer_id 422",
               resp.status_code == 422,
               str(resp.status_code))

    print()
    print(f"通过: {PASS} / 失败: {FAIL} / 总计: {PASS + FAIL}")
    for line in RESULTS:
        print(line)
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
