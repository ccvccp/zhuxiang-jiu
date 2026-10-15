"""P2 批次②③外部依赖 Mock 通道专项测试(设计稿 §一/§二/§三/§四/§五)

运行:
    python test_p2_batch23.py

覆盖(五域, 全部 mock 轨零凭证):
    01 高德 LBS   add_address 地理编码回填(坐标/adcode/source)/缓存命中/
                  显式坐标 manual/polygon 围栏 ray casting/驾车路网距离
    02 区块链存证 add_evidence 上链回填(状态机 local→onchain/txHash/
                  blockHeight)/verify 附链状态/pending 惰性扫描
    03 花呗白条   分期参数校验(期数/渠道/游客)/mock 方案快照/支付全链
                  落账/Baitiao real 凭证 fail-hard/Huabei real 签名参数
    04 面单理赔   useEwaybill 面单留痕/打印/取消互斥与状态门/回调验签三态/
                  理赔申请-审核-打款全链(保价上限/防重复/状态机)
    05 NFC 防伪   bind 确定性 UID/幂等/占用互斥/verify 反查激活态/HTTP 面
"""
import asyncio
import os
import sys

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")

PASS = 0
FAIL = 0
RESULTS = []

MEMBER = {"X-Member-Id": "99501"}
ADMIN = {"X-Role": "admin"}
U1 = 99501


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def _err(r):
    return (r.json().get("error") or r.json().get("detail") or "")


def run(coro):
    """独立 loop 执行协程(带锁 service 直调, 清锁缓存防跨 loop 挂起)"""
    from core import locks as _locks
    _locks._async_locks.clear()
    try:
        return asyncio.run(coro)
    finally:
        _locks._async_locks.clear()


# ============================================================
# 测试主体
# ============================================================

def main():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    print("[01 高德 LBS(mock 地理编码回填)]")
    # 不传坐标 → mock geocode 回填
    r = client.post("/api/location/address/add", headers=MEMBER, json={
        "receiverName": "竹小马", "receiverPhone": "13800009501",
        "province": "mock省", "city": "mock市", "district": "mock区",
        "detailAddress": "竹香路 3 号"})
    body = r.json().get("data") or {}
    check("无坐标地址 mock 回填 200",
          r.status_code == 200 and body.get("longitude") is not None
          and body.get("latitude") is not None,
          str((r.status_code, body.get("longitude"))))
    check("mock 坐标域 [117-120, 34-37]",
          117 <= (body.get("longitude") or 0) <= 120
          and 34 <= (body.get("latitude") or 0) <= 37,
          str((body.get("longitude"), body.get("latitude"))))
    check("adcode 回填(5 位)+source=mock",
          len(str(body.get("adcode") or "")) == 5
          and body.get("geocodeSource") == "mock",
          str((body.get("adcode"), body.get("geocodeSource"))))

    # 同地址二次 → 缓存命中
    r2 = client.post("/api/location/address/add", headers={
        "X-Member-Id": "99502"}, json={
        "receiverName": "竹小马", "receiverPhone": "13800009502",
        "province": "mock省", "city": "mock市", "district": "mock区",
        "detailAddress": "竹香路 3 号"})
    b2 = r2.json().get("data") or {}
    check("同地址缓存命中(source=cache)",
          b2.get("geocodeSource") == "cache"
          and b2.get("longitude") == body.get("longitude"),
          str((b2.get("geocodeSource"), b2.get("longitude"))))

    # 显式坐标 → manual 不编码
    r3 = client.post("/api/location/address/add", headers={
        "X-Member-Id": "99503"}, json={
        "receiverName": "竹小马", "receiverPhone": "13800009503",
        "province": "mock省", "city": "mock市", "district": "mock区",
        "detailAddress": "竹香路 4 号",
        "longitude": 118.5, "latitude": 32.5})
    b3 = r3.json().get("data") or {}
    check("显式坐标 source=manual",
          b3.get("geocodeSource") == "manual"
          and b3.get("longitude") == 118.5,
          str((b3.get("geocodeSource"), b3.get("longitude"))))

    # polygon 围栏 ray casting
    async def _add_polygon_zone():
        from services.location_service import LocationService
        return await LocationService().add_delivery_zone(
            zone_name="P2多边形围栏", zone_type="city",
            polygon="118.0,31.5;119.5,31.5;119.5,32.5;118.0,32.5")
    run(_add_polygon_zone())
    r = client.post("/api/location/delivery/check", headers=MEMBER,
                    json={"longitude": 118.78, "latitude": 32.06})
    body = r.json().get("data") or {}
    hit = any(z.get("zoneName") == "P2多边形围栏"
              for z in (body.get("matchedZones") or []))
    check("polygon 内点命中", r.status_code == 200 and hit, str(body))
    r = client.post("/api/location/delivery/check", headers=MEMBER,
                    json={"longitude": 117.0, "latitude": 30.0})
    body = r.json().get("data") or {}
    hit2 = any((z or {}).get("zoneName") == "P2多边形围栏"
               for z in (body.get("matchedZones") or []))
    check("polygon 外点不命中", not hit2, str(body))

    # 驾车路网距离(mock = 直线×1.28)
    from services.location_service import LocationService
    res = run(LocationService().calculate_distance(
        118.0, 32.0, 119.0, 32.0, routing="driving"))
    straight = run(LocationService().calculate_distance(
        118.0, 32.0, 119.0, 32.0))
    check("driving mock 路网=直线×1.28",
          res.get("routing") == "driving"
          and res.get("routeSource") == "mock"
          and abs((res.get("distance") or 0)
                  - round((straight.get("distance") or 0) * 1.28, 2))
          < 0.01,
          str(res))
    check("默认仍直线", straight.get("routing") == "straight", "")

    print("[02 区块链存证(mock 上链回填)]")
    r = client.post("/api/location/blockchain/evidence", headers=ADMIN,
                    json={"evidenceType": "logistics",
                          "evidenceData": "P2批次二三测试"})
    body = r.json().get("data") or {}
    check("存证落台账+mock 上链 onchain",
          r.status_code == 200
          and body.get("chainStatus") == "onchain"
          and body.get("chainVendor") == "mock",
          str((r.status_code, body.get("chainStatus"))))
    check("txHash 0x 前缀+blockHeight>0",
          str(body.get("txHash") or "").startswith("0x")
          and (body.get("blockHeight") or 0) > 0,
          str((body.get("txHash"), body.get("blockHeight"))))
    r = client.get("/api/location/blockchain/verify", params={
        "hash": body.get("evidenceHash")})
    vb = r.json().get("data") or {}
    check("verify 附链状态",
          r.status_code == 200 and vb.get("chainStatus") == "onchain"
          and vb.get("txHash") == body.get("txHash"), str(vb))
    scan = run(LocationService().chain_pending_scan())
    check("pending 扫描推进无失败", scan.get("failed") == 0, str(scan))

    print("[03 花呗/白条分期]")
    from services.payment_service import (
        PaymentService, SUPPORTED_CHANNELS,
    )
    check("渠道枚举含 huabei/baitiao",
          "huabei" in SUPPORTED_CHANNELS
          and "baitiao" in SUPPORTED_CHANNELS, "")

    psvc = PaymentService()
    # 分期渠道缺期数 → 409
    r = client.post("/api/payment/pay", headers=MEMBER, json={
        "orderId": "P2B23-OR-001", "totalAmount": 1200.0,
        "payChannel": "huabei", "payMethod": "wap"})
    check("花呗缺期数 → 409", r.status_code == 409, str(_err(r)))
    # 非法期数 → 409
    r = client.post("/api/payment/pay", headers=MEMBER, json={
        "orderId": "P2B23-OR-002", "totalAmount": 1200.0,
        "payChannel": "huabei", "payMethod": "wap",
        "installments": 5})
    check("期数非法(5) → 409", r.status_code == 409, str(_err(r)))
    # 非分期渠道带期数 → 409
    r = client.post("/api/payment/pay", headers=MEMBER, json={
        "orderId": "P2B23-OR-003", "totalAmount": 100.0,
        "payChannel": "wechat", "installments": 6})
    check("微信带 installments → 409", r.status_code == 409, str(_err(r)))
    # 游客分期 → 409
    r = client.post("/api/payment/pay", json={
        "orderId": "P2B23-OR-004", "totalAmount": 100.0,
        "payChannel": "huabei", "payMethod": "wap", "installments": 6,
        "sceneType": "guest_order_pay", "guestPhone": "13800009555",
        "ageConfirmed": True})
    check("游客分期渠道 → 409", r.status_code == 409, str(_err(r)))

    # 正常分期下单 + mock 方案(注意: payment 端点响应无 data 包装)
    r = client.post("/api/payment/pay", headers=MEMBER, json={
        "orderId": "P2B23-OR-010", "totalAmount": 1200.0,
        "payChannel": "huabei", "payMethod": "wap", "installments": 6})
    body = r.json()
    plans = body.get("installmentPlans") or []
    plan6 = next((p for p in plans if p.get("term") == 6), {})
    check("花呗 6 期下单 200",
          r.status_code == 200 and body.get("installments") == 6,
          str((r.status_code, body.get("installments"),
               body.get("payNo"))))
    check("mock 方案三档+6期金额",
          len(plans) == 3
          and plan6.get("perAmount")
          == round((1200.0 + round(1200.0 * 0.045, 2)) / 6, 2)
          and plan6.get("planSource") == "mock",
          str(plans))

    # mock 支付全链落账(order_pay 非自动完成 → 显式回调)
    pay_no = body.get("payNo")

    async def _start():
        return await psvc.start_pay(pay_no)
    started = run(_start())
    check("start_pay → paying",
          started.get("status") == "paying", str(started.get("status")))

    async def _callback():
        return await psvc.pay_callback(
            channel_trade_no=f"MOCK{pay_no}",
            callback_content={"channel": "mock", "mode": "mock"},
            pay_no=pay_no)
    cb = run(_callback())
    got_paid = run(psvc.get_pay(pay_no))
    check("回调落账 paid",
          cb.get("success") is True
          and got_paid.get("status") == "paid",
          str((cb.get("success"), got_paid.get("status"))))
    got = got_paid
    check("支付单留存分期快照",
          got.get("installments") == 6
          and len(got.get("installmentPlans") or []) == 3,
          str((got.get("installments"),
               len(got.get("installmentPlans") or []))))

    # Baitiao real 凭证 fail-hard
    from services.pay_gateway_service import BaitiaoGateway
    try:
        async def _baitiao():
            return await BaitiaoGateway(None).execute(
                {"payNo": "T1", "actualAmount": 100, "installments": 3,
                 "payMethod": "page"}, "https://n")
        run(_baitiao())
        check("Baitiao 缺凭证 fail-hard", False, "未抛错")
    except ValueError as e:
        check("Baitiao 缺凭证 fail-hard", "白条凭证未配置" in str(e),
              str(e))

    # Huabei real 签名参数(fake transport + 临时 RSA 私钥)
    async def _huabei_real():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        os.environ["PAY60_ALIPAY_APPID"] = "p2-test-appid"
        os.environ["PAY60_ALIPAY_PRIVATE_KEY"] = pem
        from services.pay_gateway_service import HuabeiGateway

        async def fake_post(url, json_body=None, form=None, headers=None):
            return 200, {}
        try:
            gw = HuabeiGateway(fake_post)
            return await gw.execute(
                {"payNo": "T2", "orderId": "OR2", "actualAmount": 600.0,
                 "installments": 6, "payMethod": "wap"},
                "https://n")
        finally:
            os.environ.pop("PAY60_ALIPAY_APPID", None)
            os.environ.pop("PAY60_ALIPAY_PRIVATE_KEY", None)
    hb = run(_huabei_real())
    pay_url = str(((hb or {}).get("payParams") or {}).get("payUrl") or "")
    check("Huabei real 预下单含分期参数族",
          (hb.get("prepay") or {}).get("gateway") == "huabei"
          and "enable_pay_installment" in pay_url,
          pay_url[:120])
    check("Huabei 分期数透传 hn_fq_num",
          "hn_fq_num" in pay_url and "6" in pay_url, "")

    print("[04 电子面单 + 理赔]")
    sender = {"name": "竹香仓", "phone": "13900009501",
              "address": "江苏省南京市玄武区竹香路 1 号"}
    receiver = {"name": "竹小马", "phone": "13800009501",
                "address": "江苏省南京市秦淮区竹香路 2 号",
                "province": "江苏省", "city": "南京市"}
    r = client.post("/api/logistics/order", headers=MEMBER, json={
        "orderId": "P2B23-LG-001", "orderType": "retail",
        "carrier": "SF", "serviceType": "standard",
        "sender": sender, "receiver": receiver,
        "weight": 2.5, "pieceCount": 2, "insuredValue": 2000.0,
        "useEwaybill": True})
    body = r.json().get("data") or {}
    wb1 = body.get("waybillNo") or ""
    check("电子面单下单 200(SFMOCK 运单号)",
          r.status_code == 200 and wb1.startswith("SFMOCK"),
          str((r.status_code, wb1)))
    check("面单留痕(大头笔/模板/source)",
          (body.get("ewaybill") or {}).get("machineCode")
          and (body.get("ewaybill") or {}).get("printTemplate")
          and body.get("ewaybillSource") == "mock",
          str(body.get("ewaybillSource")))

    # 打印
    r = client.post(f"/api/logistics/ewaybill/{wb1}/print")
    body = r.json().get("data") or {}
    check("面单打印 200 含模板",
          r.status_code == 200 and body.get("printTemplate"),
          str(r.status_code))

    # 互斥: useEwaybill + waybill_no → 409(HTTP 面无 waybillNo 参数,
    # 该参数为发货流内部通道, 直调 service 验证)
    async def _mutex():
        from services.logistics_service import LogisticsService
        return await LogisticsService().create_order(
            order_id="P2B23-LG-002", order_type="retail",
            carrier="SF", service_type="standard",
            sender=sender, receiver=receiver, weight=1.0,
            use_ewaybill=True, waybill_no="SF9999")
    try:
        run(_mutex())
        check("面单与外部运单号互斥 → 409", False, "未抛错")
    except ValueError as e:
        check("面单与外部运单号互斥 → 409", "互斥" in str(e), str(e))

    # 普通单(非面单)打印 → 409
    r = client.post("/api/logistics/order", headers=MEMBER, json={
        "orderId": "P2B23-LG-003", "orderType": "retail",
        "carrier": "YT", "serviceType": "standard",
        "sender": sender, "receiver": receiver, "weight": 1.0})
    wb3 = (r.json().get("data") or {}).get("waybillNo")
    r = client.post(f"/api/logistics/ewaybill/{wb3}/print")
    check("非面单运单打印 → 409", r.status_code == 409, str(_err(r)))

    # 面单取消(待揽收可取消)
    r = client.post(f"/api/logistics/ewaybill/{wb1}/cancel")
    check("面单取消(待揽收) 200",
          r.status_code == 200
          and (r.json().get("data") or {}).get("ewaybillCancelled")
          is True, str(r.status_code))

    # 回调验签三态(签名 payload 与 TrackCallbackRequest.model_dump()
    # 字段集一致——含默认 operator/trackTime)
    cb_body = {"waybillNo": wb3, "trackStatus": "运输中",
               "unifiedStatus": "transporting",
               "description": "P2 验签测试", "location": "南京市",
               "operator": "carrier", "trackTime": None}
    r = client.post("/api/logistics/callback/track", json=cb_body)
    check("mock 态无签名回调放行", r.status_code == 200,
          str(r.status_code))
    saved_mode = os.environ.get("LGS_CARRIER_MODE")
    saved_token = os.environ.get("LGS_CALLBACK_TOKEN")
    try:
        os.environ["LGS_CARRIER_MODE"] = "real"
        os.environ["LGS_CALLBACK_TOKEN"] = "p2-secret"
        r = client.post("/api/logistics/callback/track", json=cb_body)
        check("real 态无签名 → 401", r.status_code == 401,
              str(r.status_code))
        import json as _json
        from services.logistics_gateway_service import (
            sign_callback_payload,
        )
        sig = sign_callback_payload(
            _json.dumps(cb_body, ensure_ascii=False))
        r = client.post("/api/logistics/callback/track", json=cb_body,
                        headers={"X-Carrier-Signature": sig})
        check("real 态正确签名 → 200", r.status_code == 200,
              str(r.status_code))
        r = client.post("/api/logistics/callback/track", json=cb_body,
                        headers={"X-Carrier-Signature": "bad"})
        check("real 态错签名 → 401", r.status_code == 401,
              str(r.status_code))
    finally:
        if saved_mode is None:
            os.environ.pop("LGS_CARRIER_MODE", None)
        else:
            os.environ["LGS_CARRIER_MODE"] = saved_mode
        if saved_token is None:
            os.environ.pop("LGS_CALLBACK_TOKEN", None)
        else:
            os.environ["LGS_CALLBACK_TOKEN"] = saved_token

    # 理赔全链
    r = client.post("/api/logistics/claim", headers=MEMBER, json={
        "waybillNo": wb3, "claimType": "damage", "claimAmount": 500.0,
        "evidenceUrls": ["https://img/1.jpg"],
        "description": "外箱破损两瓶碎裂"})
    check("未签收理赔 → 409", r.status_code == 409, str(_err(r)))

    # 推进签收: booked→picked→transporting→delivering→signed
    from services.logistics_service import LogisticsService
    lsvc = LogisticsService()

    async def _sign(wb):
        await lsvc.update_status(wb, "booked", "carrier", "已下单")
        await lsvc.update_status(wb, "picked", "carrier", "已揽收")
        await lsvc.update_status(wb, "transporting", "carrier", "运输中")
        await lsvc.update_status(wb, "delivering", "carrier", "派送中")
        return await lsvc.update_status(
            wb, "signed", "carrier", "已签收",
            sign_info={"signerName": "竹小马", "signType": "self"})
    run(_sign(wb3))

    # wb3(无保价): 申请 800 → pending
    r = client.post("/api/logistics/claim", headers=MEMBER, json={
        "waybillNo": wb3, "claimType": "damage", "claimAmount": 800.0,
        "evidenceUrls": ["https://img/1.jpg"],
        "description": "外箱破损两瓶碎裂"})
    body = r.json().get("data") or {}
    claim_no = body.get("claimNo") or ""
    check("签收后理赔申请 pending",
          r.status_code == 200 and claim_no.startswith("CLP")
          and body.get("status") == "pending",
          str((r.status_code, claim_no, _err(r))))
    r = client.post("/api/logistics/claim", headers=MEMBER, json={
        "waybillNo": wb3, "claimType": "lost", "claimAmount": 100.0,
        "description": "重复申请"})
    check("重复理赔 → 409", r.status_code == 409, str(_err(r)))

    # 审核
    r = client.post(f"/api/logistics/claim/{claim_no}/review",
                    headers=MEMBER, json={"approved": True})
    check("非 admin 审核 → 403", r.status_code == 403,
          str(r.status_code))
    r = client.post(f"/api/logistics/claim/{claim_no}/review",
                    headers=ADMIN, json={"approved": True,
                                         "payoutAmount": 600.0})
    body = r.json().get("data") or {}
    check("审核受理定赔 600",
          r.status_code == 200 and body.get("status") == "accepted"
          and body.get("payoutAmount") == 600.0,
          str((body.get("status"), _err(r))))
    r = client.post(f"/api/logistics/claim/{claim_no}/review",
                    headers=ADMIN, json={"approved": True})
    check("重复审核 → 409", r.status_code == 409, str(_err(r)))

    # 打款(复用 payment payout, admin 端点)
    r = client.post(f"/api/logistics/claim/{claim_no}/payout",
                    headers=MEMBER)
    check("非 admin 打款 → 403", r.status_code == 403,
          str(r.status_code))
    r = client.post(f"/api/logistics/claim/{claim_no}/payout",
                    headers=ADMIN)
    paid = r.json().get("data") or {}
    check("理赔打款 → paid+打款单号",
          r.status_code == 200 and paid.get("status") == "paid"
          and paid.get("payoutNo"),
          str((r.status_code, paid.get("status"),
               paid.get("payoutNo"))))

    # wb4(保价 500): 超保价 409 + 拒赔分支
    r = client.post("/api/logistics/order", headers=MEMBER, json={
        "orderId": "P2B23-LG-004", "orderType": "retail",
        "carrier": "SF", "serviceType": "standard",
        "sender": sender, "receiver": receiver, "weight": 1.0,
        "insuredValue": 500.0})
    wb4 = (r.json().get("data") or {}).get("waybillNo")
    run(_sign(wb4))
    r = client.post("/api/logistics/claim", headers=MEMBER, json={
        "waybillNo": wb4, "claimType": "damage", "claimAmount": 800.0,
        "description": "超保价申请"})
    check("超保价金额 → 409", r.status_code == 409, str(_err(r)))
    r = client.post("/api/logistics/claim", headers=MEMBER, json={
        "waybillNo": wb4, "claimType": "short", "claimAmount": 400.0})
    claim2 = (r.json().get("data") or {}).get("claimNo")
    r = client.post(f"/api/logistics/claim/{claim2}/review",
                    headers=ADMIN,
                    json={"approved": False, "note": "包装完好无短少"})
    body = r.json().get("data") or {}
    check("拒赔 → rejected",
          r.status_code == 200 and body.get("status") == "rejected",
          str(body.get("status")))
    r = client.get("/api/logistics/claims", params={"status": "paid"})
    items = r.json().get("data") or []
    check("理赔列表按状态筛选",
          r.status_code == 200
          and any(c.get("claimNo") == claim_no for c in items),
          str(len(items)))

    print("[05 NFC 防伪]")
    # 造生命码
    async def _gen_life():
        from services.trace_service import TraceService
        res = await TraceService().generate_life_codes(
            "ZX-P2B23", "B-P2B23-01", 2, "竹香二期测试")
        return [c["lifeCode"] for c in res["lifeCodes"]]
    codes = run(_gen_life())
    lc1 = codes[0]

    # bind(非 admin → 403; admin 无 uid → mock 确定性 UID)
    r = client.post("/api/trace/nfc/bind", headers=MEMBER, json={
        "lifeCode": lc1})
    check("bind 非 admin → 403", r.status_code == 403,
          str(r.status_code))
    r = client.post("/api/trace/nfc/bind", headers=ADMIN, json={
        "lifeCode": lc1})
    body = r.json().get("data") or {}
    uid = body.get("nfcUid") or ""
    check("bind 200 确定性 UID(NFCMOCK+CRC16)",
          r.status_code == 200 and uid.startswith("NFCMOCK")
          and len(uid) == len("NFCMOCK") + 4,
          str(uid))
    # 幂等: 同 uid 重复 bind OK
    r = client.post("/api/trace/nfc/bind", headers=ADMIN, json={
        "lifeCode": lc1, "nfcUid": uid})
    check("同 uid 重复 bind 幂等", r.status_code == 200,
          str(_err(r)))
    # 换 uid 重绑 → 409
    r = client.post("/api/trace/nfc/bind", headers=ADMIN, json={
        "lifeCode": lc1, "nfcUid": "NFCAAAA0001"})
    check("生命码换绑 → 409", r.status_code == 409, str(_err(r)))
    # uid 被其他码占用 → 409
    r = client.post("/api/trace/nfc/bind", headers=ADMIN, json={
        "lifeCode": codes[1], "nfcUid": uid})
    check("UID 被占用 → 409", r.status_code == 409, str(_err(r)))
    # 不存在的生命码 → 404
    r = client.post("/api/trace/nfc/bind", headers=ADMIN, json={
        "lifeCode": "ZX-P2B23-NOPE-0001-CRC1"})
    check("bind 不存在码 → 404", r.status_code == 404, str(_err(r)))

    # verify(未激活)
    r = client.post("/api/trace/nfc/verify", json={"nfcUid": uid})
    body = r.json().get("data") or {}
    check("verify 公开 200(未激活)",
          r.status_code == 200 and body.get("verified") is True
          and body.get("activated") is False
          and body.get("lifeCode") == lc1,
          str(body))
    # 激活后 verify
    async def _activate():
        from services.trace_service import TraceService
        return await TraceService().activate_life_code(
            lc1, U1, purchase_price=300.0)
    run(_activate())
    r = client.post("/api/trace/nfc/verify", json={
        "nfcUid": uid, "longitude": 118.8, "latitude": 32.0})
    body = r.json().get("data") or {}
    check("激活后 verify activated=True+地理留痕",
          body.get("activated") is True
          and body.get("antiFakeLevel") == "nfc+code"
          and body.get("scanGeo") is not None,
          str(body.get("activated")))
    # 未绑定 UID → 404
    r = client.post("/api/trace/nfc/verify", json={
        "nfcUid": "NFCUNKNOWN"})
    check("未绑定 UID → 404", r.status_code == 404, str(r.status_code))

    # 汇总
    print()
    print(f"P2 批次②③ Mock 通道专项: PASS={PASS} FAIL={FAIL}")
    for line in RESULTS:
        print(line)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
