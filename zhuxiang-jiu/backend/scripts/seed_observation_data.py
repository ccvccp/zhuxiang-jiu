"""观测面数据激活播种脚本(幂等 v1)

目的: 激活因演示数据字段不全而休眠的观测面——
  - 智单: 履约 ETA / 体检履约维 / 日单量预测(需 paidAt→signedAt 全生命周期样本)
  - 智酿运通: 碳足迹(需 weight>0 的物流订单)
  - 智客: RFM 分层 / 流失三信号(需多会员跨期消费分布)

动作:
  A. 电商回溯订单 ×8(ZD-SEED 前缀, 直接仓储写入, 时间回溯 2-46 天,
     COMPLETED/RECEIVED/SHIPPED/CANCELLED 四态, 履约时长 42-88h 分布)
  B. 物流正式 API 订单 ×3(WT-CARBON 前缀, weight/province/city 完整,
     经 POST /api/logistics/order 强制 weight>0, 推进至 signed)
  C. 电商正式 API 全链 ×1(create→pay→ship→confirm→review,
     真实状态机 + 44号 hooks + 42号开票 best-effort)

宪法口径: 全部演示数据带 SEED 前缀清晰留痕; A 类为仓储直写(不触发
hooks, 对齐 ZY-DEMO 播种先例); B/C 类走正式接口(真实管线)。
幂等: Redis 标志 zhuxiang:seed:obs:v1(内存模式 _mock_store 同位标志)。

运行:
  本地演练: STORE_MODE=asyncio LOCK_MODE=asyncio AUTH_MODE=compat \
            SEED_BASE_URL=http://127.0.0.1:8077 python scripts/seed_observation_data.py
  生产容器: docker exec zhuxiang-backend-1 python scripts/seed_observation_data.py
"""

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, UTC
from pathlib import Path

# backend 目录入 path(脚本位于 scripts/ 子目录)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ------------------------------------------------------------------
# 基础设施
# ------------------------------------------------------------------

BASE_URL = os.environ.get("SEED_BASE_URL", "http://127.0.0.1:8000")
ADMIN_H = {"X-Role": "admin"}
MEMBER_H = {"X-Member-Id": "3"}
FLAG_KEY = "zhuxiang:seed:obs:v1"
MEM_FLAG = "seed_obs_v1"

P_MAIN = ("ZX42-2026L07", "竹奕·竹香型 42° 500ml", 268.0)
P_GIFT = ("ZX42-2026L05", "竹奕·竹香型 42° 500ml 礼盒", 168.0)

ADDRESS = {"name": "演示收货人", "phone": "13800009999",
           "province": "四川", "city": "成都",
           "district": "锦江区", "detail": "演示路 1 号"}


def _req(method: str, path: str, headers: dict = None,
         body: dict = None):
    """极简 HTTP(生产容器内自环 127.0.0.1:8000)"""
    url = BASE_URL.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except (ValueError, OSError):
            return e.code, {}


def _now() -> datetime:
    return datetime.now(UTC)


def _item(product: tuple, qty: int) -> dict:
    pid, name, price = product
    return {"productId": pid, "productName": name, "quantity": qty,
            "unitPrice": price, "subtotal": round(qty * price, 2)}


# ------------------------------------------------------------------
# 幂等标志(双模式)
# ------------------------------------------------------------------

async def _flag_set() -> bool:
    if os.environ.get("STORE_MODE") == "asyncio":
        from repositories.store import _mock_store
        return bool(_mock_store.get(MEM_FLAG))
    from repositories.backend import get_redis_client
    client = await get_redis_client()
    return bool(await client.get(FLAG_KEY))


async def _flag_mark():
    if os.environ.get("STORE_MODE") == "asyncio":
        from repositories.store import _mock_store
        _mock_store[MEM_FLAG] = True
        return
    from repositories.backend import get_redis_client
    client = await get_redis_client()
    await client.set(FLAG_KEY, _now().isoformat())


# ------------------------------------------------------------------
# A. 电商回溯订单(仓储直写, ZD-SEED 前缀)
# ------------------------------------------------------------------

# (序号, 会员, 创建距今天数, 终态, 承运商, 履约小时 or None, 商品, 数量)
SEED_ORDERS = [
    (1, 2, 46, "COMPLETED", "SF", 78, P_MAIN, 2),
    (2, 2, 40, "COMPLETED", "YT", 55, P_GIFT, 1),
    (3, 1, 9,  "RECEIVED",  "DB", 74, P_MAIN, 1),
    (4, 1, 6,  "COMPLETED", "JD", 88, P_GIFT, 12),   # 囤货画像 demo
    (5, 3, 5,  "COMPLETED", "SF", 42, P_MAIN, 2),
    (6, 1, 3,  "COMPLETED", "DB", 68, P_MAIN, 4),
    (7, 3, 2,  "SHIPPED",   "SF", None, P_GIFT, 2),
    (8, 3, 4,  "CANCELLED", "", None, P_MAIN, 1),
]

REVIEW_TEXTS = ["竹香醇厚, 回购", "包装精美, 送人体面",
                "口感清爽, 家宴首选", "物流很快, 酒香正"]


async def seed_backdated() -> None:
    from repositories.order_repository import OrderRepository
    repo = OrderRepository()
    existing = await repo.list_all()
    if any(str(o.get("orderId", "")).startswith("ZD-SEED-") for o in existing):
        print("[A] ZD-SEED 前缀已存在, 跳过(幂等)")
        return

    now = _now()
    for (seq, member, days_ago, final, carrier, dur_h, product,
         qty) in SEED_ORDERS:
        oid = f"ZD-SEED-{seq:03d}"
        created = now - timedelta(days=days_ago)
        paid = created + timedelta(minutes=10)
        items = [_item(product, qty)]
        goods = round(sum(i["subtotal"] for i in items), 2)
        order = {
            "orderId": oid, "memberId": member, "orderType": "RT",
            "status": final, "items": items,
            "priceDetail": {"goodsTotal": goods, "memberDiscount": 0.0,
                            "couponDiscount": 0.0, "pointsDiscount": 0.0,
                            "shippingFee": 0.0, "actualAmount": goods,
                            "discountRate": 1.0},
            "address": dict(ADDRESS), "remark": "观测面激活演示数据",
            "logistics": {}, "payment": {}, "review": {},
            "usedPoints": 0, "consumedPoints": 0, "timeline": [],
            "createdAt": created.isoformat(), "updatedAt": created.isoformat(),
        }
        if final == "CANCELLED":
            order["timeline"] = [
                {"status": "PENDING", "time": created.isoformat(),
                 "action": "订单创建"},
                {"status": "CANCELLED",
                 "time": (created + timedelta(minutes=30)).isoformat(),
                 "action": "取消: 演示数据"}]
            order["updatedAt"] = (created + timedelta(minutes=30)).isoformat()
        else:
            shipped = paid + timedelta(hours=2 + seq % 3)
            order["payment"] = {"method": "wechat",
                                "tradeNo": f"SEEDPAY{seq:03d}",
                                "paidAt": paid.isoformat()}
            if final in ("SHIPPED", "RECEIVED", "COMPLETED"):
                order["logistics"] = {
                    "carrier": carrier, "waybillNo": f"{carrier}7700{seq}",
                    "shippedAt": shipped.isoformat(), "signedAt": ""}
                order["timeline"] = [
                    {"status": "PENDING", "time": created.isoformat(),
                     "action": "订单创建"},
                    {"status": "PAID", "time": paid.isoformat(),
                     "action": "支付成功(wechat)"},
                    {"status": "SHIPPED", "time": shipped.isoformat(),
                     "action": f"已发货 {carrier}"}]
                if final in ("RECEIVED", "COMPLETED"):
                    signed = paid + timedelta(hours=dur_h)
                    order["logistics"]["signedAt"] = signed.isoformat()
                    order["timeline"].append(
                        {"status": "RECEIVED", "time": signed.isoformat(),
                         "action": "确认收货"})
                    order["updatedAt"] = signed.isoformat()
                    if final == "COMPLETED":
                        reviewed = signed + timedelta(hours=2)
                        order["review"] = {
                            "rating": 4 + seq % 2,
                            "content": REVIEW_TEXTS[seq % len(REVIEW_TEXTS)],
                            "reviewedAt": reviewed.isoformat()}
                        order["timeline"].append(
                            {"status": "COMPLETED",
                             "time": reviewed.isoformat(),
                             "action": "评价完成"})
                        order["updatedAt"] = reviewed.isoformat()
        await repo.create(order)
        print(f"[A] {oid} member={member} status={final} "
              f"dur={dur_h}h goods=¥{goods}")
    print("[A] 回溯订单播种完成(8 笔)")


# ------------------------------------------------------------------
# B. 物流正式 API 订单(weight 完整, 推进至 signed)
# ------------------------------------------------------------------

SENDER = {"name": "竹香酒仓", "phone": "02812345678",
          "address": "四川省成都市锦江区酒厂路 1 号"}
LOGI_ORDERS = [
    ("WT-CARBON-001", "DB",
     {"name": "王五", "phone": "13800005555", "address": "锦江区 2 号",
      "province": "四川", "city": "成都"}, 12.0, 3),
    ("WT-CARBON-002", "SF",
     {"name": "赵六", "phone": "13800004444",
      "address": "南山区科技园 1 号", "province": "广东", "city": "深圳"},
     45.0, 6),
    ("WT-CARBON-003", "YT",
     {"name": "李四", "phone": "13800006666", "address": "城关区 1 号",
      "province": "西藏", "city": "拉萨"}, 8.0, 2),
]


def seed_logistics() -> None:
    st, resp = _req("GET", "/api/logistics-ai/status",
                   headers={**ADMIN_H, "X-Admin-Id": "3"})
    print(f"[B] 物流模块状态: {st}")
    waybills = []  # (幂等前缀检查在 main 中完成)
    for (oid, carrier, receiver, weight, pieces) in LOGI_ORDERS:
        st, resp = _req("POST", "/api/logistics/order", headers=MEMBER_H,
                        body={"orderId": oid, "orderType": "retail",
                              "carrier": carrier, "sender": SENDER,
                              "receiver": receiver, "weight": weight,
                              "pieceCount": pieces, "insuredValue": 0})
        if st != 200:
            print(f"[B] {oid} 创建失败: {st} {str(resp)[:160]}")
            continue
        wb = (resp.get("data") or {}).get("waybillNo", "")
        for step in ("booked", "picked", "transporting", "delivering",
                     "signed"):
            st2, _ = _req("POST", f"/api/logistics/order/{wb}/status",
                          headers=ADMIN_H,
                          body={"status": step, "operator": "seed"})
            if st2 != 200:
                print(f"[B] {oid}→{step} 失败: {st2}")
                break
        waybills.append((oid, wb, carrier, weight))
        print(f"[B] {oid} waybill={wb} carrier={carrier} "
              f"weight={weight}kg → signed")
    print(f"[B] 物流订单完成({len(waybills)}/3)")


# ------------------------------------------------------------------
# C. 电商正式 API 全链(真实管线 + hooks)
# ------------------------------------------------------------------


def _pick_product():
    st, resp = _req("GET", "/api/product/list?page=1&pageSize=20")
    data = (resp or {}).get("data")
    items = []
    if isinstance(data, dict):
        items = (data.get("products") or data.get("list")
                 or data.get("items") or [])
    elif isinstance(data, list):
        items = data
    for p in items:
        pid = p.get("productId") or p.get("id")
        price = float(p.get("price") or p.get("salePrice") or 0)
        stock = p.get("stock", p.get("stockLevel"))
        name = p.get("productName") or p.get("name") or ""
        if pid and price > 0 and (stock is None or int(stock) > 0):
            return str(pid), str(name), price
    return P_MAIN  # 兜底(演示常量)


def seed_formal_ecommerce() -> str:
    pid, name, price = _pick_product()
    # 会员回退链(生产 1-5 均存在; 本地演练可能仅有注册会员)
    order_id = ""
    h = MEMBER_H
    for mid in ("3", "1", "2"):
        h = {"X-Member-Id": mid}
        st, resp = _req("POST", "/api/order/create", headers=h,
                        body={"items": [{"productId": pid,
                                         "productName": name,
                                         "quantity": 1,
                                         "unitPrice": price}],
                              "address": ADDRESS, "usePoints": 0,
                              "remark": "观测面激活·正式管线验证",
                              "ageConfirmed": True})
        if st == 200:
            order_id = (resp.get("details") or {}).get("orderId") \
                or resp.get("orderId", "")
            print(f"[C] 会员 {mid} 创建 {order_id} ({name} ¥{price})")
            break
        print(f"[C] 会员 {mid} 创建失败: {st} {str(resp)[:120]}")
    if not order_id:
        print("[C] 无可用会员, 跳过正式管线验证")
        return ""
    ok = True
    st, _ = _req("POST", f"/api/order/{order_id}/pay", headers=h,
                 body={"method": "wechat"})
    ok &= st == 200
    print(f"[C] pay: {st}")
    st, _ = _req("POST", f"/api/order/{order_id}/ship", headers=ADMIN_H,
                 body={"carrier": "SF", "waybillNo": "SF9000SEED"})
    ok &= st == 200
    print(f"[C] ship: {st}")
    st, _ = _req("POST", f"/api/order/{order_id}/confirm", headers=h)
    ok &= st == 200
    print(f"[C] confirm: {st}")
    st, _ = _req("POST", f"/api/order/{order_id}/review", headers=h,
                 body={"rating": 5, "content": "竹香醇厚, 正式管线验证好评"})
    ok &= st == 200
    print(f"[C] review: {st} → {'全链 OK' if ok else '存在失败步骤'}")
    return order_id


# ------------------------------------------------------------------
# D. 观测面激活验证
# ------------------------------------------------------------------


def verify():
    checks = []

    st, r = _req("GET", "/api/order-ai/eta", headers=ADMIN_H)
    eta = ((r.get("data") or {}).get("etaHours"))
    checks.append(("智单-履约ETA激活", eta is not None, f"etaHours={eta}"))

    st, r = _req("GET", "/api/order-ai/checkup", headers=ADMIN_H)
    dims = (r.get("data") or {}).get("dimensions", [])
    fulfill = next((d for d in dims if "履约" in str(d.get("dim", ""))), {})
    checks.append(("智单-体检履约维打分", float(fulfill.get("score", 0)) > 0,
                   f"score={fulfill.get('score')}"))

    st, r = _req("GET", "/api/order-ai/forecast", headers=ADMIN_H)
    data = r.get("data") or {}
    rows = len(data.get("rows", []))
    hist_days = (data.get("basis") or {}).get("historyDays", 0)
    checks.append(("智单-预测序列激活", rows >= 10 and hist_days >= 5,
                   f"rows={rows} historyDays={hist_days}"))

    st, r = _req("GET", "/api/logistics-ai/evolution2/carbon",
                 headers=ADMIN_H)
    kg = float((r.get("data") or {}).get("totalCarbonKg", 0))
    checks.append(("智酿-碳排激活", kg > 0, f"totalKg={kg}"))

    st, r = _req("POST", "/api/order-ai/anomaly-scan", headers=ADMIN_H)
    found = len((r.get("data") or {}).get("anomalies", []))
    checks.append(("智单-异常扫描(囤货画像)", found >= 1,
                   f"anomalyCount={found}"))
    st, r = _req("GET", "/api/order-ai/anomalies", headers=ADMIN_H)
    anom = len(r.get("data") or [])
    checks.append(("智单-异常留痕", anom >= 1, f"anomalies={anom}"))

    st, r = _req("GET", "/api/member-ai/churn-scan", headers=ADMIN_H)
    data = r.get("data") or {}
    items = data.get("items", [])
    red_yellow = sum(1 for it in items
                     if it.get("riskLevel") in ("red", "yellow"))
    checks.append(("智客-流失预警激活", red_yellow >= 1,
                    f"riskCounts={data.get('riskCounts')}"))

    print("\n===== 观测面激活验证 =====")
    passed = 0
    for name, okv, detail in checks:
        mark = "PASS" if okv else "WARN"
        passed += 1 if okv else 0
        print(f"  [{mark}] {name}: {detail}")
    print(f"===== {passed}/{len(checks)} 通过 =====")


# ------------------------------------------------------------------
# D2. 服务级验证(同进程; 内存模式下与 HTTP 服务进程隔离,
#     生产 Redis 模式下与 HTTP 等价——同库)
# ------------------------------------------------------------------


async def verify_services():
    """进程内直读织物/服务, 验证播种数据确实激活观测面"""
    from services.zd_fabric_service import ZdFabricService
    from services.zd_forecast_service import ZdForecastService
    fabric = ZdFabricService()
    ov = await fabric.overview()
    eta = await ZdForecastService(fabric=fabric).eta_forecast()
    samples = ov.get("fulfillmentSamples")
    eta_h = eta.get("etaHours")
    days = len(ov.get("dailySeries", []))
    print("\n===== 服务级激活验证(进程内) =====")
    print(f"  履约样本={samples} ETA={eta_h}h 日序列={days}天 "
          f"GMV=¥{ov.get('gmv')}")
    ok = samples and samples >= 6 and eta_h and days >= 5
    print(f"  [{'PASS' if ok else 'WARN'}] "
          f"样本>=6 且 ETA 非空 且 日序列>=5")
    return bool(ok)


async def _logi_prefix_exists() -> bool:
    from repositories.logistics_repository import LogisticsRepository
    rows = await LogisticsRepository().list_orders(limit=500)
    return any(str(o.get("orderId", "")).startswith("WT-CARBON-")
               for o in rows)


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------


async def main():
    if await _flag_set():
        print("幂等标志已存在(seed:obs:v1), 本次跳过全部播种。")
        verify()
        return
    await seed_backdated()
    if await _logi_prefix_exists():
        print("[B] WT-CARBON 前缀已存在, 跳过(幂等)")
    else:
        seed_logistics()
    seed_formal_ecommerce()
    await _flag_mark()
    print("幂等标志已写入:", FLAG_KEY)
    await verify_services()
    verify()


if __name__ == "__main__":
    asyncio.run(main())
