"""生产验证: checkout 链运费统一(满 ¥99 免 / ¥10)

三单验证: 低价含运费(50→60) / 满额免(599) / 临界免(99)。
输出订单号供清理脚本使用。
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
c = httpx.Client(base_url=BASE, timeout=60)

r = None
for host in ("redis", "127.0.0.1"):
    try:
        rc = redis.Redis(host=host, port=6379, decode_responses=True,
                         socket_timeout=5)
        rc.ping()
        r = rc
        print("redis connected:", host)
        break
    except Exception as e:
        print("redis fail:", host, repr(e)[:60])
assert r, "no redis"


def submit(price):
    resp = c.post("/api/checkout/submit", json={
        "items": [{"id": "ZX42-2026L07", "name": "竹香经典",
                   "price": price, "qty": 1}],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def points_l1():
    v = r.hget("zhuxiang:sc:checkout_points", "L1")
    return int(json.loads(v)) if v else 0


stock_before = int(r.hget("zhuxiang:inventory:ZX42-2026L07", "stock"))
pts_before = points_l1()
print(f"baseline: stock={stock_before} points_L1={pts_before}")

orders = []

# 1 低价单: 折后 50 < 99 → 运费 10 → 实付 60
d = submit(50)
print(f"[low 50 ] shipping={d['details']['shipping']} "
      f"final={d['details']['finalAmount']} (expect 10 / 60.0)")
assert d["details"]["shipping"] == 10
assert d["details"]["finalAmount"] == 60.0
orders.append(d["orderNo"])

# 2 满额单: 折后 599 >= 99 → 免运费
d = submit(599)
print(f"[high599] shipping={d['details']['shipping']} "
      f"final={d['details']['finalAmount']} (expect 0 / 599.0)")
assert d["details"]["shipping"] == 0
orders.append(d["orderNo"])

# 3 临界单: 折后 99 恰好满门槛 → 免运费
d = submit(99)
print(f"[edge99 ] shipping={d['details']['shipping']} "
      f"final={d['details']['finalAmount']} (expect 0 / 99.0)")
assert d["details"]["shipping"] == 0
assert d["details"]["finalAmount"] == 99.0
orders.append(d["orderNo"])

stock_after = int(r.hget("zhuxiang:inventory:ZX42-2026L07", "stock"))
pts_after = points_l1()
print(f"after: stock={stock_after}(delta {stock_after - stock_before}) "
      f"points_L1={pts_after}(delta {pts_after - pts_before})")

# ---- 清理: 订单/分润 LREM + 积分回补 + 库存回补 ----
removed = 0
for key in ("zhuxiang:sc:checkout_orders", "zhuxiang:sc:profit_records"):
    for item in r.lrange(key, 0, -1):
        rec = json.loads(item)
        if rec.get("order_no") in orders or rec.get("orderNo") in orders:
            r.lrem(key, 1, item)
            removed += 1
print("records removed:", removed)

r.hset("zhuxiang:sc:checkout_points", "L1",
       json.dumps(pts_before))
r.hincrby("zhuxiang:inventory:ZX42-2026L07", "stock", len(orders))
print("points restored:", points_l1() == pts_before,
      "stock restored:", int(r.hget("zhuxiang:inventory:ZX42-2026L07",
                                     "stock")) == stock_before)
print("ALL PASS & CLEANED")
