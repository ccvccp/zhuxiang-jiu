"""70号·AI智能二维码大模型 P3 专项测试
(收货码——三要素校验+证据链+异常升档)

运行方式:
    python test_qr70_p3.py

覆盖(70号规划 §4.4/§七 P3):
    - 判定域封闭(matched/not_owner/
      order_state/fence/time)
    - 签发: 绑定订单归属人/仅 SHIPPED
      可签/订单域外 404/围栏非法 409
    - 三要素校验: 命中 matched/围栏外
      escalated/超时窗 escalated/非归属
      拒绝/订单态拒绝
    - 换设备检测: 异设备→escalated
    - 显式签收: 未扫码 409/异常未确认
      409/确认成功(RECEIVED)/重放拒绝/
      先核销后执行
    - 证据链: 时间戳+脱敏城市+设备摘要
    - 码域防御: 篡改/过期/已核销不可扫
    - 模式矩阵: 决策面 off 409/公开
      scan/confirm 不受影响
    - HTTP: 5 端点全链
    - QC: 订单域零改动(confirm 独立
      可用+15 天惯例对齐)
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["PAY69_MODE"] = "off"
os.environ["QR70_MODE"] = "off"

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


def set_mode(m: str):
    os.environ["QR70_MODE"] = m


async def seed_orders():
    """播种订单(直写仓储——订单域零改动)"""
    from repositories.order_repository \
        import OrderRepository
    repo = OrderRepository()

    async def make(order_id, status,
                  member_id, city,
                  days_ago,
                  addr_lng=None,
                  addr_lat=None):
        shipped_iso = (
            datetime.now()
            - timedelta(days=days_ago)).isoformat()
        order = {
            "orderId": order_id,
            "memberId": member_id,
            "orderType": "RT",
            "status": status,
            "items": [],
            "priceDetail": {
                "actualAmount": 396},
            "address": {
                "province": "山东省",
                "city": city,
                "lng": addr_lng,
                "lat": addr_lat,
            },
            "remark": "",
            "logistics": {
                "carrier": "SF",
                "waybillNo": "SF-001",
                "shippedAt": shipped_iso,
                "signedAt": "",
            },
            "payment": {"method": "wechat",
                        "tradeNo": "T1",
                        "paidAt": shipped_iso},
            "review": {},
            "refund": {},
            "usedPoints": 0,
            "consumedPoints": 0,
            "timeline": [],
            "createdAt": shipped_iso,
            "updatedAt": shipped_iso,
        }
        await repo.create(order)

    # A: 正常待收货(1 天前发货, 泰安)
    await make("ORD-70P3-A", "SHIPPED", 9,
               "泰安市", 1)
    # B: 超时窗(20 天前发货)
    await make("ORD-70P3-B", "SHIPPED", 9,
               "泰安市", 20)
    # C: 非待收货(PAID)
    await make("ORD-70P3-C", "PAID", 9,
               "泰安市", 1)
    # D: 他人订单(member 10)
    await make("ORD-70P3-D", "SHIPPED", 10,
               "泰安市", 1)
    # E: 坐标级围栏(泰安坐标)
    await make("ORD-70P3-E", "SHIPPED", 9,
               "泰安市", 1,
               addr_lng=117.087,
               addr_lat=36.200)
    # H: 坐标级围栏专用单(未被状态流转消费)
    await make("ORD-70P3-H", "SHIPPED", 9,
               "泰安市", 1,
               addr_lng=117.087,
               addr_lat=36.200)


async def main():
    from repositories.store import reset_store
    reset_store()
    await seed_orders()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/qr70"

    print("[01 判定域与字典封闭]")

    from services import qr70_receiving_service as rv

    record("判定域五态封闭",
           set(rv.SCAN_VERDICTS) == {
               "matched", "not_owner",
               "order_state_violation",
               "fence_violation",
               "time_violation"})
    record("拒绝型/升档型分离",
           set(rv.REJECT_VERDICTS)
           & set(rv.ESCALATE_VERDICTS)
           == set())
    record("围栏默认 20km(67/68号范式)",
           rv.FENCE_DEFAULT_KM == 20)
    record("时间窗 15 天(超时惯例对齐)",
           rv.RECEIVE_WINDOW_DAYS == 15)
    d = rv.Qr70ReceivingService().dict_view()
    record("字典公示(判定+铁律)",
           len(d["scanVerdicts"]) == 5
           and len(d["redlines"]) >= 4)

    print("[02 签收码签发]")

    set_mode("assist")
    svc = rv.Qr70ReceivingService()

    code_a = await svc.issue("ORD-70P3-A")
    record("签发成功(qr70-receiving-sign)",
           code_a["code"].startswith(
               "ZXBJ-QR55:qr70-receiving-sign:")
           and code_a["orderId"]
           == "ORD-70P3-A",
           code_a["code"][:48])
    record("归属人=订单 memberId",
           code_a["memberId"] == 9)
    record("围栏参数入码(fenceKm=20)",
           code_a["params"]["fenceKm"]
           == "20")
    try:
        await svc.issue("ORD-70P3-C")
        record("PAID 签发 ValueError", False,
               "未抛出")
    except ValueError:
        record("PAID 签发 ValueError(409)",
               True)
    try:
        await svc.issue("ORD-GHOST")
        record("订单域外 KeyError", False,
               "未抛出")
    except KeyError:
        record("订单域外 KeyError(404)", True)
    try:
        await svc.issue("ORD-70P3-A", 0)
        record("围栏非法 ValueError", False,
               "未抛出")
    except ValueError:
        record("围栏非法 ValueError(409)",
               True)

    print("[03 三要素校验]")

    s = await svc.scan(code_a["code"], 9,
                       "泰安市",
                       device_id="dev-1")
    record("三要素齐备 matched",
           s["scannable"] is True
           and s["verdict"] == "matched"
           and s["escalated"] is False,
           str(s.get("verdict")))
    record("判定标签直出",
           s["verdictLabel"] == "三要素齐备")
    record("证据含时间戳+城市+设备摘要",
           bool(s["evidence"]["scannedAt"])
           and s["evidence"]["city"]
           == "泰安市"
           and len(s["evidence"][
               "deviceDigest"]) == 12)
    record("scan 口径(不改订单状态)",
           "显式" in s["note"])

    s_fence = await svc.scan(
        code_a["code"], 9, "济南市",
        device_id="dev-1")
    record("围栏外 escalated",
           s_fence["verdict"]
           == "fence_violation"
           and s_fence["escalated"] is True)

    code_b = await svc.issue("ORD-70P3-B")
    s_time = await svc.scan(
        code_b["code"], 9, "泰安市")
    record("超时窗 escalated(20 天)",
           s_time["verdict"]
           == "time_violation"
           and s_time["escalated"] is True)

    code_d = await svc.issue("ORD-70P3-D")
    s_owner = await svc.scan(
        code_d["code"], 9, "泰安市")
    record("非归属人拒绝(not_owner)",
           s_owner["verdict"] == "not_owner"
           and s_owner["escalated"] is False)

    # 订单态拒绝: E 先被订单域 confirm
    from services.order_service import (
        OrderService,
    )
    code_e = await svc.issue("ORD-70P3-E")
    await OrderService().confirm("ORD-70P3-E")
    s_state = await svc.scan(
        code_e["code"], 9, "泰安市")
    record("订单非待收货拒绝",
           s_state["verdict"]
           == "order_state_violation")

    print("[04 坐标级围栏与换设备]")

    code_h = await svc.issue("ORD-70P3-H")
    s_coord_ok = await svc.scan(
        code_h["code"], 9, "",
        lng=117.10, lat=36.21,
        device_id="dev-1")
    record("坐标级围栏内 matched",
           s_coord_ok["verdict"] == "matched")
    s_coord_out = await svc.scan(
        code_h["code"], 9, "",
        lng=116.40, lat=39.90,
        device_id="dev-1")
    record("坐标级围栏外(北京→泰安)",
           s_coord_out["verdict"]
           == "fence_violation")
    code_a2 = await svc.issue("ORD-70P3-A")
    s_dev1 = await svc.scan(
        code_a2["code"], 9, "泰安市",
        device_id="dev-1")
    s_dev2 = await svc.scan(
        code_a2["code"], 9, "泰安市",
        device_id="dev-2")
    record("换设备检测(deviceChange)",
           s_dev1["deviceChange"] is False
           and s_dev2["deviceChange"] is True
           and s_dev2["escalated"] is True)

    print("[05 显式签收与升档铁律]")

    # ORD-C 为 PAID 不可签——新单 F 走确认链
    from repositories.order_repository \
        import OrderRepository
    await OrderRepository().create({
        "orderId": "ORD-70P3-F",
        "memberId": 9, "orderType": "RT",
        "status": "SHIPPED", "items": [],
        "priceDetail": {"actualAmount": 1},
        "address": {"city": "泰安市"},
        "logistics": {
            "carrier": "SF",
            "waybillNo": "SF-2",
            "shippedAt": datetime.now()
            .isoformat(),
            "signedAt": ""},
        "timeline": [],
        "createdAt": "", "updatedAt": "",
    })
    code_f = await svc.issue("ORD-70P3-F")
    try:
        await svc.confirm(code_f["code"], 9)
        record("未扫码确认 ValueError", False,
               "未抛出")
    except ValueError:
        record("未扫码确认 ValueError(409)",
               True)
    await svc.scan(code_f["code"], 9,
                   "泰安市",
                   device_id="dev-f")
    r_f = await svc.confirm(code_f["code"], 9)
    record("matched 确认成功(RECEIVED)",
           r_f["received"] is True
           and r_f["orderStatus"]
           == "RECEIVED",
           str(r_f)[:80])
    order_f = await OrderRepository()\
        .get_by_id("ORD-70P3-F")
    record("订单域状态真实变更(signedAt)",
           order_f["status"] == "RECEIVED"
           and bool(order_f["logistics"][
               "signedAt"]))
    r_f2 = await svc.confirm(code_f["code"], 9)
    record("重放确认拒绝(replayed)",
           r_f2["received"] is False
           and r_f2["verifyStatus"]
           == "replayed")

    # 升档单: 新订单 G 围栏外扫码
    await OrderRepository().create({
        "orderId": "ORD-70P3-G",
        "memberId": 9, "orderType": "RT",
        "status": "SHIPPED", "items": [],
        "priceDetail": {"actualAmount": 1},
        "address": {"city": "泰安市"},
        "logistics": {
            "carrier": "SF",
            "waybillNo": "SF-3",
            "shippedAt": datetime.now()
            .isoformat(),
            "signedAt": ""},
        "timeline": [],
        "createdAt": "", "updatedAt": "",
    })
    code_g = await svc.issue("ORD-70P3-G")
    await svc.scan(code_g["code"], 9,
                   "济南市")
    try:
        await svc.confirm(code_g["code"], 9)
        record("升档未确认 ValueError", False,
               "未抛出")
    except ValueError:
        record("升档未确认 ValueError(409)",
               True)
    r_g = await svc.confirm(
        code_g["code"], 9,
        escalated_ack=True)
    record("二次确认后签收成功",
           r_g["received"] is True
           and r_g["evidence"][
               "escalatedAck"] is True)

    print("[06 码域防御]")

    tampered = code_f["code"][:-2] + "zz"
    t = await svc.scan(tampered, 9, "泰安市")
    record("篡改码不可扫(tampered)",
           t["scannable"] is False
           and t["verifyStatus"] == "tampered")
    from services.qr55_crypto import (
        generate_code as qr55_gen,
    )
    expired = qr55_gen(
        "qr70-receiving-sign",
        {"orderId": "ORD-70P3-A",
         "fenceKm": "20"}, 9,
        ttl_seconds=-10)
    e = await svc.scan(expired["code"], 9,
                       "泰安市")
    record("过期码不可扫(expired)",
           e["scannable"] is False
           and e["verifyStatus"] == "expired")
    other = await svc.issue("ORD-70P3-A")
    record("未消费备用码留痕(issued)",
           other["status"] == "generated")
    # 已核销码不可扫: 用 F 的 redeemed 码
    try:
        await svc.scan(code_f["code"], 9,
                       "泰安市")
        record("已核销码 ValueError", False,
               "未抛出")
    except ValueError:
        record("已核销码 ValueError(409)",
               True)

    print("[07 证据链视图]")

    ev = await svc.evidence_view("ORD-70P3-F")
    record("证据链含 scan+confirm",
           ev["chainLength"] == 2
           and [c["type"]
                for c in ev["chain"]]
           == ["receiving_scan",
               "receiving_confirm"])
    confirm_ev = ev["chain"][-1]
    record("签收回执字段齐备",
           confirm_ev["receivedAt"] != ""
           and confirm_ev["city"] == "泰安市"
           and len(confirm_ev[
               "deviceDigest"]) == 12)
    ev_g = await svc.evidence_view(
        "ORD-70P3-G")
    record("升档证据留痕(escalatedAck)",
           ev_g["chain"][-1]["escalatedAck"]
           is True)
    ev_none = await svc.evidence_view(
        "ORD-GHOST")
    record("无证据订单空链",
           ev_none["chainLength"] == 0)

    print("[08 HTTP 全链]")

    r = client.get(f"{BASE}/receiving/dict",
                   headers=ADMIN)
    record("字典 200(admin)",
           r.status_code == 200
           and len(r.json()["scanVerdicts"])
           == 5)
    r = client.get(f"{BASE}/receiving/dict")
    record("字典无 admin 403",
           r.status_code == 403)

    set_mode("off")
    r = client.post(f"{BASE}/receiving/issue",
                    headers=ADMIN,
                    json={"orderId":
                          "ORD-70P3-A"})
    record("签发 off 409",
           r.status_code == 409,
           f"s={r.status_code}")

    set_mode("assist")
    # HTTP 专用隔离单 I(设备链干净——
    # 避免与此前 scan 的设备史串扰)
    await OrderRepository().create({
        "orderId": "ORD-70P3-I",
        "memberId": 9, "orderType": "RT",
        "status": "SHIPPED", "items": [],
        "priceDetail": {"actualAmount": 1},
        "address": {"city": "泰安市"},
        "logistics": {
            "carrier": "SF",
            "waybillNo": "SF-4",
            "shippedAt": datetime.now()
            .isoformat(),
            "signedAt": ""},
        "timeline": [],
        "createdAt": "", "updatedAt": "",
    })
    r = client.post(f"{BASE}/receiving/issue",
                    headers=ADMIN,
                    json={"orderId":
                          "ORD-70P3-I",
                          "fenceKm": 20})
    body = r.json()
    record("签发 200(assist)",
           r.status_code == 200
           and body["orderId"]
           == "ORD-70P3-I",
           f"s={r.status_code}")
    http_code = body["code"]
    r = client.post(f"{BASE}/receiving/issue",
                    headers=ADMIN,
                    json={"orderId":
                          "ORD-GHOST"})
    record("签发订单域外 404",
           r.status_code == 404,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/receiving/issue",
                    headers=ADMIN,
                    json={"orderId":
                          "ORD-70P3-I",
                          "fenceKm": 0})
    record("签发围栏非法 409(422 校验)",
           r.status_code in (409, 422),
           f"s={r.status_code}")

    set_mode("off")
    r = client.post(f"{BASE}/receiving/scan",
                    json={"code": http_code,
                          "memberId": 9,
                          "city": "泰安市",
                          "deviceId":
                              "dev-i"})
    record("扫码公开 200(off 无关)",
           r.status_code == 200
           and r.json()["verdict"]
           == "matched",
           f"s={r.status_code}")

    r = client.post(
        f"{BASE}/receiving/confirm",
        json={"code": http_code,
              "operatorId": 9})
    record("确认公开 200(off 无关)",
           r.status_code == 200
           and r.json()["received"] is True,
           f"s={r.status_code}")
    r = client.post(
        f"{BASE}/receiving/confirm",
        json={"code": http_code,
              "operatorId": 9})
    record("HTTP 重放拒绝",
           r.status_code == 200
           and r.json()["received"] is False)

    r = client.get(
        f"{BASE}/receiving/evidence/"
        f"ORD-70P3-I",
        headers=ADMIN)
    body = r.json()
    record("证据链 200(admin)",
           r.status_code == 200
           and body["chainLength"] == 2,
           f"n={body['chainLength']}")

    print("[09 QC 订单域零改动]")

    record("OrderService confirm 独立可用",
           hasattr(OrderService, "confirm")
           and hasattr(OrderService, "ship"))
    from services import \
        order_timeout_scheduler as sched
    record("15 天超时惯例零改动",
           hasattr(sched, "run_timeout_scan")
           and hasattr(sched, "start_scheduler"))
    from services import qr70_registry as reg
    record("receiving-sign 注册语义保持",
           reg.CODE_REGISTRY[
               "receiving-sign"][
               "consumePolicy"] == "once"
           and reg.CODE_REGISTRY[
               "receiving-sign"]["params"]
           == ["orderId", "fenceKm"])
    dist = rv._haversine_km(
        117.087, 36.200,
        117.10, 36.21)
    record("haversine 确定性(泰安同城<20)",
           0 < dist < 20,
           str(dist))

    # ------------------------------------------------------------
    print()
    print("=" * 60)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
