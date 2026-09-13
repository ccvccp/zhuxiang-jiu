"""71号 P8 Redis 模式红队验证(生产缺陷复现——idempotentKey 序列化)

本地 asyncio 模式无法复现(存取同形); 本脚本以 Redis 后端直连
复现红队 RT-02A 并断言修复(需服务器 Redis 可达或本地 Redis)。

运行(服务器容器内): python test_pay71_p8_redis.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "redis"
os.environ["STORE_MODE"] = "redis"
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ["PAY71_MODE"] = "shadow"
os.environ.pop("PAY71_KILL", None)
os.environ.pop("PAY71_IMMUNITY", None)

PASS = 0
FAIL = 0


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} — {detail}")


async def main():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}

    r = client.post("/api/pay71/immunity/redteam",
                    headers=ADMIN)
    body = r.json()
    record("Redis 模式红队执行 200",
           r.status_code == 200, str(r.status_code))
    rt02 = next(v for v in body["vectors"]
                if v["vector"] == "RT-02")
    record("RT-02 重放洪泛全防御"
           "(Redis 序列化修复后)",
           rt02["defended"] is True,
           str([(a["attack"],
                 a["defended"])
                for a in rt02["attacks"]]))
    record("allDefended=4/4",
           body["allDefended"] is True,
           body.get("summary", ""))
    print("-" * 50)
    print(f"Redis 验证: {PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
