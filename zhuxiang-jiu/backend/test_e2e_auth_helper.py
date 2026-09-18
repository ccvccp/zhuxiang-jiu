"""E2E JWT 范式公共辅助验证: 本地(strict)双角色全链"""
import os

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "strict"

from fastapi.testclient import TestClient
from main import app

from e2e_auth_helper import E2EAuth

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
auth = E2EAuth(client)

# 管理面: JWT 注入 x-role=admin
admin = auth.admin_headers()
record("admin_headers 登录", bool(admin.get("Authorization")))
r = client.get("/api/hub/ops/overview", headers=admin)
record("管理面 hub 总览(admin Bearer)", r.status_code == 200,
       f"{r.status_code}")

# 用户面: JWT 注入 x-member-id
member = auth.member_headers()
r = client.get("/api/points/account/1", headers=member)
record("用户面 积分账户(member Bearer)", r.status_code == 200,
       f"{r.status_code}")

# 权限边界: member 访问管理面 403(注入 x-role=member)
r = client.get("/api/hub/ops/overview", headers=member)
record("权限边界 member Bearer→403", r.status_code == 403, f"{r.status_code}")

# 裸头对照: 旧范式 401(strict)
r = client.get("/api/hub/ops/overview", headers={"X-Role": "admin"})
record("裸头对照 X-Role→401", r.status_code == 401, f"{r.status_code}")

print("-" * 64)
print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
raise SystemExit(1 if FAIL else 0)
