"""钱包 Redis 态开通/读取加固 实机部署验收脚本(Redis 模式容器)

覆盖(e1f2cda 加固口径, S1-S5 场景):
    S0 鉴权: 无 X-Member-Id 头 → 401
    S1 全新开通: 单条 HSET 原子写入(键类型 hash/无占位字段/索引入册)
    S2 重复开通: 409 拒绝且真实数据(余额)不被重置
    S3 旧版 SETNX 脏键("{}"): 读侧视为未开通(404)/开通自动清除重建
        (生产 member3 实证场景——2026-09-12 部署前 500, 部署后 404)
    S4 异常键("junk"): 读侧/开通均诚实拒绝(409, 不盲目删键)
    S5 数值还原: 余额读回 float

用法(backend 容器内执行):
    VERIFY_BASE_URL=http://127.0.0.1:8000 \
    VERIFY_REDIS_HOST=redis python verify_wallet_live.py
环境变量:
    VERIFY_BASE_URL  API 基址(默认 http://127.0.0.1:8000)
    VERIFY_REDIS_HOST/PORT  redis 直连(默认 127.0.0.1:6379;
    容器内为 redis:6379——脏键构造/余额注入用)

scratch 数据: 会员 990101-990103 + 钱包键 990101-990103, 测后清理。
"""
import json
import os
import sys
import urllib.error
import urllib.request

B = os.environ.get("VERIFY_BASE_URL", "http://127.0.0.1:8000")
PASS = FAIL = 0

UID = 990101   # S1/S2 主体
UID2 = 990102  # S3 旧脏键自愈
UID3 = 990103  # S4 异常键诚实拒绝


def req(method, path, body=None, headers=None):
    data = (json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None else None)
    r = urllib.request.Request(B + path, data=data, method=method)
    r.add_header("Content-Type", "application/json; charset=utf-8")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def errmsg(r):
    """错误信息提取(全局异常格式 success/error; 兼容 detail)"""
    return str(r.get("error") or r.get("detail") or r)


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} -- {detail}")


def redis_conn():
    import redis
    return redis.Redis(
        host=os.environ.get("VERIFY_REDIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("VERIFY_REDIS_PORT", "6379")),
        decode_responses=True)


def seed_member(c, uid):
    """播种 scratch 会员(growth 3000 = L3, 满足开通门槛 ≥500)"""
    c.delete(f"zhuxiang:member:{uid}")
    c.hset(f"zhuxiang:member:{uid}", mapping={
        "id": uid, "phone": f"1390000{uid}", "nickname": f"verify-live-{uid}",
        "status": 1, "growth_value": 3000, "points": 0, "level": 3,
    })


def cleanup(c):
    """幂等清理 scratch 数据(会员/钱包/索引)"""
    for uid in (UID, UID2, UID3):
        c.delete(f"zhuxiang:member:{uid}", f"zhuxiang:wallet:{uid}")
        c.srem("zhuxiang:wallet:index", uid)


print("=" * 62)
print("钱包 Redis 态开通/读取加固 实机部署验收(e1f2cda)")
print("=" * 62)

c = redis_conn()
cleanup(c)
seed_member(c, UID)
seed_member(c, UID2)
seed_member(c, UID3)

# ---------- S0 鉴权 ----------
print("\n[S0 鉴权]")
s, r = req("POST", "/api/wallet/open")
record("无 X-Member-Id → 401", s == 401, f"status={s}")

# ---------- S1 全新开通 ----------
print("\n[S1 全新开通(单条 HSET 原子写入)]")
s, r = req("POST", "/api/wallet/open", {}, {"X-Member-Id": str(UID)})
record("开通 200 + success + active",
       s == 200 and r.get("success") and r.get("status") == "active",
       f"s={s} r={r}")
record("键类型 hash", c.type(f"zhuxiang:wallet:{UID}") == "hash",
       f"type={c.type(f'zhuxiang:wallet:{UID}')}")
record("无占位字段(_placeholder 不存在)",
       "_placeholder" not in c.hgetall(f"zhuxiang:wallet:{UID}"))
record("全局索引入册",
       c.sismember("zhuxiang:wallet:index", UID) == 1)

# ---------- S2 重复开通 ----------
print("\n[S2 重复开通(409 拒绝且数据不被重置)]")
c.hset(f"zhuxiang:wallet:{UID}", "balance", "123.45")
s, r = req("POST", "/api/wallet/open", {}, {"X-Member-Id": str(UID)})
record("重复开通 → 409",
       s == 409 and "已开通" in errmsg(r),
       f"s={s} r={r}")
record("余额未被重置(redis 直读)",
       c.hget(f"zhuxiang:wallet:{UID}", "balance") == "123.45",
       f"balance={c.hget(f'zhuxiang:wallet:{UID}', 'balance')}")
s, r = req("GET", "/api/wallet/info", None, {"X-Member-Id": str(UID)})
record("GET info 余额读回 123.45",
       s == 200 and r.get("currentBalance") == 123.45,
       f"s={s} bal={r.get('currentBalance')}")

# ---------- S3 旧版 SETNX 脏键自愈 ----------
print("\n[S3 旧脏键自愈(生产 member3 实证场景)]")
c.set(f"zhuxiang:wallet:{UID2}", "{}")
s, r = req("GET", "/api/wallet/info", None, {"X-Member-Id": str(UID2)})
record("脏键读侧视为未开通 → 404", s == 404, f"status={s}")
s, r = req("POST", "/api/wallet/open", {}, {"X-Member-Id": str(UID2)})
record("脏键开通自愈 → 200",
       s == 200 and r.get("success"), f"s={s} r={r}")
record("自愈后键类型 hash",
       c.type(f"zhuxiang:wallet:{UID2}") == "hash",
       f"type={c.type(f'zhuxiang:wallet:{UID2}')}")

# ---------- S4 异常键诚实拒绝 ----------
print("\n[S4 异常键诚实拒绝(不盲目删键)]")
c.set(f"zhuxiang:wallet:{UID3}", "junk")
s, r = req("GET", "/api/wallet/info", None, {"X-Member-Id": str(UID3)})
record("异常键读侧 → 409(人工核查)", s == 409, f"status={s}")
s, r = req("POST", "/api/wallet/open", {}, {"X-Member-Id": str(UID3)})
record("异常键开通 → 409(不删键不自愈)",
       s == 409 and "人工核查" in errmsg(r),
       f"s={s} r={r}")

# ---------- S5 数值还原 ----------
print("\n[S5 数值还原]")
s, r = req("GET", "/api/wallet/info", None, {"X-Member-Id": str(UID)})
record("currentBalance 为 float 类型",
       isinstance(r.get("currentBalance"), float),
       f"type={type(r.get('currentBalance')).__name__}")

# ---------- 清理 ----------
cleanup(c)
print("\n[清理] scratch 会员/钱包/索引已清除")

print("\n" + "-" * 62)
print(f"总计: {PASS} 通过 / {FAIL} 失败")
print("-" * 62)
sys.exit(1 if FAIL else 0)
