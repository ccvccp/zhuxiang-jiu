"""E2E JWT 范式公共辅助(strict 模式生产/本地 E2E 统一鉴权层)

背景:
    AUTH_MODE=strict(2026-09-18)后, 旧裸头 E2E 范式(X-Role/X-Member-Id)
    全部 401 —— 本模块为后续所有模块检查 E2E 提供统一 JWT 登录/头构造。

用法(生产 E2E 脚本内):
    from e2e_auth_helper import E2EAuth

    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as c:
        auth = E2EAuth(c)
        admin = auth.admin_headers()      # 站点管理员(role=admin)
        member = auth.member_headers()     # 普通会员
        r = c.get("/api/hub/ops/overview", headers=admin)

凭据来源(2026-11-08 治理: 本文件不再有明文号/口令常量):
    统一走 prod_creds(env 驱动 + fail-closed, 缺失即 raise):
        admin  -> PROD_ADMIN_PHONE   (兼容 ZX_API_PHONE)
        member -> E2E_MEMBER_PHONE   (兼容 ZX_API_MEMBER_PHONE)
        口令   -> PROD_ADMIN_PASSWORD(兼容 ZX_API_PASSWORD / ZX_CRED_FILE)
    本地内存夹具(STORE_MODE=asyncio)的种子值单一事实源在
    repositories/store.py 的 SEED_* 常量, 由 test_e2e_auth_helper.py 在
    main() 内注入同名 env —— 测试进程之外本模块不落任何明文默认。

容器注入(重要):
    prod_synapse_gradation_verify.py / prod_zyh_gradation_verify.py 等生产
    验收脚本把本文件 docker cp 进容器 /app 使用。本文件现依赖 prod_creds,
    故重建容器后须**两个文件一起**注入:
        docker cp e2e_auth_helper.py <backend容器>:/app/
        docker cp prod_creds.py      <backend容器>:/app/
    并在容器 env(compose environment)注入上述凭据变量, 否则 fail-closed。

设计要点:
    - 兼容 httpx.Client(传 client 复用连接池)与自定义 base_url
    - get_jwt() 幂等: 每次登录取新 token(2h TTL, E2E 短周期内足够)
    - 与 get_jwt() in prod_e2e_jwt_paradigm.py 同源, 提升为公共模块
    - 凭据在调用时解析(非 import 时): 缺 env 不会让 import 本模块的脚本
      直接崩, 只在真正登录那一步 fail-closed
"""

import httpx

from prod_creds import admin_phone, login_password, member_phone


class E2EAuth:
    """E2E JWT 鉴权辅助(strict 模式统一入口)"""

    def __init__(self, client: httpx.Client):
        self.client = client

    def get_jwt(self, phone: str, password: str | None = None) -> str:
        """登录取 accessToken(公开白名单端点, 无需鉴权)

        Args:
            phone: 登录手机号(调用方显式给定时不再读 env)
            password: 口令; None = 由 prod_creds 从 env 解析

        Raises:
            AssertionError: 登录失败(账号不存在/密码错/风控二验)
            RuntimeError: password 为 None 且 env 未注入凭据(fail-closed)
        """
        pwd = login_password() if password is None else password
        r = self.client.post("/api/auth/login",
                             json={"phone": phone, "password": pwd})
        assert r.status_code == 200, f"登录失败({phone}): {r.text[:200]}"
        token = r.json().get("accessToken")
        assert token, f"响应缺 accessToken: {r.text[:200]}"
        return token

    def _bearer(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def admin_headers(self, phone: str | None = None) -> dict:
        """管理面请求头(JWT 注入 x-role: admin, 取代旧 X-Role 裸头)"""
        return self._bearer(self.get_jwt(phone or admin_phone()))

    def member_headers(self, phone: str | None = None) -> dict:
        """用户面请求头(JWT 注入 x-member-id, 取代旧 X-Member-Id 裸头)"""
        return self._bearer(self.get_jwt(phone or member_phone()))

    def bearer_only(self, token: str) -> dict:
        """已有 token 的裸 Bearer 头(供重试/多角色场景)"""
        return self._bearer(token)
