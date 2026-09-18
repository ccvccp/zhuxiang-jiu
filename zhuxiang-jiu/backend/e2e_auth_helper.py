"""E2E JWT 范式公共辅助(strict 模式生产/本地 E2E 统一鉴权层)

背景:
    AUTH_MODE=strict(2026-09-18)后, 旧裸头 E2E 范式(X-Role/X-Member-Id)
    全部 401 —— 本模块为后续所有模块检查 E2E 提供统一 JWT 登录/头构造。

用法(生产 E2E 脚本内):
    from e2e_auth_helper import E2EAuth

    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as c:
        auth = E2EAuth(c)
        admin = auth.admin_headers()      # 站点管理员(member3, role=admin)
        member = auth.member_headers()     # 普通会员(member1)
        r = c.get("/api/hub/ops/overview", headers=admin)

种子账号(生产/本地同口径):
    member1 = 13800000001(普通会员; 注意: 触发 AI 风控时登录可能要求
             短信二验——本地内存态无真实短信, 优先用管理员号)
    member3 = 13800000002(站点管理员 role=admin, E2E 管理面首选)
    密码统一 test123456

设计要点:
    - 兼容 httpx.Client(传 client 复用连接池)与自定义 base_url
    - get_jwt() 幂等: 每次登录取新 token(2h TTL, E2E 短周期内足够)
    - 与 get_jwt() in prod_e2e_jwt_paradigm.py 同源, 提升为公共模块
"""

import httpx

ADMIN_PHONE = "13800000002"    # member3 站点管理员(role=admin)
MEMBER_PHONE = "13800000001"   # member1 普通会员
DEFAULT_PASSWORD = "test123456"


class E2EAuth:
    """E2E JWT 鉴权辅助(strict 模式统一入口)"""

    def __init__(self, client: httpx.Client):
        self.client = client

    def get_jwt(self, phone: str, password: str = DEFAULT_PASSWORD) -> str:
        """登录取 accessToken(公开白名单端点, 无需鉴权)

        Raises:
            AssertionError: 登录失败(账号不存在/密码错/风控二验)
        """
        r = self.client.post("/api/auth/login",
                             json={"phone": phone, "password": password})
        assert r.status_code == 200, f"登录失败({phone}): {r.text[:200]}"
        token = r.json().get("accessToken")
        assert token, f"响应缺 accessToken: {r.text[:200]}"
        return token

    def _bearer(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def admin_headers(self, phone: str = ADMIN_PHONE) -> dict:
        """管理面请求头(JWT 注入 x-role: admin, 取代旧 X-Role 裸头)"""
        return self._bearer(self.get_jwt(phone))

    def member_headers(self, phone: str = MEMBER_PHONE) -> dict:
        """用户面请求头(JWT 注入 x-member-id, 取代旧 X-Member-Id 裸头)"""
        return self._bearer(self.get_jwt(phone))

    def bearer_only(self, token: str) -> dict:
        """已有 token 的裸 Bearer 头(供重试/多角色场景)"""
        return self._bearer(token)
