"""41号·AI智能代驾模块 实机验收脚本(Docker 全量口径 P0+P1+P2+P3)

运行前提:
    - Docker 容器运行中(backend+redis, docker compose -p zhuxiang-jiu up -d)
    - 直达容器: http://127.0.0.2:8000(本机 8000 三方占用, localhost 不稳)

内置 Redis 前置准备(落实 40号复盘行动清单"验收脚本内置清理"):
    - 清 zhuxiang:ride:* 全部键(行程/券/司机池/审查/结算/风险/评价)
    - 种子司机全部置 offline(保证后续派单确定性: 仅测试司机 online)
    - 会员1 置 L5(司机资格门槛测试数据)

验收章节(14 章):
    01 连通与鉴权 / 02 券引擎梯度 / 03 订单支付自动赠券 E2E /
    04 司机资格 AI 审查 / 05 司机池管理 / 06 智能派单 /
    07 行程生命周期+AI 结算 / 08 取消免责窗口 / 09 三轨溢出平台直发 /
    10 平台直发回调 / 11 安全监控(POI/扫描/面板/处置) /
    12 双向评价 AI 审评 / 13 无券拒绝 / 14 管理端全景

退出码: 全绿 0 / 任一失败 1
"""

import json
import sys
import urllib.request
import urllib.error

import redis

B = "http://127.0.0.2:8000"   # 直达 Docker 容器(项目约定)
ADMIN = {"X-Role": "admin"}
M1 = {"X-Member-Id": "1"}      # 会员1(L5, 乘客+司机会员双角色分阶段测试)

PASS = 0
FAIL = 0


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} -- {detail}")


def req(method, path, body=None, headers=None):
    """统一请求: 返回 (status, json); HTTPError 同构返回"""
    data = (json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None else None)
    r = urllib.request.Request(B + path, data=data, method=method)
    r.add_header("Content-Type", "application/json; charset=utf-8")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": repr(e)}


def err_msg(body):
    return str(body.get("error") or body.get("detail") or "")


# ============================================================
# Redis 前置准备(清理+种子确定性)
# ============================================================

def prepare_redis():
    """清 41号全部键 + 种子司机 offline + 会员1 置 L5

    键族: zhuxiang:ride:*(四表+风险+评价+seq);
    种子司机 offline → 派单确定性(仅测试司机 online);
    会员1 L5 → 司机资格硬门槛数据。
    """
    r = redis.Redis(host="127.0.0.1", port=6379, db=0,
                    decode_responses=True)
    keys = list(r.keys("zhuxiang:ride:*"))
    if keys:
        r.delete(*keys)
    # 种子司机 1-8 置 offline(键由服务端惰性灌入——先摸一遍 API 触发)
    req("GET", "/api/ride/admin/pool", None, ADMIN)
    for did in range(1, 9):
        key = f"zhuxiang:ride:ride_driver_pool:{did}"
        if r.exists(key):
            r.hset(key, mapping={"status": "offline",
                                 "currentRideId": ""})
    # 会员1 置 L5 SVIP(司机资格门槛)
    r.hset("zhuxiang:member:1", mapping={
        "level": "5", "growth_value": "9999",
        "birthdate": "1990-01-01", "ageVerified": "1",
        "ageConfirmed": "1", "status": "1",
        "phone": "13800000001", "nickname": "测试会员小竹",
    })
    return len(keys)


def set_member_level(level):
    """会员1 等级切换(司机资格门槛正反向测试)"""
    r = redis.Redis(host="127.0.0.1", port=6379, db=0,
                    decode_responses=True)
    r.hset("zhuxiang:member:1", "level", str(level))


def clear_coupons():
    """中途清券+券包(无券拒绝测试前置)"""
    r = redis.Redis(host="127.0.0.1", port=6379, db=0,
                    decode_responses=True)
    for pattern in ("zhuxiang:ride:ride_coupons:*",
                    "zhuxiang:ride:ride_coupon_packages:*"):
        keys = list(r.keys(pattern))
        if keys:
            r.delete(*keys)


# ============================================================
# 验收主体
# ============================================================

def main():
    print("=" * 62)
    print("41号·AI智能代驾模块 实机验收(Docker 全量口径 P0-P3)")
    print("=" * 62)

    cleaned = prepare_redis()
    print(f"[前置] 已清理 ride 键 {cleaned} 个; 种子司机 offline; 会员1 L5")

    # --------------------------------------------------------
    # 01 连通与鉴权
    # --------------------------------------------------------
    print("\n[01. 连通与鉴权]")
    s, b = req("GET", "/api/decision/health")
    record("连通-容器健康", s == 200, f"{s} {err_msg(b)}")

    s, b = req("GET", "/api/ride/coupons")
    record("鉴权-券包缺头403", s == 403, str(s))
    s, b = req("GET", "/api/ride/admin/overview")
    record("鉴权-概览非admin403", s == 403, str(s))

    s, b = req("GET", "/api/ride/admin/overview", None, ADMIN)
    record("概览-空池初始化", s == 200 and b.get("poolTotal") == 8
           and b.get("onlineCount") == 0, str(b)[:150])

    # --------------------------------------------------------
    # 02 券引擎梯度(admin 补发口径)
    # --------------------------------------------------------
    print("\n[02. 券引擎梯度]")
    s, b = req("POST", "/api/ride/coupons/grant", {
        "memberId": 1, "orderId": "LIVE-A", "amount": 800}, ADMIN)
    record("券-一档1张", s == 200 and b.get("granted") == 1,
           str(b))
    s, b = req("POST", "/api/ride/coupons/grant", {
        "memberId": 1, "orderId": "LIVE-A", "amount": 800}, ADMIN)
    record("券-同单幂等", s == 200 and b.get("granted") == 0
           and b.get("skipped") == 1, str(b))
    s, b = req("POST", "/api/ride/coupons/grant", {
        "memberId": 1, "orderId": "LIVE-B", "amount": 2500}, ADMIN)
    record("券-二档2张", s == 200 and b.get("granted") == 2, str(b))
    s, b = req("POST", "/api/ride/coupons/grant", {
        "memberId": 1, "orderId": "LIVE-C", "amount": 3500}, ADMIN)
    record("券-三档3张", s == 200 and b.get("granted") == 3, str(b))
    s, b = req("POST", "/api/ride/coupons/grant", {
        "memberId": 1, "orderId": "LIVE-D", "amount": 499}, ADMIN)
    record("券-未达门槛0张", s == 200 and b.get("granted") == 0
           and "门槛" in str(b.get("reason")), str(b))

    s, b = req("GET", "/api/ride/coupons", None, M1)
    record("券包-聚合(1+2+3=6)", s == 200 and b.get("holdCount") == 6
           and b.get("totalGranted") == 6, str(b.get("holdCount")))
    record("券包-持有上限6", b.get("holdCap") == 6)
    s, b = req("POST", "/api/ride/coupons/grant", {
        "memberId": 1, "orderId": "LIVE-E", "amount": 800}, ADMIN)
    record("券-达上限不再发", s == 200 and b.get("granted") == 0
           and "上限" in str(b.get("reason")), str(b))

    # 中途清券(行程测试从头计, 同时验上限重置)
    clear_coupons()
    s, b = req("GET", "/api/ride/coupons", None, M1)
    record("券包-清理后归零", s == 200 and b.get("holdCount") == 0,
           str(b.get("holdCount")))

    # --------------------------------------------------------
    # 03 订单支付自动赠券 E2E
    # --------------------------------------------------------
    print("\n[03. 订单支付自动赠券 E2E]")
    s, b = req("GET", "/api/product/list")
    product = ((b.get("products") or [{}])[0])
    pid = product.get("product_id")
    price = product.get("price")
    record("商品-取到种子商品", bool(pid), str(pid))

    s, b = req("POST", "/api/order/create", {
        "items": [{"productId": pid, "productName": product.get("name"),
                   "quantity": 20, "unitPrice": price}],
        "address": {"name": "张三", "phone": "13800000001",
                    "province": "山东省", "city": "泰安市",
                    "district": "泰山区", "detail": "竹香路 1 号"},
    }, M1)
    order_id = ((b.get("details") or {}).get("orderId")
                or b.get("orderId") or "")
    record("订单-创建成功", s == 200 and bool(order_id),
           f"{s} {err_msg(b)}")
    actual = ((b.get("details") or {}).get("priceDetail")
              or b.get("priceDetail") or {}).get("actualAmount")
    record("订单-L5实付≥1000", (actual or 0) >= 1000, str(actual))

    s, b = req("POST", f"/api/order/{order_id}/pay",
               {"method": "wechat"}, M1)
    record("订单-支付成功", s == 200, f"{s} {err_msg(b)}")

    s, b = req("GET", "/api/ride/coupons", None, M1)
    expected_n = 2 if (actual or 0) < 3000 else 3
    record("E2E-支付自动赠券入包", b.get("holdCount") == expected_n,
           f"实付{actual} 持有{b.get('holdCount')} 期望{expected_n}")
    codes = sorted(c.get("code") for c in (b.get("coupons") or []))
    record("E2E-券码来源订单", codes == sorted(
        f"RIDE{order_id}_{i + 1}" for i in range(expected_n)),
        str(codes))
    record("E2E-券面值60", all(c.get("value") == 60
                            for c in (b.get("coupons") or [])))

    # --------------------------------------------------------
    # 04 司机资格 AI 审查
    # --------------------------------------------------------
    print("\n[04. 司机资格 AI 审查]")
    set_member_level(1)
    s, b = req("POST", "/api/ride/driver/apply", {
        "idNumber": "370900199001010011",
        "licenseNumber": "370900123456", "licenseClass": "C1",
        "drivingYears": 8, "accidentFreeDecl": True,
        "drunkFreeDecl": True, "emergencyContact": "王紧急",
        "bambooScore": 800}, M1)
    record("司机-非SVIP硬拒409", s == 409 and "SVIP" in err_msg(b),
           f"{s} {err_msg(b)}")

    set_member_level(5)
    s, b = req("POST", "/api/ride/driver/apply", {
        "idNumber": "370900199001010011",
        "licenseNumber": "370900123456", "licenseClass": "C1",
        "drivingYears": 8, "accidentFreeDecl": True,
        "drunkFreeDecl": True, "emergencyContact": "王紧急",
        "bambooScore": 800}, M1)
    record("司机-SVIP申请自动通过", s == 200
           and b.get("status") == "approved", f"{s} {str(b)[:150]}")
    record("司机-评分≥70", (b.get("score") or 0) >= 70,
           str(b.get("score")))
    driver_id = b.get("driverId")
    record("司机-通过即入池", bool(driver_id), str(driver_id))

    s, b = req("GET", "/api/ride/driver/application", None, M1)
    record("司机-审查进度查询", s == 200
           and (b.get("application") or {}).get("status") == "approved",
           f"{s}")

    s, b = req("POST", "/api/ride/driver/apply", {
        "idNumber": "370900199001010011",
        "licenseNumber": "370900123456", "licenseClass": "C1",
        "drivingYears": 8, "accidentFreeDecl": True,
        "drunkFreeDecl": True, "emergencyContact": "王紧急"}, M1)
    record("司机-重复申请409", s == 409, str(s))

    # --------------------------------------------------------
    # 05 司机池管理
    # --------------------------------------------------------
    print("\n[05. 司机池管理]")
    s, b = req("POST", "/api/ride/driver/status",
               {"status": "online"}, M1)
    record("池-无牌照上线409", s == 409, f"{s} {err_msg(b)}")

    s, b = req("POST", "/api/ride/driver/profile", {
        "plateNo": "鲁J88888", "city": "泰安",
        "lat": 36.1905, "lng": 117.130}, M1)
    record("池-补充牌照与位置", s == 200, f"{s} {err_msg(b)}")

    s, b = req("POST", "/api/ride/driver/status",
               {"status": "online"}, M1)
    record("池-上线成功", s == 200
           and (b.get("driver") or {}).get("status") == "online",
           f"{s}")

    s, b = req("GET", "/api/ride/admin/pool", None, ADMIN,
               )
    # Query 参数走 path
    s, b = req("GET", "/api/ride/admin/pool?status=online", None, ADMIN)
    record("池-在线过滤仅测试司机", s == 200 and b.get("total") == 1
           and (b.get("drivers") or [{}])[0].get("driverId")
           == driver_id, str(b.get("total")))

    # --------------------------------------------------------
    # 06 智能派单
    # --------------------------------------------------------
    print("\n[06. 智能派单]")
    near = {"lat": 36.1905, "lng": 117.130, "address": "泰安老字号饭店"}
    center = {"lat": 36.19, "lng": 117.13, "address": "泰安市区中心"}

    s, b = req("POST", "/api/ride/coupons/grant", {
        "memberId": 1, "orderId": "LIVE-R1", "amount": 800}, ADMIN)
    s, b = req("POST", "/api/ride/call", {
        "pickup": near, "dropoff": center, "distanceKm": 8.0}, M1)
    ride1 = b.get("rideId")
    record("派单-叫单成功dispatched", s == 200
           and b.get("status") == "dispatched", f"{s} {str(b)[:150]}")
    snap = b.get("driverSnapshot") or {}
    record("派单-测试司机唯一在线选中",
           snap.get("driverId") == driver_id, str(snap.get("driverId")))
    record("派单-AI模式留痕", b.get("dispatchMode") == "ai"
           and (b.get("dispatchScore") or 0) >= 70,
           str(b.get("dispatchScore")))
    record("派单-自营轨道", snap.get("track") == "self")
    record("派单-POI饮酒场景留痕", b.get("poiCategory") == "drinking",
           str(b.get("poiCategory")))

    # 在忙跳过: 司机占用中再叫单 → 无可用(种子 offline) → 平台直发
    s, b = req("POST", "/api/ride/coupons/grant", {
        "memberId": 1, "orderId": "LIVE-R2", "amount": 800}, ADMIN)
    s, b = req("POST", "/api/ride/call", {
        "pickup": near, "dropoff": center, "distanceKm": 8.0}, M1)
    record("派单-在忙溢出平台直发", s == 200
           and b.get("dispatchMode") == "platform",
           str(b.get("dispatchMode")))
    ride2 = b.get("rideId")
    po2 = (b.get("driverSnapshot") or {}).get("partnerOrderId")
    record("派单-平台mock回执", bool(po2), str(po2))
    record("派单-mock通道标记", b.get("platformChannel") == "mock",
           str(b.get("platformChannel")))

    # --------------------------------------------------------
    # 07 行程生命周期 + AI 结算
    # --------------------------------------------------------
    print("\n[07. 行程生命周期+AI 结算]")
    s, b = req("POST", f"/api/ride/driver/orders/{ride1}/accept",
               None, M1)
    record("行程-司机接单arriving", s == 200
           and (b.get("ride") or {}).get("status") == "driver_arriving",
           f"{s} {err_msg(b)}")
    s, b = req("POST", f"/api/ride/driver/orders/{ride1}/start",
               None, M1)
    record("行程-开始started", s == 200
           and (b.get("ride") or {}).get("status") == "trip_started",
           f"{s}")

    s, b = req("POST", f"/api/ride/driver/orders/{ride1}/complete", {
        "durationMinutes": 0, "pricingHour": 14}, M1)
    ride = b.get("ride") or {}
    record("行程-完成结算settled", s == 200
           and ride.get("status") == "settled", f"{s} {str(b)[:150]}")
    pricing = ride.get("pricing") or {}
    record("结算-8km日间总额50", pricing.get("totalAmount") == 50.0,
           str(pricing.get("totalAmount")))
    record("结算-券抵50本站付", pricing.get("couponDeduction") == 50.0,
           str(pricing.get("couponDeduction")))
    record("结算-乘客补差0", pricing.get("extraCharge") == 0.0)

    s, b = req("GET", "/api/ride/coupons", None, M1)
    record("结算-券包核销计数", b.get("totalUsed") == 1,
           str(b.get("totalUsed")))

    s, b = req("GET", "/api/ride/driver/settlements", None, M1)
    settlements = b.get("settlements") or []
    record("结算-司机结算单", s == 200 and len(settlements) == 1
           and settlements[0].get("totalAmount") == 50.0,
           str(len(settlements)))
    record("结算-自营直付paid",
           settlements and settlements[0].get("payoutStatus") == "paid")

    # --------------------------------------------------------
    # 08 取消免责窗口
    # --------------------------------------------------------
    print("\n[08. 取消免责窗口]")
    s, b = req("POST", "/api/ride/coupons/grant", {
        "memberId": 1, "orderId": "LIVE-CANCEL", "amount": 800}, ADMIN)
    s, b = req("POST", "/api/ride/call", {
        "pickup": near, "dropoff": center, "distanceKm": 8.0}, M1)
    ride3 = b.get("rideId")
    coupon3 = b.get("couponCode")
    record("取消-叫单前置", s == 200 and bool(ride3), str(s))

    s, b = req("POST", f"/api/ride/orders/{ride3}/cancel",
               {"reason": "乘客改主意"}, M1)
    ride = b.get("ride") or {}
    record("取消-窗口内成功", s == 200
           and ride.get("status") == "cancelled"
           and ride.get("cancelWindowFree") is True, f"{s}")
    s, b = req("GET", f"/api/ride/coupons/{coupon3}", None, M1)
    record("取消-券退回留用", (b.get("coupon") or {}).get("status")
           == "granted", str((b.get("coupon") or {}).get("status")))

    # 退回券再用(FEFO)
    s, b = req("POST", "/api/ride/call", {
        "pickup": near, "dropoff": center, "distanceKm": 8.0}, M1)
    record("取消-退回券可再用", s == 200
           and b.get("couponCode") == coupon3,
           str(b.get("couponCode")))
    ride4 = b.get("rideId")
    # 收尾: 该行程走完(供评价章节复用)
    req("POST", f"/api/ride/driver/orders/{ride4}/accept", None, M1)
    req("POST", f"/api/ride/driver/orders/{ride4}/start", None, M1)
    req("POST", f"/api/ride/driver/orders/{ride4}/complete", {
        "durationMinutes": 0, "pricingHour": 14}, M1)

    # --------------------------------------------------------
    # 09 三轨溢出平台直发(超半径)
    # --------------------------------------------------------
    print("\n[09. 三轨溢出平台直发]")
    far = {"lat": 36.29, "lng": 117.13, "address": "郊区上车点"}
    s, b = req("POST", "/api/ride/coupons/grant", {
        "memberId": 1, "orderId": "LIVE-FAR", "amount": 800}, ADMIN)
    s, b = req("POST", "/api/ride/call", {
        "pickup": far, "dropoff": center, "distanceKm": 11.0}, M1)
    ride5 = b.get("rideId")
    po5 = (b.get("driverSnapshot") or {}).get("partnerOrderId")
    record("溢出-超半径平台直发", s == 200
           and b.get("dispatchMode") == "platform"
           and bool(po5), f"{s} {str(b)[:120]}")
    record("溢出-平台司机mock", (b.get("driverSnapshot") or {})
           .get("name") == "平台司机丙")

    s, b = req("POST", "/api/ride/call", {
        "pickup": near, "dropoff": center, "distanceKm": 41.0}, M1)
    record("溢出-超市内范围409", s == 409 and "市内" in err_msg(b),
           f"{s} {err_msg(b)}")

    # --------------------------------------------------------
    # 10 平台直发回调
    # --------------------------------------------------------
    print("\n[10. 平台直发回调]")
    s, b = req("POST", "/api/ride/partner/callback", {
        "partnerOrderId": po5, "event": "started"})
    record("回调-started", s == 200
           and (b.get("ride") or {}).get("status") == "trip_started",
           f"{s} {err_msg(b)}")

    s, b = req("POST", "/api/ride/partner/callback", {
        "partnerOrderId": po5, "event": "completed",
        "trace": {"actualKm": 12.0, "durationMinutes": 30,
                   "pricingHour": 14}})
    ride = b.get("ride") or {}
    record("回调-completed触发AI结算", s == 200
           and ride.get("status") == "settled", f"{s}")
    pricing = ride.get("pricing") or {}
    record("回调-实际里程计价70", pricing.get("totalAmount") == 70.0,
           str(pricing.get("totalAmount")))
    record("回调-拆分券60补差10",
           pricing.get("couponDeduction") == 60.0
           and pricing.get("extraCharge") == 10.0, str(pricing))
    record("回调-trace留痕", (ride.get("partnerTrace") or {})
           .get("actualKm") == 12.0)

    # ride2(在忙溢出的平台单) → 回调取消
    s, b = req("POST", "/api/ride/partner/callback", {
        "partnerOrderId": po2, "event": "cancelled"})
    record("回调-cancelled", s == 200
           and (b.get("ride") or {}).get("status") == "cancelled",
           f"{s}")

    s, b = req("POST", "/api/ride/partner/callback", {
        "partnerOrderId": po5, "event": "flying"})
    record("回调-未知事件409", s == 409, str(s))
    s, b = req("POST", "/api/ride/partner/callback", {
        "partnerOrderId": "PD00000000", "event": "started"})
    record("回调-未知单号404", s == 404, str(s))

    # --------------------------------------------------------
    # 11 安全监控
    # --------------------------------------------------------
    print("\n[11. 安全监控]")
    # POI 高频: 中性地址连续叫单(取消退券)第 3 次触发
    neutral = {"lat": 36.19, "lng": 117.14, "address": "泰安火车站广场"}
    flagged = None
    for i in range(3):
        s, b = req("POST", "/api/ride/coupons/grant", {
            "memberId": 1, "orderId": f"LIVE-POI{i}", "amount": 800},
            ADMIN)
        s, b = req("POST", "/api/ride/call", {
            "pickup": neutral, "dropoff": center, "distanceKm": 8.0},
            M1)
        if b.get("riskFlag"):
            flagged = b
            break
        req("POST", f"/api/ride/orders/{b.get('rideId')}/cancel",
            {"reason": "测试取消"}, M1)
    record("安全-中性POI高频第3次触发", bool(flagged),
           str((flagged or {}).get("riskFlag")))

    s, b = req("POST", "/api/ride/admin/safety/scan", None, ADMIN)
    record("安全-超时扫描端点", s == 200 and "warnings" in b,
           f"{s}")

    s, b = req("GET", "/api/ride/admin/risk-panel", None, ADMIN)
    panel = b
    record("安全-风险面板聚合", s == 200
           and (b.get("byType") or {}).get("poi_high_frequency") == 1,
           str(b.get("byType")))
    risk_events = [e for e in (b.get("events") or [])
                   if not e.get("resolved")]
    if risk_events:
        rid = risk_events[0]["riskId"]
        s, b = req("POST",
                   f"/api/ride/admin/risk-events/{rid}/resolve",
                   {"note": "已核实正常通勤"}, ADMIN)
        record("安全-风险处置", s == 200
               and (b.get("event") or {}).get("resolved") is True,
               f"{s}")
    else:
        record("安全-风险处置", True, "无未处置事件")

    # --------------------------------------------------------
    # 12 双向评价 AI 审评
    # --------------------------------------------------------
    print("\n[12. 双向评价 AI 审评]")
    # ride1/ride4 已 settled; 先取司机当前评分
    s, b = req("GET", "/api/ride/admin/pool", None, ADMIN)
    drivers = {d.get("driverId"): d for d in (b.get("drivers") or [])}
    rating_before = (drivers.get(driver_id) or {}).get("rating")

    s, b = req("POST", f"/api/ride/orders/{ride1}/review", {
        "direction": "passenger_to_driver", "score": 4,
        "content": "师傅提前到, 开车稳, 整体不错"}, M1)
    review = b.get("review") or {}
    record("评价-中评show", s == 200 and review.get("action") == "show",
           f"{s} {str(b)[:120]}")
    record("评价-评分回写标记", review.get("ratingApplied") is True)

    s, b = req("GET", "/api/ride/admin/pool", None, ADMIN)
    drivers = {d.get("driverId"): d for d in (b.get("drivers") or [])}
    rating_after = (drivers.get(driver_id) or {}).get("rating")
    record("评价-司机评分回写", rating_after != rating_before
           and rating_after is not None,
           f"{rating_before} → {rating_after}")

    s, b = req("POST", f"/api/ride/orders/{ride1}/review", {
        "direction": "passenger_to_driver", "score": 4,
        "content": "再评一次"}, M1)
    record("评价-重复409", s == 409, str(s))

    # 恶意差评 fold(ride4)
    s, b = req("GET", "/api/ride/admin/pool", None, ADMIN)
    drivers = {d.get("driverId"): d for d in (b.get("drivers") or [])}
    rating_before2 = (drivers.get(driver_id) or {}).get("rating")
    s, b = req("POST", f"/api/ride/orders/{ride4}/review", {
        "direction": "passenger_to_driver", "score": 1,
        "content": "垃圾玩意, 骗子司机"}, M1)
    review = b.get("review") or {}
    record("评价-恶意差评fold", s == 200 and review.get("action")
           == "fold", str(review.get("action")))
    record("评价-fold不回写", review.get("ratingApplied") is False)
    s, b = req("GET", "/api/ride/admin/pool", None, ADMIN)
    drivers = {d.get("driverId"): d for d in (b.get("drivers") or [])}
    rating_after2 = (drivers.get(driver_id) or {}).get("rating")
    record("评价-fold评分不变", rating_after2 == rating_before2,
           f"{rating_before2} → {rating_after2}")

    # 司机评乘客(双向)
    s, b = req("POST", f"/api/ride/orders/{ride1}/review", {
        "direction": "driver_to_passenger", "score": 5,
        "content": "乘客礼貌, 目的地清晰"}, M1)
    record("评价-司机评乘客留档", s == 200
           and (b.get("review") or {}).get("direction")
           == "driver_to_passenger", f"{s} {err_msg(b)}")

    s, b = req("GET", f"/api/ride/orders/{ride1}/reviews", None, M1)
    record("评价-行程双向查询", s == 200
           and (b.get("passengerReview") or {}).get("action") == "show"
           and b.get("driverReview") is not None, f"{s}")

    s, b = req("GET", "/api/ride/driver/reviews", None, M1)
    reviews = b.get("reviews") or []
    fold_items = [r for r in reviews if r.get("action") == "fold"]
    record("评价-司机侧fold文本屏蔽", s == 200
           and fold_items and fold_items[0].get("content")
           == "(该评价已被 AI 审评折叠)", str(fold_items[:1]))

    s, b = req("GET", "/api/ride/admin/review-stats", None, ADMIN)
    record("评价-统计看板", s == 200
           and (b.get("byAction") or {}).get("fold") == 1
           and (b.get("byDirection") or {})
           .get("driver_to_passenger") == 1, str(b.get("byAction")))

    # --------------------------------------------------------
    # 13 无券拒绝
    # --------------------------------------------------------
    print("\n[13. 无券拒绝]")
    # 造 member 3(存在但无券, 不清 member 1 的券以保留对账数据)
    _r = redis.Redis(host="127.0.0.1", port=6379, db=0,
                     decode_responses=True)
    _r.hset("zhuxiang:member:3", mapping={
        "id": "3", "phone": "13800000003", "password": "x",
        "nickname": "无券测试会员", "level": "1", "growth_value": "0",
        "points": "0", "status": "1", "role": "member",
        "ageConfirmed": "1", "birthdate": "1995-05-05",
        "ageVerified": "1",
        "created_at": "2026-08-21T00:00:00+00:00"})
    s, b = req("POST", "/api/ride/call", {
        "pickup": near, "dropoff": center, "distanceKm": 8.0},
               {"X-Member-Id": "3"})
    record("无券-叫单409", s == 409 and "无可用代驾券" in err_msg(b),
           f"{s} {err_msg(b)}")

    # --------------------------------------------------------
    # 14 管理端全景
    # --------------------------------------------------------
    print("\n[14. 管理端全景]")
    s, b = req("GET", "/api/ride/admin/rides?status=settled", None,
               ADMIN)
    record("全景-settled行程过滤", s == 200 and (b.get("total") or 0)
           >= 3, str(b.get("total")))
    s, b = req("GET", "/api/ride/admin/settlements?track=platform",
               None, ADMIN)
    plat_settlements = b.get("settlements") or []
    record("全景-平台结算aggregated", s == 200
           and all(x.get("payoutStatus") == "aggregated"
                   for x in plat_settlements) and plat_settlements,
           str(len(plat_settlements)))
    s, b = req("GET", "/api/ride/admin/settlements?track=self", None,
               ADMIN)
    record("全景-自营结算paid", s == 200
           and all(x.get("payoutStatus") == "paid"
                   for x in (b.get("settlements") or [])),
           str(b.get("total")))
    s, b = req("GET", "/api/ride/admin/overview", None, ADMIN)
    record("全景-概览收口", s == 200 and b.get("poolTotal") == 9
           and b.get("applications", {}).get("approved") == 1,
           str(b.get("poolTotal")))

    # --------------------------------------------------------
    # 15 P4: 日结对账与学习回流
    # --------------------------------------------------------
    print("\n[15. P4 日结对账与学习回流]")
    from datetime import datetime as _dt, timezone as _tz
    period = _dt.now(_tz.utc).strftime("%Y-%m-%d")

    # 镜像对账(platform): 零差异 → reconciling → confirmed → paid
    s, b = req("POST", "/api/ride/admin/reconciliation/start", {
        "period": period, "track": "platform"}, ADMIN)
    record("对账-镜像生成reconciling", s == 200
           and b.get("status") == "reconciling"
           and b.get("diffCount") == 0, f"{s} {str(b)[:150]}")
    recon_no = b.get("reconNo")
    record("对账-三方总额一致",
           b.get("siteTotal") == b.get("channelTotal"),
           f"{b.get('siteTotal')} vs {b.get('channelTotal')}")
    s, b = req("POST",
               f"/api/ride/admin/reconciliation/{recon_no}/confirm",
               None, ADMIN)
    record("对账-confirm", s == 200
           and (b.get("reconciliation") or {}).get("status")
           == "confirmed", str(s))
    s, b = req("POST",
               f"/api/ride/admin/reconciliation/{recon_no}/pay",
               None, ADMIN)
    record("对账-pay终态", s == 200
           and (b.get("reconciliation") or {}).get("status") == "paid",
           str(s))

    # 差异对账(partner): 注入差异账单 → diff 分支
    s, b = req("GET", "/api/ride/admin/settlements?track=partner",
               None, ADMIN)
    partner_bills = [
        {"rideId": x.get("rideId"),
         "totalAmount": (float(x.get("totalAmount") or 0) - 3.0)}
        for x in (b.get("settlements") or [])[:2]]
    partner_bills.append({"rideId": "RD99999999", "totalAmount": 88.0})
    s, b = req("POST", "/api/ride/admin/reconciliation/start", {
        "period": period, "track": "partner",
        "channelBills": partner_bills}, ADMIN)
    recon_no2 = b.get("reconNo")
    record("对账-差异生成diff", s == 200 and b.get("status") == "diff"
           and b.get("diffCount") >= 1, f"{s} {str(b)[:150]}")
    s, b = req("POST",
               f"/api/ride/admin/reconciliation/{recon_no2}/investigate",
               None, ADMIN)
    record("对账-investigate", s == 200
           and (b.get("reconciliation") or {}).get("status")
           == "investigating", str(s))
    s, b = req("POST",
               f"/api/ride/admin/reconciliation/{recon_no2}/resolve",
               {"resolution": "平台补单核对"}, ADMIN)
    record("对账-resolve留痕", s == 200
           and "补单" in str((b.get("reconciliation") or {})
                          .get("resolution", "")), str(s))
    # 自营轨道拒绝
    s, b = req("POST", "/api/ride/admin/reconciliation/start", {
        "period": period, "track": "self"}, ADMIN)
    record("对账-自营轨道409", s == 409, str(s))
    # 列表
    s, b = req("GET", "/api/ride/admin/reconciliations?status=paid",
               None, ADMIN)
    record("对账-列表过滤paid", s == 200 and (b.get("total") or 0)
           >= 1, str(b.get("total")))

    # 学习回流: collect(派单+审查) → status → run
    s, b = req("POST", "/api/ride/admin/learning/collect", None, ADMIN)
    record("回流-批量collect", s == 200
           and (b.get("dispatch") or {}).get("submitted", 0) >= 1
           and (b.get("gate") or {}).get("submitted", 0) >= 1,
           f"{s} {str(b)[:150]}")
    s, b = req("POST", "/api/ride/admin/learning/collect", None, ADMIN)
    record("回流-幂等二轮0提交", s == 200
           and (b.get("dispatch") or {}).get("submitted") == 0
           and (b.get("gate") or {}).get("submitted") == 0, str(b)[:150])
    s, b = req("GET", "/api/ride/admin/learning/status", None, ADMIN)
    record("回流-状态视图", s == 200
           and (b.get("dispatch") or {}).get("fed", 0) >= 1
           and (b.get("gate") or {}).get("fed", 0) >= 1,
           f"{s} {str(b.get('dispatch'))[:80]}")
    record("回流-权重视图", "weights" in b
           and "ride_dispatch" in (b.get("weights") or {}),
           str((b.get("weights") or {}).keys()))
    s, b = req("POST", "/api/ride/admin/learning/run", None, ADMIN)
    record("回流-学习触发", s == 200 and "ride_dispatch"
           in (b.get("results") or {}), f"{s} {str(b)[:120]}")

    # --------------------------------------------------------
    print("\n" + "-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("-" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
