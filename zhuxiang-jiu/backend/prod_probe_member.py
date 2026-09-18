"""生产探测: member 存储 + 手机号定位(用于 strict 验证登录)"""
import json

import redis

r = redis.Redis(host="redis", port=6379, decode_responses=True)

# 通用扫描 member 相关键
for pattern in ("zhuxiang:member*", "zhuxiang:members*",
                "zhuxiang:auth*", "*member*"):
    keys = list(r.scan_iter(pattern, count=100))
    if keys:
        print(f"pattern={pattern}: {len(keys)} keys ->", keys[:8])

# 尝试 hash 形态读 member 3 / member 1
for key in ("zhuxiang:member:3", "zhuxiang:member:1",
            "zhuxiang:members", "zhuxiang:member_3"):
    try:
        t = r.type(key)
        if t == "hash":
            data = r.hgetall(key)
            phone = {k: v for k, v in data.items()
                     if "phone" in k.lower() or "role" in k.lower()}
            print(f"{key} (hash): {phone}")
        elif t != "none":
            print(f"{key} type={t}")
    except Exception as e:
        print(f"{key} err={e}")
