"""生产清理: 订单评价同步 E2E 测试数据(RT202609172323106121)

清理项:
    - 商品评价列表 LREM(按 order_id 匹配)
    - 产品评分恢复基线(4.8/320)
    - 订单四键(order/user index/orders:index/invoice42)
    - 库存回补(+1)
"""
import json

import redis

oid = "RT202609172323106121"
PID = "ZX42-2026L07"

r = None
for host in ("redis", "127.0.0.1"):
    try:
        c = redis.Redis(host=host, port=6379, decode_responses=True,
                        socket_timeout=5)
        c.ping()
        r = c
        print("connected:", host)
        break
    except Exception as e:
        print("fail:", host, repr(e)[:80])
assert r, "no redis reachable"

# 1 评价移除(按 order_id 匹配)
key = f"zhuxiang:product:reviews:{PID}"
removed = 0
for item in r.lrange(key, 0, -1):
    d = json.loads(item)
    if d.get("order_id") == oid:
        r.lrem(key, 1, item)
        removed += 1
        print("removed review:", d.get("review_id"))
print("reviews removed:", removed)

# 2 评分恢复基线(E2E 前记录值)
r.hset(f"zhuxiang:product:{PID}", mapping={
    "rating_avg": 4.8, "rating_count": 320})
print("rating restored: 4.8/320")

# 3 订单键清理
r.delete(f"zhuxiang:order:{oid}")
r.srem("zhuxiang:order:user:1", oid)
r.lrem("zhuxiang:orders:index", 1, oid)
r.delete(f"zhuxiang:invoice42:invoice_decisions:{oid}")
print("order keys cleaned")

# 4 库存回补(下单预扣 1 件)
r.hincrby(f"zhuxiang:inventory:{PID}", "stock", 1)
print("stock +1")

print("CLEANUP DONE")
