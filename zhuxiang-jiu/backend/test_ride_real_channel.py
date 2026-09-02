"""41号·直发通道真实接入专项测试(待办清单 §5.1 代码侧验证)

运行方式:
    python test_ride_real_channel.py

验证目标(凭证缺省场景下, 用本地 mock 平台服务器模拟真实平台):
    - real 轨全链路(直发请求→响应映射→channel=real 留痕)
    - 平台鉴权头(HMAC 签名风格 / Bearer 风格 / 裸跑无鉴权)
    - 幂等键 X-Request-Id 与请求契约字段
    - 传输错误重试(首次断连自动重试 1 次成功)
    - accepted=false → no_driver 券退回
    - 回调签名校验(静态令牌 / HMAC / 错误令牌 403 / 未配置放行)
"""

import asyncio
import hashlib
import hmac
import json as _json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 必须在 import 业务模块之前设置
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ.pop("DRIDE_PARTNER_CALLBACK_TOKEN", None)

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


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


FAR = {"lat": 36.29, "lng": 117.13, "address": "郊区上车点"}
CENTER = {"lat": 36.19, "lng": 117.13, "address": "泰安市区中心"}

DEFAULT_RESPONSE = {
    "accepted": True,
    "partnerOrderId": "PDMOCK0001",
    "driver": {"name": "平台模拟司机", "phone": "13900000099",
               "plateNo": "鲁J90001", "rating": 4.6},
    "etaSeconds": 300,
}


def start_mock_platform(response=None, fail_first=False):
    """本地 mock 平台服务器(记录请求头/体, 可配置响应与首连失败)

    Returns:
        (base_url, records, server)
    """
    records = []
    config = {"response": response if response is not None
              else DEFAULT_RESPONSE, "fail_first": fail_first}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            try:
                body = _json.loads(raw or b"{}")
            except ValueError:
                body = {}
            records.append({
                "path": self.path,
                "headers": {k.lower(): v
                            for k, v in self.headers.items()},
                "body": body,
            })
            # 首连失败模拟(不回包 → 客户端 TransportError → 重试)
            if config["fail_first"] and len(records) == 1:
                return
            payload = _json.dumps(
                config["response"], ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever,
                     daemon=True).start()
    return (f"http://127.0.0.1:{server.server_address[1]}",
            records, server)


async def grant_coupon(member_id=1, order_id="ORD", amount=800.0):
    from services.ride_coupon_service import RideCouponService
    return await RideCouponService().grant_for_order(
        member_id, order_id, amount)


def patch_channel(**kwargs):
    """patch ride_dispatch_service 模块级通道常量, 返回还原函数"""
    import services.ride_dispatch_service as rds
    keys = ("DRIDE_CHANNEL_MODE", "DRIDE_PARTNER_URL",
            "DRIDE_PARTNER_APP_ID", "DRIDE_PARTNER_APP_SECRET",
            "DRIDE_PARTNER_TOKEN")
    orig = {k: getattr(rds, k) for k in keys}
    for k, v in kwargs.items():
        setattr(rds, k, v)

    def restore():
        for k, v in orig.items():
            setattr(rds, k, v)
    return restore


class TestRealChannel:
    async def run(self):
        from services.ride_dispatch_service import RideDispatchService
        from repositories.ride_repository import (
            RIDE_STATUS_NO_DRIVER, COUPON_STATUS_GRANTED,
        )

        svc = RideDispatchService()

        # ① HMAC 签名风格 + 全链路
        url, records, server = start_mock_platform()
        restore = patch_channel(DRIDE_CHANNEL_MODE="real",
                                DRIDE_PARTNER_URL=url,
                                DRIDE_PARTNER_APP_ID="app_test_001",
                                DRIDE_PARTNER_APP_SECRET="secret_test",
                                DRIDE_PARTNER_TOKEN="")
        try:
            await grant_coupon(1, "RC1", 800.0)
            r = await svc.call(1, FAR, CENTER, distance_km=11.0)
            record("real-直发成功", r["dispatchMode"] == "platform"
                   and r["platformChannel"] == "real",
                   str(r.get("platformChannel")))
            record("real-响应driver映射",
                   r["driverSnapshot"]["name"] == "平台模拟司机"
                   and r["driverSnapshot"]["partnerOrderId"]
                   == "PDMOCK0001", str(r["driverSnapshot"]))
            record("real-ETA留痕",
                   r["driverSnapshot"].get("etaSeconds") == 300)
            record("real-请求契约字段",
                   records and
                   {"rideId", "pickup", "dropoff", "couponValue",
                    "estimatedKm"} <= set(records[0]["body"]),
                   str(records[0]["body"].keys() if records else []))
            record("real-幂等键X-Request-Id",
                   records and
                   records[0]["headers"].get("x-request-id")
                   == r["rideId"], str(records[0]["headers"].get(
                       "x-request-id") if records else "-"))
            record("real-HMAC签名头齐全",
                   records and
                   records[0]["headers"].get("x-app-id")
                   == "app_test_001"
                   and "x-signature" in records[0]["headers"]
                   and "x-timestamp" in records[0]["headers"]
                   and "x-nonce" in records[0]["headers"],
                   str({k: v for k, v in records[0]["headers"].items()
                        if k.startswith("x-")}))
            record("real-无Bearer混用",
                   "authorization" not in records[0]["headers"])
        finally:
            restore()
            server.shutdown()

        # ② Bearer 风格(仅 TOKEN)
        url, records, server = start_mock_platform()
        restore = patch_channel(DRIDE_CHANNEL_MODE="real",
                                 DRIDE_PARTNER_URL=url,
                                 DRIDE_PARTNER_APP_ID="",
                                 DRIDE_PARTNER_APP_SECRET="",
                                 DRIDE_PARTNER_TOKEN="tok_bearer_test")
        try:
            await grant_coupon(1, "RC2", 800.0)
            r = await svc.call(1, FAR, CENTER, distance_km=11.0)
            record("bearer-直发成功", r["platformChannel"] == "real")
            record("bearer-Authorization头",
                   records and
                   records[0]["headers"].get("authorization")
                   == "Bearer tok_bearer_test",
                   str(records[0]["headers"].get("authorization")
                       if records else "-"))
            record("bearer-无HMAC头",
                   records and "x-signature"
                   not in records[0]["headers"])
        finally:
            restore()
            server.shutdown()

        # ③ 裸跑(无任何凭证)
        url, records, server = start_mock_platform()
        restore = patch_channel(DRIDE_CHANNEL_MODE="real",
                                 DRIDE_PARTNER_URL=url,
                                 DRIDE_PARTNER_APP_ID="",
                                 DRIDE_PARTNER_APP_SECRET="",
                                 DRIDE_PARTNER_TOKEN="")
        try:
            await grant_coupon(1, "RC3", 800.0)
            r = await svc.call(1, FAR, CENTER, distance_km=11.0)
            record("裸跑-无鉴权头直发成功", r["platformChannel"] == "real"
                   and "authorization" not in records[0]["headers"]
                   and "x-signature" not in records[0]["headers"])
        finally:
            restore()
            server.shutdown()

        # ④ 首连失败 → 重试 1 次成功
        url, records, server = start_mock_platform(fail_first=True)
        restore = patch_channel(DRIDE_CHANNEL_MODE="real",
                                 DRIDE_PARTNER_URL=url)
        try:
            await grant_coupon(1, "RC4", 800.0)
            r = await svc.call(1, FAR, CENTER, distance_km=11.0)
            record("重试-首连失败重试成功", r["platformChannel"] == "real"
                   and len(records) == 2,
                   f"请求数{len(records)}")
        finally:
            restore()
            server.shutdown()

        # ⑤ accepted=false → no_driver 券退回
        url, records, server = start_mock_platform(
            response={"accepted": False})
        restore = patch_channel(DRIDE_CHANNEL_MODE="real",
                                 DRIDE_PARTNER_URL=url)
        try:
            await grant_coupon(1, "RC5", 800.0)
            r = await svc.call(1, FAR, CENTER, distance_km=11.0)
            record("拒单-no_driver留痕",
                   r["status"] == RIDE_STATUS_NO_DRIVER
                   and "全轨无运力" in r.get("cancelReason", ""),
                   str(r.get("status")))
            coupon = await svc.repo.get_coupon(r["couponCode"])
            record("拒单-券退回", coupon["status"]
                   == COUPON_STATUS_GRANTED)
        finally:
            restore()
            server.shutdown()

        # ⑥ real + 不可达 → fail-hard 抛错
        restore = patch_channel(DRIDE_CHANNEL_MODE="real",
                                 DRIDE_PARTNER_URL="http://127.0.0.1:9/dispatch")
        try:
            await grant_coupon(1, "RC6", 800.0)
            try:
                await svc.call(1, FAR, CENTER, distance_km=11.0)
                record("failhard-不可达抛错", False, "未抛出")
            except ValueError as e:
                record("failhard-不可达抛错", "不可用" in str(e))
        finally:
            restore()


class TestCallbackSignature:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.ride_routes import register_ride_routes
        from services.ride_dispatch_service import RideDispatchService

        app = FastAPI()
        register_ride_routes(app)
        client = TestClient(app)

        # 前置: 一单平台直发行程(mock 模式)
        svc = RideDispatchService()
        await grant_coupon(1, "CB1", 800.0)
        r = await svc.call(1, FAR, CENTER, distance_km=11.0)
        po = r["driverSnapshot"]["partnerOrderId"]

        # ① 未配置令牌 → 放行(兼容口径)
        os.environ.pop("DRIDE_PARTNER_CALLBACK_TOKEN", None)
        resp = client.post("/api/ride/partner/callback", json={
            "partnerOrderId": po, "event": "started"})
        record("回调-未配置令牌放行", resp.status_code == 200,
               str(resp.status_code))

        # 后续事件重置(供签名测试): 用 cancelled 产生新行程
        await grant_coupon(1, "CB2", 800.0)
        r = await svc.call(1, FAR, CENTER, distance_km=11.0)
        po2 = r["driverSnapshot"]["partnerOrderId"]

        # ② 配置令牌后
        os.environ["DRIDE_PARTNER_CALLBACK_TOKEN"] = "tok_secret_001"
        try:
            body_bytes = _json.dumps({
                "partnerOrderId": po2, "event": "cancelled"
            }).encode("utf-8")

            # 正确静态令牌 → 放行
            resp = client.post(
                "/api/ride/partner/callback", content=body_bytes,
                headers={"Content-Type": "application/json",
                         "X-Partner-Token": "tok_secret_001"})
            record("回调-静态令牌放行", resp.status_code == 200,
                   f"{resp.status_code} {resp.text[:80]}")

            # 错误令牌 → 403
            await grant_coupon(1, "CB3", 800.0)
            r = await svc.call(1, FAR, CENTER, distance_km=11.0)
            po3 = r["driverSnapshot"]["partnerOrderId"]
            body_bytes = _json.dumps({
                "partnerOrderId": po3, "event": "cancelled"
            }).encode("utf-8")
            resp = client.post(
                "/api/ride/partner/callback", content=body_bytes,
                headers={"Content-Type": "application/json",
                         "X-Partner-Token": "wrong"})
            record("回调-错误令牌403", resp.status_code == 403,
                   str(resp.status_code))

            # HMAC 签名 → 放行
            await grant_coupon(1, "CB4", 800.0)
            r = await svc.call(1, FAR, CENTER, distance_km=11.0)
            po4 = r["driverSnapshot"]["partnerOrderId"]
            body_bytes = _json.dumps({
                "partnerOrderId": po4, "event": "cancelled"
            }).encode("utf-8")
            sig = hmac.new(b"tok_secret_001", body_bytes,
                           hashlib.sha256).hexdigest()
            resp = client.post(
                "/api/ride/partner/callback", content=body_bytes,
                headers={"Content-Type": "application/json",
                         "X-Partner-Signature": sig})
            record("回调-HMAC签名放行", resp.status_code == 200,
                   f"{resp.status_code} {resp.text[:80]}")

            # 篡改体 + 原签名 → 403
            await grant_coupon(1, "CB5", 800.0)
            r = await svc.call(1, FAR, CENTER, distance_km=11.0)
            po5 = r["driverSnapshot"]["partnerOrderId"]
            tampered = _json.dumps({
                "partnerOrderId": po5, "event": "cancelled",
                "hack": True
            }).encode("utf-8")
            resp = client.post(
                "/api/ride/partner/callback", content=tampered,
                headers={"Content-Type": "application/json",
                         "X-Partner-Signature": sig})
            record("回调-篡改体403", resp.status_code == 403,
                   str(resp.status_code))

            # 无任何凭证头 → 403
            await grant_coupon(1, "CB6", 800.0)
            r = await svc.call(1, FAR, CENTER, distance_km=11.0)
            po6 = r["driverSnapshot"]["partnerOrderId"]
            resp = client.post("/api/ride/partner/callback", json={
                "partnerOrderId": po6, "event": "cancelled"})
            record("回调-无凭证头403", resp.status_code == 403,
                   str(resp.status_code))
        finally:
            os.environ.pop("DRIDE_PARTNER_CALLBACK_TOKEN", None)


async def main():
    test_classes = [
        ("real 轨全链路", TestRealChannel),
        ("回调签名校验", TestCallbackSignature),
    ]
    print("=" * 62)
    print("41号·直发通道真实接入专项测试(mock 平台服务器)")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, repr(e))

    print("\n" + "-" * 62)
    print("\n".join(RESULTS))
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
