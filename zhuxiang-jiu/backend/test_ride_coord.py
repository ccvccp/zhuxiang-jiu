"""41号·坐标转换专项测试(WGS84 ↔ GCJ-02, 凭证前置开发)

运行方式:
    python test_ride_coord.py

覆盖:
    - 正向转换精度(泰安锚点偏移 50-500m 合理区间)
    - 逆转换往返(闭环误差 ≤ 2m)
    - 境外坐标不偏移
    - 边界/异常输入容错
    - 开关口径(wgs84 原样 / gcj02 转换)
    - 直发链路坐标传递(mock 平台服务器捕获请求坐标)
"""

import asyncio
import json as _json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
# 开关在 ride_coord_service 导入时读取, 测试内 patch 模块常量
os.environ.pop("DRIDE_COORD_SYS", None)

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


# 本站种子锚点(泰安, WGS84)
TAIAN_WGS = (117.13, 36.19)
FAR_WGS = (117.13, 36.29)
CENTER_ADDR = {"lat": 36.19, "lng": 117.13, "address": "泰安市区中心"}
FAR_ADDR = {"lat": 36.29, "lng": 117.13, "address": "郊区上车点"}


class TestTransform:
    async def run(self):
        from services.ride_coord_service import (
            wgs84_to_gcj02, gcj02_to_wgs84, out_of_china,
        )

        # 正向: 泰安锚点偏移(国测局算法偏移典型 50-500m,
        # 0.001° 纬度 ≈ 111m; 偏移量应在 [0.0003°, 0.007°] 量级)
        g_lng, g_lat = wgs84_to_gcj02(*TAIAN_WGS)
        d_lng, d_lat = g_lng - TAIAN_WGS[0], g_lat - TAIAN_WGS[1]
        record("正向-泰安有偏移", abs(d_lng) > 1e-4
               and abs(d_lat) > 1e-4,
               f"Δ({d_lng:.6f}, {d_lat:.6f})")
        record("正向-偏移量级合理",
               abs(d_lng) < 0.01 and abs(d_lat) < 0.01,
               f"Δ({d_lng:.6f}, {d_lat:.6f})")

        # 逆转换往返闭环(误差 ≤ 2m ≈ 0.00002°)
        w_lng, w_lat = gcj02_to_wgs84(g_lng, g_lat)
        record("往返-闭环误差≤2m",
               abs(w_lng - TAIAN_WGS[0]) < 2e-5
               and abs(w_lat - TAIAN_WGS[1]) < 2e-5,
               f"Δ({w_lng - TAIAN_WGS[0]:.7f}, "
               f"{w_lat - TAIAN_WGS[1]:.7f})")

        # 多点往返(泰安/北京/上海/广州)
        for name, (lng, lat) in {
                "泰安": (117.13, 36.19), "北京": (116.40, 39.90),
                "上海": (121.47, 31.23), "广州": (113.26, 23.13)}.items():
            g = wgs84_to_gcj02(lng, lat)
            w = gcj02_to_wgs84(*g)
            record(f"往返-{name}闭环",
                   abs(w[0] - lng) < 2e-5 and abs(w[1] - lat) < 2e-5,
                   f"Δ({w[0] - lng:.7f}, {w[1] - lat:.7f})")

        # 境外不偏移(纽约/悉尼)
        for name, (lng, lat) in {"纽约": (-74.0, 40.7),
                                 "悉尼": (151.2, -33.9)}.items():
            g = wgs84_to_gcj02(lng, lat)
            record(f"境外-{name}原样", g == (lng, lat), str(g))
            record(f"境外-{name}判定", out_of_china(lng, lat))

        # 境内判定
        record("境内-泰安判定", not out_of_china(*TAIAN_WGS))


class TestSwitch:
    async def run(self):
        import services.ride_coord_service as rcs
        from services.ride_coord_service import to_partner_coords

        # wgs84(默认): 原样
        orig = rcs.DRIDE_COORD_SYS
        try:
            rcs.DRIDE_COORD_SYS = "wgs84"
            lng, lat = to_partner_coords(*TAIAN_WGS)
            record("开关-wgs84原样", (lng, lat) == TAIAN_WGS,
                   f"{(lng, lat)}")

            # gcj02: 转换
            rcs.DRIDE_COORD_SYS = "gcj02"
            lng, lat = to_partner_coords(*TAIAN_WGS)
            g = rcs.wgs84_to_gcj02(*TAIAN_WGS)
            record("开关-gcj02转换", (lng, lat) == g,
                   f"{(lng, lat)} vs {g}")

            # 非法值: 容错按原样(不崩)
            rcs.DRIDE_COORD_SYS = "bogus"
            lng, lat = to_partner_coords(*TAIAN_WGS)
            record("开关-非法值容错原样", (lng, lat) == TAIAN_WGS)
        finally:
            rcs.DRIDE_COORD_SYS = orig


class TestDispatchLink:
    async def run(self):
        from services.ride_dispatch_service import RideDispatchService
        from services.ride_coupon_service import RideCouponService
        import services.ride_coord_service as rcs
        import services.ride_dispatch_service as rds

        # mock 平台服务器捕获请求坐标
        records = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = _json.loads(self.rfile.read(length) or b"{}")
                records.append(body)
                payload = _json.dumps({
                    "accepted": True, "partnerOrderId": "PDCOORD01",
                    "driver": {"name": "平台司机X", "phone": "139",
                               "plateNo": "鲁J9", "rating": 4.5},
                    "etaSeconds": 300}, ensure_ascii=False).encode()
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
        url = f"http://127.0.0.1:{server.server_address[1]}"

        orig_mode, orig_url = rds.DRIDE_CHANNEL_MODE, rds.DRIDE_PARTNER_URL
        orig_sys = rcs.DRIDE_COORD_SYS
        rds.DRIDE_CHANNEL_MODE = "real"
        rds.DRIDE_PARTNER_URL = url
        try:
            svc = RideDispatchService()
            # wgs84 开关: 请求坐标 = 本站原坐标
            rcs.DRIDE_COORD_SYS = "wgs84"
            await RideCouponService().grant_for_order(
                1, "COORD1", 800.0)
            await svc.call(1, FAR_ADDR, CENTER_ADDR, distance_km=11.0)
            req0 = records[-1]
            record("链路-wgs84原坐标直传",
                   req0["pickup"]["lat"] == 36.29
                   and req0["pickup"]["lng"] == 117.13,
                   str(req0["pickup"]))

            # gcj02 开关: 请求坐标 = 转换后坐标
            rcs.DRIDE_COORD_SYS = "gcj02"
            await RideCouponService().grant_for_order(
                1, "COORD2", 800.0)
            await svc.call(1, FAR_ADDR, CENTER_ADDR, distance_km=11.0)
            req1 = records[-1]
            expected_lng, expected_lat = rcs.wgs84_to_gcj02(117.13, 36.29)
            record("链路-gcj02坐标转换上行",
                   abs(req1["pickup"]["lat"] - expected_lat) < 1e-6
                   and abs(req1["pickup"]["lng"] - expected_lng) < 1e-6,
                   f"{req1['pickup']} vs "
                   f"({expected_lng}, {expected_lat})")
            record("链路-转换非原坐标",
                   req1["pickup"]["lat"] != req0["pickup"]["lat"])
            # 本站存储口径不变(仍是 WGS84)
            ride = await svc.repo.get_ride(
                [r for r in await svc.repo.list_rides(limit=10)][-1]
                ["rideId"])
            record("链路-本站存储仍WGS84",
                   ride["pickup"]["lat"] == 36.29)
        finally:
            rds.DRIDE_CHANNEL_MODE = orig_mode
            rds.DRIDE_PARTNER_URL = orig_url
            rcs.DRIDE_COORD_SYS = orig_sys
            server.shutdown()


async def main():
    test_classes = [
        ("转换算法", TestTransform),
        ("坐标系开关", TestSwitch),
        ("直发链路坐标传递", TestDispatchLink),
    ]
    print("=" * 62)
    print("41号·坐标转换专项测试(WGS84 ↔ GCJ-02)")
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
