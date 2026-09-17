"""生产探查: E2E 两个观察项事实采集

1. 列表/详情 stock 口径: 下单前后对比两 API 的 stock(同商品同刻)
2. 运费规则: 88 元商品(满99免运费门槛下)下单运费明细实证
"""
import httpx

BASE = "http://127.0.0.1:8000"
H = {"X-Member-Id": "1"}
A = {"X-Role": "admin"}
PID = "ZX42-2026B01"  # ¥88 < 99 门槛

c = httpx.Client(base_url=BASE, timeout=60)


def stock_of(pid):
    d = c.get(f"/api/product/{pid}").json()["product"]
    lst = c.get("/api/product/list", params={"page": 1, "pageSize": 50}).json()
    p = [x for x in lst["products"] if x["product_id"] == pid][0]
    return {"detail_stock": d["stock"], "detail_reserved": d.get("reserved"),
            "list_stock": p["stock"]}


print("== 基线(下单前) ==")
print("price:", c.get(f"/api/product/{PID}").json()["product"]["price"])
print(stock_of(PID))

print("== 试算(88元x1, L5会员) ==")
r = c.post("/api/order/price/preview", json={
    "items": [{"productId": PID, "productName": "竹奕·竹香便携 42° 250ml",
               "quantity": 1, "unitPrice": 88.00}],
    "usePoints": 0}, headers=H)
print("preview:", r.status_code)
pd = r.json()["priceDetail"]
print(f"  goodsTotal={pd['goodsTotal']} memberDiscount={pd['memberDiscount']}"
      f" shippingFee={pd['shippingFee']} actualAmount={pd['actualAmount']}"
      f" discountRate={pd['discountRate']}")

print("== 下单(88元x1) ==")
r = c.post("/api/order/create", json={
    "items": [{"productId": PID, "productName": "竹奕·竹香便携 42° 250ml",
               "quantity": 1, "unitPrice": 88.00}],
    "address": {"name": "张三", "phone": "13800000001", "province": "山东省",
                 "city": "泰安市", "district": "泰山区", "detail": "竹香路1号"},
    "usePoints": 0, "remark": "观察项采集", "ageConfirmed": True}, headers=H)
print("create:", r.status_code)
oid = r.json()["orderId"]
pd = r.json()["priceDetail"]
print(f"  orderId={oid} shippingFee={pd['shippingFee']}"
      f" actualAmount={pd['actualAmount']}")

print("== 下单后(库存口径对比) ==")
print(stock_of(PID))

print("ORDER_ID_FOR_CLEANUP:", oid)
