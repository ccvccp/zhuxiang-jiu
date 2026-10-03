"""AUTH-TEST-01 P1: 真 Bearer 测试轨 + strict 模式常驻回归

背景:
    P0 恢复了直跑 HTTP 测试(头信任自举, 裸头 X-Role 通道); 本文件
    建立真 token 轨——测试面与生产面同构(Bearer 全链: 中间件验签
    →注入身份→路由层鉴权), 并把 compat 剥离语义/应急回滚开关/
    strict 401 语义固化为常驻回归。

验收语义(立项方案 §五):
    1. mint_token 铸 admin/member 双角色(服务层直调 AuthService)
    2. 幂等: 重复铸 token 走 login 通道仍成功
    3. compat+头信任(测试默认): 裸头 X-Role: admin → 200(P0 通道)
    4. compat+默认剥离(生产语义): 裸头 → 403
    5. compat+默认剥离: admin Bearer → 200(真鉴权全链)
    6. compat+默认剥离: member Bearer+伪造 X-Role: admin → 403
    7. strict: 无 Bearer → 401 / 裸头 → 401(身份只能来自 JWT)
    8. strict: admin Bearer → 200 / member Bearer 访管理端 → 403

运行: python test_auth_support.py
"""
import test_support  # noqa: F401 (直跑自举: 内存模式; 首行约定)

import os
import sys

PASS = 0
FAIL = 0
RESULTS = []

# 管理端靶点(main 应用既有路由, 路由层读 X-Role 鉴权)
ADMIN_EP = "/api/hub/ops/overview"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def main():
    # --- ① 铸 token(P1 轨道就位) ---
    admin_token = test_support.mint_token(role="admin")
    check("铸token-admin(服务层直调)", bool(admin_token))
    member_token = test_support.mint_token(role="member")
    check("铸token-member", bool(member_token))
    again = test_support.mint_token(role="admin")  # 幂等: 二次走 login
    check("铸token-幂等(重复铸走 login 通道)", bool(again))
    check("铸token-双角色 token 互异", admin_token != member_token)

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    admin_bearer = {"Authorization": f"Bearer {admin_token}"}
    member_bearer = {"Authorization": f"Bearer {member_token}"}

    # --- ② compat+头信任(P0 通道回归): 裸头 200 ---
    os.environ["AUTH_COMPAT_TRUST_HEADERS"] = "1"
    r = client.get(ADMIN_EP, headers={"X-Role": "admin"})
    check("compat+头信任: 裸头 X-Role 200(P0 存量通道)",
          r.status_code == 200, f"status={r.status_code}")

    # --- ③ compat+默认剥离(生产语义) ---
    os.environ["AUTH_COMPAT_TRUST_HEADERS"] = "0"
    r = client.get(ADMIN_EP, headers={"X-Role": "admin"})
    check("compat+默认: 裸头被剥离 → 403",
          r.status_code == 403, f"status={r.status_code}")
    r = client.get(ADMIN_EP, headers=admin_bearer)
    check("compat+默认: admin Bearer 真链 200",
          r.status_code == 200, f"status={r.status_code}")
    r = client.get(ADMIN_EP,
                   headers={**member_bearer, "X-Role": "admin"})
    check("compat+默认: member Bearer+伪造X-Role → 403(JWT 声明覆盖)",
          r.status_code == 403, f"status={r.status_code}")

    # --- ④ strict(身份只能来自 JWT) ---
    os.environ["AUTH_MODE"] = "strict"
    r = client.get(ADMIN_EP)
    check("strict: 无 Bearer → 401",
          r.status_code == 401, f"status={r.status_code}")
    r = client.get(ADMIN_EP, headers={"X-Role": "admin"})
    check("strict: 裸头 → 401",
          r.status_code == 401, f"status={r.status_code}")
    r = client.get(ADMIN_EP, headers=admin_bearer)
    check("strict: admin Bearer → 200",
          r.status_code == 200, f"status={r.status_code}")
    r = client.get(ADMIN_EP, headers=member_bearer)
    check("strict: member Bearer 访管理端 → 403(注入 x-role=member)",
          r.status_code == 403, f"status={r.status_code}")

    # 恢复测试默认环境(避免污染同进程后续用例)
    os.environ["AUTH_MODE"] = "compat"
    os.environ["AUTH_COMPAT_TRUST_HEADERS"] = "1"

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
