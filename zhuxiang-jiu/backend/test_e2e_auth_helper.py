"""E2E JWT 范式公共辅助验证: 本地(strict)双角色全链

运行(standalone):
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_e2e_auth_helper.py

NOTE: 环境设置与全部断言在 main() 内执行(非模块顶层), 避免 pytest
全量收集时导入副作用泄漏 AUTH_MODE=strict 污染同进程后续测试
(2026-10-12 全量体检修复)。
"""
# ---------------------------------------------------------------------------
# [SCRIPT-STYLE TEST | 脚本式测试]  pytest 收集为 0 属预期（IMP-12 台账文件）。
#   运行方式 : python test_e2e_auth_helper.py        （cwd 必须是 zhuxiang-jiu/backend）
#   结果判定 : 看脚本尾部打印的 pass/fail 计数，不要看 pytest 输出。
#   勿误判   : pytest --collect-only 对本文件收集 0 条并以退出码 5
#              「no tests collected」结束——这是本文件的正常形态。
#              禁止据此判定测试损坏，禁止把退出码 5 当失败上报。
#   成因     : 用例以 record()/模块级计数函数组织，或 Test* 类内方法未命名
#              为 test_*，不满足 pytest.ini 的 python_functions=test_* 规则。
#   台账     : backend/scriptstyle_tests_manifest.json（机器可读）
#   闸门     : backend/test_scriptstyle_gate.py 校验清单一致性与 banner 存在性
# ---------------------------------------------------------------------------

import os
import sys


def main():
    os.environ["LOCK_MODE"] = "asyncio"
    os.environ["STORE_MODE"] = "asyncio"
    os.environ["AUTH_MODE"] = "strict"

    from fastapi.testclient import TestClient
    from main import app

    from e2e_auth_helper import E2EAuth

    PASS = 0
    FAIL = 0

    def record(name, ok, detail=""):
        nonlocal PASS, FAIL
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
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
