"""限时秒杀模块 HTTP 层验证(TestClient 全栈直连, 覆盖 15 个接口)

在宿主机运行(需已安装 fastapi + httpx):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_flashsale_http.py

场景主线(全部走 HTTP, 请求经过 JWT 中间件 + 全部路由层):
    参数默认值/修改即时生效 → 建场次/加商品/发布(含负向校验)
    → 公开浏览(草稿不外泄) → 抢购下单(幂等/限购/库存/防刷门槛)
    → 订单流转(支付/取消回补/取消后再购/越权拦截)
    → 超时批量取消(回补) → 场次取消联动(回补)
    → 10 并发抢 5 库存恰好成交 5(httpx.AsyncClient 真并发)
    → 全局统计对账 → JWT Bearer 免旧头访问(中间件身份注入演示)
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone, UTC

# 必须在导入 main 之前设置(内存模式 + 认证兼容模式)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")

import httpx
from fastapi.testclient import TestClient

from main import app
from repositories.member_repository import MemberRepository
from repositories.store import _mock_store, reset_store

client = TestClient(app)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


def hdr(member_id):
    return {"X-Member-Id": str(member_id)}


ADMIN = {"X-Role": "admin"}


def buy(session_id, item_id, member_id, quantity=1):
    """抢购下单快捷方法"""
    return client.post("/api/flash/order", headers=hdr(member_id),
                       json={"sessionId": session_id, "itemId": item_id,
                             "quantity": quantity})


def _mk_members() -> dict:
    """构造测试会员, 返回 {角色: 会员ID}; C 为低等级会员(等级门槛用)"""
    async def _create_all():
        repo = MemberRepository()
        mapping = {}
        roles = [("A", 3), ("B", 3), ("C", 1), ("D", 3), ("E", 3),
                 ("F", 3), ("G", 3), ("H", 3), ("I", 3)]
        roles += [(f"P{i:02d}", 3) for i in range(1, 11)]  # 并发抢购 10 人
        for idx, (key, level) in enumerate(roles, start=1):
            m = await repo.create({
                "phone": f"13966{idx:06d}", "nickname": f"秒杀测试{key}",
                "password": "x" * 64, "status": 1, "role": "member",
                "level": level, "growth_value": 600, "points": 0,
                "created_at": datetime.now(UTC).isoformat(),
            })
            mapping[key] = m["id"]
        return mapping
    return asyncio.run(_create_all())


def main():
    print("=" * 64)
    print("限时秒杀模块 HTTP 层验证(TestClient 全栈)")
    print("=" * 64)

    reset_store()
    M = _mk_members()
    now = datetime.now(UTC)
    iso = lambda dt: dt.isoformat()

    # --------------------------------------------------------
    # 1. 管理端参数配置
    # --------------------------------------------------------
    r = client.get("/api/flash/admin/settings", headers=ADMIN)
    s = r.json().get("settings", {})
    record("01_admin_read_default_settings",
           r.status_code == 200 and s.get("enabled") is True
           and s.get("minRegisterHours") == 0 and s.get("minMemberLevel") == 0
           and s.get("orderExpireMinutes") == 15
           and s.get("maxQuantityPerOrder") == 5,
           f"status={r.status_code}, settings={s}")

    r = client.get("/api/flash/admin/settings")
    record("02_admin_settings_requires_admin_role",
           r.status_code == 403, f"status={r.status_code}")

    r = client.post("/api/flash/admin/settings", headers=ADMIN,
                    json={"orderExpireMinutes": 0})
    record("03_admin_invalid_expire_minutes_rejected_422",
           r.status_code == 422,
           f"status={r.status_code}(Pydantic ge=1 拦截)")

    r = client.post("/api/flash/admin/settings", headers=ADMIN, json={})
    record("04_admin_empty_settings_rejected_409",
           r.status_code == 409 and "无可更新" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    r = client.post("/api/flash/admin/settings", headers=ADMIN,
                    json={"orderExpireMinutes": 20})
    record("05_admin_update_settings_takes_effect",
           r.status_code == 200 and r.json().get("settings", {}).get(
               "orderExpireMinutes") == 20,
           f"status={r.status_code}, body={r.json()}")

    # --------------------------------------------------------
    # 2. 管理端场次管理
    # --------------------------------------------------------
    r = client.post("/api/flash/admin/sessions", headers=ADMIN,
                    json={"name": "晚8点整点秒杀",
                          "startTime": iso(now + timedelta(hours=1)),
                          "endTime": iso(now + timedelta(hours=3))})
    S1 = r.json().get("session", {})
    record("06_create_draft_session",
           r.status_code == 200 and S1.get("sessionId", "").startswith("FS")
           and S1.get("status") == "draft",
           f"status={r.status_code}, body={r.json()}")

    r = client.post("/api/flash/admin/sessions", headers=ADMIN,
                    json={"name": "非法时间场次",
                          "startTime": iso(now + timedelta(hours=3)),
                          "endTime": iso(now + timedelta(hours=1))})
    record("07_invalid_time_range_rejected_409",
           r.status_code == 409 and "晚于" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    r = client.post("/api/flash/admin/sessions", headers=ADMIN,
                    json={"name": "空场次",
                          "startTime": iso(now + timedelta(hours=1)),
                          "endTime": iso(now + timedelta(hours=2))})
    S2 = r.json().get("session", {})
    record("08_create_second_draft_session",
           r.status_code == 200 and S2.get("status") == "draft",
           f"status={r.status_code}")

    r = client.post(f"/api/flash/admin/sessions/{S1['sessionId']}/items",
                    headers=ADMIN,
                    json={"productId": "ZX42-2026B01", "flashPrice": 88.0,
                          "flashStock": 100, "limitPerMember": 2})
    record("09_flash_price_not_below_original_rejected_409",
           r.status_code == 409 and "低于原价" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    r = client.post(f"/api/flash/admin/sessions/{S1['sessionId']}/items",
                    headers=ADMIN,
                    json={"productId": "NO-SUCH-PRODUCT", "flashPrice": 58.0,
                          "flashStock": 100, "limitPerMember": 2})
    record("10_add_item_nonexistent_product_404",
           r.status_code == 404 and "不存在" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    r = client.post(f"/api/flash/admin/sessions/{S1['sessionId']}/items",
                    headers=ADMIN,
                    json={"productId": "ZX42-2026B01", "flashPrice": 58.0,
                          "flashStock": 100, "limitPerMember": 2})
    P1 = r.json().get("item", {})
    record("11_add_item_success",
           r.status_code == 200 and P1.get("itemId", "").startswith("FI")
           and P1.get("originalPrice") == 88.0 and P1.get("flashPrice") == 58.0
           and P1.get("soldCount") == 0,
           f"status={r.status_code}, body={r.json()}")

    r = client.post(f"/api/flash/admin/sessions/{S1['sessionId']}/items",
                    headers=ADMIN,
                    json={"productId": "ZX42-2026B01", "flashPrice": 48.0,
                          "flashStock": 10, "limitPerMember": 2})
    record("12_duplicate_product_in_session_rejected_409",
           r.status_code == 409 and "重复" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    r = client.post(f"/api/flash/admin/sessions/{S2['sessionId']}/publish",
                    headers=ADMIN)
    record("13_publish_empty_session_rejected_409",
           r.status_code == 409 and "未添加" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    r = client.get("/api/flash/sessions")
    record("14_public_list_hides_draft_sessions",
           r.status_code == 200 and r.json().get("count") == 0,
           f"status={r.status_code}, body={r.json()}")

    r = client.post(f"/api/flash/admin/sessions/{S1['sessionId']}/publish",
                    headers=ADMIN)
    record("15_publish_session_success",
           r.status_code == 200 and r.json().get("session", {}).get(
               "status") == "published",
           f"status={r.status_code}, body={r.json()}")

    r = client.post(f"/api/flash/admin/sessions/{S1['sessionId']}/items",
                    headers=ADMIN,
                    json={"productId": "ZX52-2026L02", "flashPrice": 48.0,
                          "flashStock": 10, "limitPerMember": 2})
    record("16_add_item_to_published_session_rejected_409",
           r.status_code == 409 and "仅草稿" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    # --------------------------------------------------------
    # 3. 公开浏览(游客)
    # --------------------------------------------------------
    r = client.get("/api/flash/sessions")
    sessions = r.json().get("sessions", [])
    record("17_public_list_shows_published_with_runtime_status",
           r.status_code == 200 and r.json().get("count") == 1
           and sessions and sessions[0].get("sessionId") == S1["sessionId"]
           and sessions[0].get("runtimeStatus") == "not_started"
           and sessions[0].get("runtimeStatusName") == "未开始",
           f"status={r.status_code}, body={r.json()}")

    r = client.get(f"/api/flash/sessions/{S1['sessionId']}")
    items = r.json().get("session", {}).get("items", [])
    record("18_session_detail_shows_item_progress",
           r.status_code == 200 and len(items) == 1
           and items[0].get("remainingStock") == 100
           and items[0].get("progressPercent") == 0.0,
           f"status={r.status_code}, items={items}")

    r = client.get("/api/flash/sessions/FS20990101-999")
    record("19_nonexistent_session_404",
           r.status_code == 404 and "不存在" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    # --------------------------------------------------------
    # 4. 抢购下单(校验链: 开关/防刷/数量/场次状态/幂等/限购/库存)
    # --------------------------------------------------------
    r = client.post("/api/flash/order",
                    json={"sessionId": S1["sessionId"],
                          "itemId": P1["itemId"], "quantity": 1})
    record("20_purchase_requires_login_401",
           r.status_code == 401, f"status={r.status_code}, body={r.json()}")

    r = buy(S1["sessionId"], P1["itemId"], M["A"])
    record("21_purchase_not_started_session_rejected_409",
           r.status_code == 409 and "未开始" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    # 建进行中的场次 S3(主流程) + S4(库存/回补流程)
    r = client.post("/api/flash/admin/sessions", headers=ADMIN,
                    json={"name": "进行中秒杀场",
                          "startTime": iso(now - timedelta(minutes=10)),
                          "endTime": iso(now + timedelta(hours=2))})
    S3 = r.json()["session"]
    r = client.post(f"/api/flash/admin/sessions/{S3['sessionId']}/items",
                    headers=ADMIN,
                    json={"productId": "ZX42-2026B01", "flashPrice": 58.0,
                          "flashStock": 5, "limitPerMember": 2})
    P2 = r.json()["item"]
    r = client.post(f"/api/flash/admin/sessions/{S3['sessionId']}/publish",
                    headers=ADMIN)
    record("22_create_and_publish_active_session",
           r.status_code == 200 and S3["sessionId"].startswith("FS")
           and P2["itemId"].startswith("FI"),
           f"S3={S3}, P2={P2}")

    r = buy(S3["sessionId"], P2["itemId"], M["A"])
    order_a = r.json().get("order", {})
    record("23_purchase_success",
           r.status_code == 200 and order_a.get("status") == "pending_payment"
           and order_a.get("orderNo", "").startswith("FO")
           and order_a.get("unitPrice") == 58.0
           and order_a.get("totalAmount") == 58.0
           and order_a.get("memberId") == M["A"],
           f"status={r.status_code}, body={r.json()}")

    r = buy(S3["sessionId"], P2["itemId"], M["A"])
    record("24_duplicate_pending_purchase_rejected_409",
           r.status_code == 409 and "已有待支付" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    r = buy(S3["sessionId"], P2["itemId"], M["A"], quantity=0)
    record("25_zero_quantity_rejected_422",
           r.status_code == 422, f"status={r.status_code}(Pydantic ge=1 拦截)")

    r = buy(S3["sessionId"], P2["itemId"], M["G"], quantity=6)
    record("26_quantity_over_max_per_order_rejected_409",
           r.status_code == 409 and "单笔订单数量" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    r = client.post("/api/flash/admin/sessions", headers=ADMIN,
                    json={"name": "库存回补验证场",
                          "startTime": iso(now - timedelta(minutes=10)),
                          "endTime": iso(now + timedelta(hours=2))})
    S4 = r.json()["session"]
    r = client.post(f"/api/flash/admin/sessions/{S4['sessionId']}/items",
                    headers=ADMIN,
                    json={"productId": "ZX42-2026B01", "flashPrice": 48.0,
                          "flashStock": 2, "limitPerMember": 5})
    P3 = r.json()["item"]
    client.post(f"/api/flash/admin/sessions/{S4['sessionId']}/publish",
                headers=ADMIN)

    r = buy(S4["sessionId"], P3["itemId"], M["C"])
    record("27_low_level_member_purchase_success_default_no_gate",
           r.status_code == 200, f"status={r.status_code}, body={r.json()}")

    r = buy(S4["sessionId"], P3["itemId"], M["D"], quantity=2)
    record("28_insufficient_stock_rejected_409",
           r.status_code == 409 and "库存不足" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    r = buy(S3["sessionId"], P2["itemId"], M["E"], quantity=2)
    order_e = r.json().get("order", {})
    record("29_purchase_quantity_two_success",
           r.status_code == 200 and order_e.get("quantity") == 2
           and order_e.get("totalAmount") == 116.0,
           f"status={r.status_code}, body={r.json()}")

    r = client.post(f"/api/flash/orders/{order_e['orderNo']}/pay",
                    headers=hdr(M["E"]))
    record("30_pay_order_success",
           r.status_code == 200 and r.json().get("order", {}).get(
               "status") == "paid" and r.json().get("order", {}).get("paidAt"),
           f"status={r.status_code}, body={r.json()}")

    r = buy(S3["sessionId"], P2["itemId"], M["E"], quantity=1)
    record("31_purchase_over_member_limit_rejected_409",
           r.status_code == 409 and "限购" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    # 防刷门槛: 注册时长
    client.post("/api/flash/admin/settings", headers=ADMIN,
                json={"minRegisterHours": 1})
    r = buy(S3["sessionId"], P2["itemId"], M["F"])
    record("32_fresh_member_blocked_by_register_hours_409",
           r.status_code == 409 and "注册满" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")
    client.post("/api/flash/admin/settings", headers=ADMIN,
                json={"minRegisterHours": 0})

    # 防刷门槛: 会员等级(C=L1)
    client.post("/api/flash/admin/settings", headers=ADMIN,
                json={"minMemberLevel": 3})
    r = buy(S3["sessionId"], P2["itemId"], M["C"])
    record("33_low_level_member_blocked_by_level_gate_409",
           r.status_code == 409 and "会员等级" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")
    client.post("/api/flash/admin/settings", headers=ADMIN,
                json={"minMemberLevel": 0})

    # 总开关
    client.post("/api/flash/admin/settings", headers=ADMIN,
                json={"enabled": False})
    r = buy(S3["sessionId"], P2["itemId"], M["G"])
    record("34_purchase_blocked_when_disabled_409",
           r.status_code == 409 and "暂未开启" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")
    client.post("/api/flash/admin/settings", headers=ADMIN,
                json={"enabled": True})

    # --------------------------------------------------------
    # 5. 订单流转(查询/越权/支付/取消回补/取消后再购)
    # --------------------------------------------------------
    r = client.get("/api/flash/my/orders", headers=hdr(M["A"]))
    orders = r.json().get("orders", [])
    record("35_my_orders_contains_own_order",
           r.status_code == 200 and r.json().get("count") == 1
           and orders and orders[0].get("orderNo") == order_a["orderNo"],
           f"status={r.status_code}, body={r.json()}")

    r = client.get(f"/api/flash/orders/{order_a['orderNo']}",
                   headers=hdr(M["A"]))
    record("36_get_own_order_success",
           r.status_code == 200 and r.json().get("order", {}).get(
               "status") == "pending_payment",
           f"status={r.status_code}")

    r = client.get(f"/api/flash/orders/{order_a['orderNo']}",
                   headers=hdr(M["B"]))
    record("37_view_others_order_rejected_409",
           r.status_code == 409 and "无权" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    r = client.get(f"/api/flash/orders/{order_a['orderNo']}", headers=ADMIN)
    record("38_admin_view_any_order_success",
           r.status_code == 200, f"status={r.status_code}")

    r = client.post(f"/api/flash/orders/{order_a['orderNo']}/pay",
                    headers=hdr(M["A"]))
    record("39_pay_own_order_success",
           r.status_code == 200 and r.json().get("order", {}).get(
               "status") == "paid" and r.json().get("order", {}).get("paidAt"),
           f"status={r.status_code}, body={r.json()}")

    r = client.post(f"/api/flash/orders/{order_a['orderNo']}/pay",
                    headers=hdr(M["A"]))
    record("40_pay_paid_order_rejected_409",
           r.status_code == 409 and "不可支付" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    r = client.post(f"/api/flash/orders/{order_a['orderNo']}/cancel",
                    headers=hdr(M["A"]))
    record("41_cancel_paid_order_rejected_409",
           r.status_code == 409 and "仅待支付" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    # D 下单 → 取消回补 → 再购(取消不计入幂等/限购)
    r = buy(S4["sessionId"], P3["itemId"], M["D"])
    order_d = r.json()["order"]
    r = client.get(f"/api/flash/sessions/{S4['sessionId']}")
    record("42_stock_exhausted_after_purchase",
           r.status_code == 200 and r.json().get("session", {}).get(
               "items", [{}])[0].get("remainingStock") == 0
           and r.json().get("session", {}).get(
               "items", [{}])[0].get("progressPercent") == 100.0,
           f"status={r.status_code}, body={r.json()}")

    r = client.post(f"/api/flash/orders/{order_d['orderNo']}/cancel",
                    headers=hdr(M["D"]))
    record("43_cancel_pending_order_success",
           r.status_code == 200 and r.json().get("order", {}).get(
               "status") == "cancelled"
           and "主动取消" in r.json().get("order", {}).get("cancelReason", ""),
           f"status={r.status_code}, body={r.json()}")

    r = client.get(f"/api/flash/sessions/{S4['sessionId']}")
    record("44_cancel_restores_stock",
           r.status_code == 200 and r.json().get("session", {}).get(
               "items", [{}])[0].get("remainingStock") == 1,
           f"status={r.status_code}, body={r.json()}")

    r = buy(S4["sessionId"], P3["itemId"], M["D"])
    order_d2 = r.json().get("order", {})
    record("45_repurchase_after_cancel_success",
           r.status_code == 200 and order_d2.get("status") == "pending_payment"
           and order_d2.get("orderNo") != order_d["orderNo"],
           f"status={r.status_code}, body={r.json()}")

    r = client.get("/api/flash/orders/FO20990101-999", headers=hdr(M["A"]))
    record("46_nonexistent_order_404",
           r.status_code == 404 and "不存在" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    # --------------------------------------------------------
    # 6. 超时未支付批量取消(回补库存)
    # --------------------------------------------------------
    # 直接将 D 的再购订单创建时间回拨 30 分钟(超过超时阈值 20 分钟)
    backdate = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    _mock_store["flash_orders"][order_d2["orderNo"]]["createdAt"] = backdate

    r = client.post("/api/flash/admin/orders/expire-cancel", headers=ADMIN)
    body = r.json()
    record("47_expire_cancel_backdated_order",
           r.status_code == 200 and body.get("cancelledCount") == 1
           and order_d2["orderNo"] in body.get("orderNos", [])
           and body.get("expireMinutes") == 20,
           f"status={r.status_code}, body={body}")

    r = client.get(f"/api/flash/sessions/{S4['sessionId']}")
    record("48_expire_cancel_restores_stock",
           r.status_code == 200 and r.json().get("session", {}).get(
               "items", [{}])[0].get("remainingStock") == 1,
           f"status={r.status_code}, body={r.json()}")

    r = client.post("/api/flash/admin/orders/expire-cancel", headers=ADMIN)
    record("49_expire_cancel_idempotent_no_recent_orders",
           r.status_code == 200 and r.json().get("cancelledCount") == 0,
           f"status={r.status_code}, body={r.json()}")

    # --------------------------------------------------------
    # 7. 场次取消联动(待支付订单取消 + 库存回补)
    # --------------------------------------------------------
    r = buy(S3["sessionId"], P2["itemId"], M["H"])
    order_h = r.json().get("order", {})
    record("50_member_h_purchase_for_session_cancel",
           r.status_code == 200 and order_h.get("status") == "pending_payment",
           f"status={r.status_code}, body={r.json()}")

    r = client.post(f"/api/flash/admin/sessions/{S3['sessionId']}/cancel",
                    headers=ADMIN)
    body = r.json().get("session", {})
    record("51_cancel_session_cascades_pending_orders",
           r.status_code == 200 and body.get("status") == "cancelled"
           and body.get("cancelledOrders") == 1,
           f"status={r.status_code}, body={r.json()}")

    r = client.get(f"/api/flash/sessions/{S3['sessionId']}")
    record("52_session_cancel_restores_stock",
           r.status_code == 200 and r.json().get("session", {}).get(
               "items", [{}])[0].get("remainingStock") == 2,
           f"status={r.status_code}, body={r.json()}")

    r = client.post(f"/api/flash/admin/sessions/{S3['sessionId']}/cancel",
                    headers=ADMIN)
    record("53_cancel_already_cancelled_session_rejected_409",
           r.status_code == 409 and "已是取消" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    r = buy(S3["sessionId"], P2["itemId"], M["I"])
    record("54_purchase_cancelled_session_rejected_409",
           r.status_code == 409 and "不可下单" in r.json().get("error", ""),
           f"status={r.status_code}, body={r.json()}")

    # --------------------------------------------------------
    # 8. 并发抢购: 10 人并发抢 5 库存, 恰好成交 5
    #    (httpx.AsyncClient + ASGITransport, 同一事件循环真并发)
    # --------------------------------------------------------
    r = client.post("/api/flash/admin/sessions", headers=ADMIN,
                    json={"name": "并发压测场",
                          "startTime": iso(now - timedelta(minutes=10)),
                          "endTime": iso(now + timedelta(hours=2))})
    S5 = r.json()["session"]
    r = client.post(f"/api/flash/admin/sessions/{S5['sessionId']}/items",
                    headers=ADMIN,
                    json={"productId": "ZX42-2026B01", "flashPrice": 58.0,
                          "flashStock": 5, "limitPerMember": 1})
    P4 = r.json()["item"]
    client.post(f"/api/flash/admin/sessions/{S5['sessionId']}/publish",
                headers=ADMIN)

    buyers = [M[f"P{i:02d}"] for i in range(1, 11)]

    def _concurrent_buy():
        async def _run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://testserver") as ac:
                async def _buy(mid):
                    resp = await ac.post(
                        "/api/flash/order", headers={"X-Member-Id": str(mid)},
                        json={"sessionId": S5["sessionId"],
                              "itemId": P4["itemId"], "quantity": 1})
                    return resp.status_code
                return await asyncio.gather(*[_buy(m) for m in buyers])
        return asyncio.run(_run())

    codes = _concurrent_buy()
    r = client.get(f"/api/flash/sessions/{S5['sessionId']}")
    item = r.json().get("session", {}).get("items", [{}])[0]
    record("55_concurrent_10_buy_5_stock_exactly_5_win",
           codes.count(200) == 5 and codes.count(409) == 5
           and item.get("soldCount") == 5 and item.get("remainingStock") == 0
           and item.get("progressPercent") == 100.0,
           f"codes={sorted(codes)}, item={item}")

    # --------------------------------------------------------
    # 9. 全局统计对账
    # --------------------------------------------------------
    r = client.get("/api/flash/admin/stats", headers=ADMIN)
    stats = r.json().get("stats", {})
    by_sid = {row.get("sessionId"): row for row in stats.get("sessions", [])}
    s3_row, s4_row, s5_row = (by_sid.get(S3["sessionId"], {}),
                              by_sid.get(S4["sessionId"], {}),
                              by_sid.get(S5["sessionId"], {}))
    record("56_admin_stats_reconciles_all_sessions",
           r.status_code == 200 and stats.get("sessionCount") == 5
           and abs(stats.get("paidAmount", 0) - 174.0) < 0.01
           and s3_row.get("paidCount") == 2
           and s3_row.get("cancelledCount") == 1
           and s3_row.get("orderCount") == 3
           and s4_row.get("pendingCount") == 1
           and s4_row.get("cancelledCount") == 2
           and s5_row.get("pendingCount") == 5
           and s5_row.get("soldCount") == 5,
           f"status={r.status_code}, stats={stats}")

    r = client.get("/api/flash/admin/stats")
    record("57_admin_stats_requires_admin_role",
           r.status_code == 403, f"status={r.status_code}")

    # --------------------------------------------------------
    # 10. JWT Bearer 免旧头访问(中间件身份注入演示)
    # --------------------------------------------------------
    r = client.post("/api/auth/login",
                    json={"phone": "13800000001", "password": "test123456"})
    token = r.json().get("accessToken", "")
    record("58_jwt_login_for_seed_member",
           r.status_code == 200 and bool(token),
           f"status={r.status_code}")

    r = client.get("/api/flash/my/orders",
                   headers={"Authorization": f"Bearer {token}"})
    record("59_jwt_bearer_injects_identity",
           r.status_code == 200 and r.json().get("count") == 0,
           f"status={r.status_code}, body={r.json()} "
           f"(无 X-Member-Id 头, 身份来自 JWT, 种子会员无秒杀订单)")

    # --------------------------------------------------------
    # 汇总
    # --------------------------------------------------------
    print()
    for line in RESULTS:
        print(line)
    print()
    print("=" * 64)
    print(f"总计: {PASS + FAIL}  通过: {PASS}  失败: {FAIL}")
    print("=" * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
