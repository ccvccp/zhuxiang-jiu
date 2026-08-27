"""网站图标智能管理模块 HTTP 层权限专项验证(TestClient 全栈)

在宿主机运行(需已安装 fastapi + httpx):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_site_theme_auth.py

验证目标(v8.1 JWT 强校验升级):
    1. 伪造 X-Role: admin 头(无 Token) → 401(堵死旧头绕过漏洞)
    2. 伪造 X-Role + X-Admin-Id 头(无 Token) → 401
    3. 普通会员 JWT Token → 403(角色不足)
    4. 管理员 JWT Token → 200(全流程可用)
    5. 无效 Token(篡改签名) → 401
    6. 公开端点(激活主题/图标库)无需登录 → 200
"""

import asyncio
import os
import sys

# 必须在导入 main 之前设置(内存模式 + 认证兼容模式)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")

from fastapi.testclient import TestClient

from main import app
from core.auth import hash_password
from repositories.member_repository import MemberRepository
from repositories.store import reset_store
from services.auth_service import AuthService

client = TestClient(app)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


# 合法配色(通过 AI 健康度)
GOOD_COLORS = {
    "primary": "#355c44", "primaryLight": "#4a7c59",
    "navBar": "#355c44", "tabSelected": "#355c44",
    "tabColor": "#999999", "tabBg": "#ffffff", "textOnPrimary": "#ffffff",
}


def _mk_members_and_tokens() -> dict:
    """构造 admin/member 两个会员并登录获取 JWT, 返回角色→Token 映射"""
    async def _create():
        repo = MemberRepository()
        tokens = {}
        auth = AuthService()
        for key, phone, role in (("admin", "13900000099", "admin"),
                                 ("member", "13900000088", "member")):
            await repo.create({
                "phone": phone, "nickname": f"测试{key}",
                "password": hash_password("x" * 64), "status": 1,
                "role": role, "level": 3, "growth_value": 600,
                "points": 0,
            })
            # 登录签发 JWT(register 也可, 这里直接调 service.login)
            result = await auth.login(phone=phone, password="x" * 64)
            tokens[key] = result["accessToken"]
        return tokens
    return asyncio.run(_create())


def main():
    print("=" * 64)
    print("网站图标智能管理模块 · HTTP 权限专项验证(JWT 强校验)")
    print("=" * 64)

    reset_store()
    T = _mk_members_and_tokens()
    admin_hdr = {"Authorization": f"Bearer {T['admin']}"}
    member_hdr = {"Authorization": f"Bearer {T['member']}"}
    forged_hdr = {"X-Role": "admin", "X-Admin-Id": "99999"}

    # --------------------------------------------------------
    # 1. 伪造头绕过测试(v8.1 核心安全用例)
    # --------------------------------------------------------
    r = client.get("/api/site-theme/themes", headers=forged_hdr)
    record("test_01_forged_xrole_without_token_rejected",
           r.status_code == 401, f"status={r.status_code}, body={r.json()}")

    r = client.post("/api/site-theme/themes/1/activate", headers=forged_hdr)
    record("test_02_forged_header_cannot_activate",
           r.status_code == 401, f"status={r.status_code}")

    r = client.post("/api/site-theme/admin/logs/1/rollback", headers=forged_hdr)
    record("test_03_forged_header_cannot_rollback",
           r.status_code == 401, f"status={r.status_code}")

    # --------------------------------------------------------
    # 2. 完全无凭证
    # --------------------------------------------------------
    r = client.get("/api/site-theme/themes")
    record("test_04_no_credentials_rejected",
           r.status_code == 401, f"status={r.status_code}")

    # --------------------------------------------------------
    # 3. 普通会员 Token → 403
    # --------------------------------------------------------
    r = client.get("/api/site-theme/themes", headers=member_hdr)
    record("test_05_member_token_forbidden",
           r.status_code == 403, f"status={r.status_code}, body={r.json()}")

    r = client.post("/api/site-theme/themes", headers=member_hdr,
                    json={"name": "越权主题", "colors": GOOD_COLORS})
    record("test_06_member_token_cannot_create",
           r.status_code == 403, f"status={r.status_code}")

    # --------------------------------------------------------
    # 4. 无效 Token(篡改) → 401
    # --------------------------------------------------------
    r = client.get("/api/site-theme/themes",
                   headers={"Authorization": f"Bearer {T['admin'][:-4]}abcd"})
    record("test_07_tampered_token_rejected",
           r.status_code == 401, f"status={r.status_code}")

    # --------------------------------------------------------
    # 5. 管理员 Token 全流程 → 200
    # --------------------------------------------------------
    r = client.post("/api/site-theme/themes", headers=admin_hdr,
                    json={"name": "权限验证主题", "colors": GOOD_COLORS,
                          "description": "JWT 鉴权专项"})
    ok = r.status_code == 200 and r.json().get("status") == "draft"
    record("test_08_admin_token_create_theme",
           ok, f"status={r.status_code}, body={r.json()}")
    theme_id = r.json().get("themeId", 0) if ok else 0

    r = client.get("/api/site-theme/themes", headers=admin_hdr)
    record("test_09_admin_token_list_themes",
           r.status_code == 200 and len(r.json().get("themes", [])) >= 1,
           f"status={r.status_code}")

    if theme_id:
        r = client.post(f"/api/site-theme/themes/{theme_id}/ai-check",
                        headers=admin_hdr)
        ok = r.status_code == 200 and r.json().get("passed")
        record("test_10_admin_token_ai_check",
               ok, f"status={r.status_code}, score={r.json().get('score')}")

        r = client.post(f"/api/site-theme/themes/{theme_id}/activate",
                        headers=admin_hdr)
        ok = r.status_code == 200 and r.json().get("success")
        record("test_11_admin_token_activate",
               ok, f"status={r.status_code}, body={r.json()}")

    r = client.get("/api/site-theme/admin/logs", headers=admin_hdr)
    ok = r.status_code == 200 and len(r.json().get("logs", [])) >= 1
    record("test_12_admin_token_audit_logs",
           ok, f"status={r.status_code}")

    # 审计记录操作人 = Token 载荷中的会员 ID(非伪造的 99999)
    if ok:
        first_log = r.json()["logs"][0]
        record("test_13_admin_id_from_token_not_header",
               first_log.get("adminId") != 99999,
               f"adminId={first_log.get('adminId')}")

    r = client.get("/api/site-theme/admin/recommend", headers=admin_hdr)
    record("test_14_admin_token_recommend",
           r.status_code == 200 and "recommendations" in r.json(),
           f"status={r.status_code}")

    # --------------------------------------------------------
    # 6. 公开端点无需登录
    # --------------------------------------------------------
    r = client.get("/api/site-theme/active")
    ok = r.status_code == 200 and r.json().get("themeId") in (1, theme_id)
    record("test_15_public_active_theme_no_auth",
           ok, f"status={r.status_code}, body={r.json()}")

    r = client.get("/api/site-theme/icons")
    record("test_16_public_icons_no_auth",
           r.status_code == 200 and "icons" in r.json(),
           f"status={r.status_code}")

    # --------------------------------------------------------
    # 输出
    # --------------------------------------------------------
    for line in RESULTS:
        print(line)
    print("=" * 64)
    print(f"网站图标智能管理 HTTP 权限专项: 通过 {PASS} / 失败 {FAIL} / 总计 {PASS + FAIL}")
    print("=" * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
