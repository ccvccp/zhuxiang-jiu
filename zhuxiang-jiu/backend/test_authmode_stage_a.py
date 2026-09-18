"""AUTH_MODE 阶段A 冒烟: 双模式头形态行为验证(compat 下零变化 + Bearer 通道就绪)

验证三种形态(与改造后前端 headers() 的实际输出一致):
  ① 未登录裸头(compat: X-Role)        → 200(行为不变)
  ② 登录 Bearer+X-Role 叠加头          → 200(中间件注入身份, strict 就绪)
  ③ 登录 Bearer 单独(未来 strict 形态)  → 200(身份纯由 JWT 注入)
"""
import os

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from fastapi.testclient import TestClient
from main import app

PASS = 0
FAIL = 0


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  \u2713 {name}")
    else:
        FAIL += 1
        print(f"  \u2717 {name} \u2014 {detail}")


client = TestClient(app)

# --- 登录取 JWT(member1) ---
r = client.post("/api/auth/login", json={"phone": "13800000001",
                                         "password": "test123456"})
if r.status_code != 200:
    # 密码不对则用测试种子常见密码再试
    for pwd in ("pass123456", "Aa123456!", "Passw0rd!123"):
        r = client.post("/api/auth/login",
                        json={"phone": "13800000001", "password": pwd})
        if r.status_code == 200:
            break
if r.status_code != 200:
    # 兜底: 注册新测试号(本地内存态)
    r = client.post("/api/auth/register",
                    json={"phone": "13800009999", "password": "test123456"})
    r = client.post("/api/auth/login",
                    json={"phone": "13800009999", "password": "test123456"})
TOKEN = r.json().get("accessToken") if r.status_code == 200 else None
record("登录获取 accessToken", TOKEN is not None,
       f"status={r.status_code}")
if not TOKEN:
    raise SystemExit("无法登录, 冒烟中止")

# --- admin Bearer(参照 test_auth_middleware.test_11 范式:
#     member1 改 role=admin 后重新登录, JWT 声明携带 x-role: admin) ---
from repositories.store import _mock_store

_mock_store["members"][1]["role"] = "admin"
r = client.post("/api/auth/login", json={"phone": "13800000001",
                                         "password": "test123456"})
ADMIN_TOKEN = r.json().get("accessToken") if r.status_code == 200 else None
record("admin 角色登录获取 accessToken", ADMIN_TOKEN is not None,
       f"status={r.status_code}")
if not ADMIN_TOKEN:
    raise SystemExit("admin 登录失败, 冒烟中止")
ADMIN_BEARER = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
BEARER = {"Authorization": f"Bearer {TOKEN}"}

# --- ① 未登录裸头(compat 行为不变) ---
CASES = [
    ("GET", "/api/hub/ops/overview", {"X-Role": "admin"}, "hub 总览(裸头)"),
    ("GET", "/api/ai-learning/overview", {"X-Role": "admin"}, "ai-learning 总览(裸头)"),
    ("GET", "/api/monitor/stats", {"X-Role": "admin"}, "monitor 统计(裸头)"),
    ("GET", "/api/ads", None, "ads 公开GET(无头)"),
]
for method, path, hdrs, label in CASES:
    r = client.request(method, path, headers=hdrs)
    record(label, r.status_code == 200, f"status={r.status_code}")

# --- ② admin Bearer + X-Role 叠加(改造后前端登录态实际形态) ---
r = client.get("/api/hub/ops/overview",
               headers={**ADMIN_BEARER, "X-Role": "admin"})
record("hub 总览(admin Bearer+X-Role 叠加)", r.status_code == 200,
       f"status={r.status_code}")
r = client.get("/api/ai-learning/overview",
               headers={**ADMIN_BEARER, "X-Role": "admin"})
record("ai-learning 总览(admin Bearer 叠加)", r.status_code == 200,
       f"status={r.status_code}")

# --- ③ Bearer 单独(strict 形态预演: 身份纯由 JWT 注入) ---
r = client.get("/api/hub/panel", headers=ADMIN_BEARER)
record("hub panel(Bearer 单独, 公开)", r.status_code == 200,
       f"status={r.status_code}")
r = client.get("/api/hub/ops/overview", headers=ADMIN_BEARER)
record("hub 总览(admin Bearer 单独, 无旧头)", r.status_code == 200,
       f"status={r.status_code}")
# member Bearer + 伪造 X-Role: admin → JWT 声明覆盖伪造值 → 403(安全设计)
# (注入以存储当前角色为准, 先回滚 member1 的 admin 角色再验证)
_mock_store["members"][1]["role"] = "member"
r = client.get("/api/hub/ops/overview",
               headers={**BEARER, "X-Role": "admin"})
record("hub 总览(member Bearer+伪造X-Role → 403 预期)",
       r.status_code == 403, f"status={r.status_code}")

# --- ④ 用户侧 X-Member-Id + Bearer 叠加(widget 形态) ---
r = client.get("/api/points/account/1",
               headers={**BEARER, "X-Member-Id": "1"})
record("积分账户(Bearer+X-Member-Id 叠加)", r.status_code == 200,
       f"status={r.status_code}")

print("-" * 64)
print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
raise SystemExit(1 if FAIL else 0)
