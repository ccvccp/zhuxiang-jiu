"""生产清理: 观察项采集测试单 RT202609180049421285

取消回补库存 + 删除订单键(含用户索引/列表索引)。
"""
import redis

oid = "RT202609180049421285"
PID = "ZX42-2026B01"

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
assert r, "no redis"

# 订单四键(未支付, 无 invoice42)
r.delete(f"zhuxiang:order:{oid}")
r.srem("zhuxiang:order:user:1", oid)
r.lrem("zhuxiang:orders:index", 1, oid)
print("order keys cleaned")

# 库存回补(下单预扣 1 件)
r.hincrby(f"zhuxiang:inventory:{PID}", "stock", 1)
print("stock +1, now:", r.hget(f"zhuxiang:inventory:{PID}", "stock"))
print("CLEANUP DONE")
