"""JWT 认证中间件单元测试(Mock ASGI 应用直调, 无需 fastapi)

验证 core/auth_middleware.py 的完整行为:
    1. 路径策略(6):        公开精确/公开GET前缀/POST非公开/非API路径/OPTIONS预检/受保护路径
    2. JWT 校验与注入(6):  身份头注入/伪造头覆盖/无重复头/admin角色注入/兼容旧头透传
    3. 无效 Token 拒绝(6): 乱码/过期/黑名单/refresh冒用/格式错误/会员不存在
    4. 账号状态(1):        禁用账号 token 拒绝
    5. strict 模式(4):     无token 401/公开路径放行/有效token放行/恢复compat
    6. 纯净性(2):          中间件模块零第三方依赖/错误响应JSON格式

运行(无需 fastapi, 纯标准库):
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_auth_middleware.py
"""

import asyncio
import json
import os
import sys

# 确保使用内存模式(必须在导入服务层之前设置)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from core.auth import create_token
from core.auth_middleware import (
    JWTAuthMiddleware, is_public_path, get_auth_mode,
    inject_identity, PUBLIC_EXACT, PUBLIC_GET_PREFIXES,
)
from services.auth_service import AuthService
from repositories.store import _mock_store, reset_store as _reset_store_impl

# 测试结果收集
PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    _reset_store_impl()


# ============================================================
# Mock ASGI 应用与调用工具
# ============================================================

class MockASGIApp:
    """记录 scope 的模拟内层应用(模拟真实路由层)"""

    def __init__(self):
        self.last_scope = None
        self.call_count = 0

    async def __call__(self, scope, receive, send):
        self.last_scope = dict(scope)  # 快照(防止后续修改影响断言)
        self.last_scope["headers"] = list(scope.get("headers", []))
        self.call_count += 1
        body = b'{"success": true}'
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": body})


def make_scope(method, path, headers=None):
    """构建 ASGI http scope"""
    raw = [
        (k.lower().encode("latin-1"), str(v).encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 12345),
        "root_path": "",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": raw,
    }


async def invoke(middleware, scope):
    """调用中间件, 返回 (状态码, 响应体dict, 是否到达内层应用)"""
    response = {"status": 0, "body": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
        elif message["type"] == "http.response.body":
            response["body"] += message.get("body", b"")

    reached = await middleware._call_and_track(scope, receive, send)
    try:
        parsed = json.loads(response["body"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        parsed = response["body"].decode("utf-8", errors="replace")
    return response["status"], parsed, reached


def _scope_headers(scope_snapshot):
    """把 scope 快照头列表转为小写键字典(重复键记录为列表)"""
    result = {}
    for key, value in scope_snapshot.get("headers", []):
        k = key.decode("latin-1")
        v = value.decode("latin-1")
        result.setdefault(k, []).append(v)
    return {k: (v[0] if len(v) == 1 else v) for k, v in result.items()}


def build_middleware():
    """构建中间件实例(包一层以便追踪是否到达内层)"""
    mock = MockASGIApp()
    mw = JWTAuthMiddleware(mock)

    async def call_and_track(scope, receive, send):
        before = mock.call_count
        await mw(scope, receive, send)
        return mock.call_count > before

    mw._call_and_track = call_and_track
    return mw, mock


async def login_token(phone, password):
    """通过服务层登录获取 access token"""
    result = await AuthService().login(phone, password)
    return result["accessToken"]


# ============================================================
# 1. 路径保护策略
# ============================================================

class TestPathPolicy:
    async def run(self):
        mw, mock = build_middleware()

        # test 1: 公开精确路径(登录接口, POST 无 token 放行)
        status, _, reached = await invoke(
            mw, make_scope("POST", "/api/auth/login"))
        record("test_01_public_exact_login",
               reached and status == 200,
               f"reached={reached}, status={status}")

        # test 2: 公开 GET 前缀(产品浏览放行)
        status, _, reached = await invoke(
            mw, make_scope("GET", "/api/product/list"))
        record("test_02_public_get_product_list",
               reached and status == 200,
               f"reached={reached}, status={status}")

        # test 3: 同前缀 POST 非公开(产品评价提交需登录, compat 下放行但会走旧头校验)
        #         公开策略只对 GET 生效
        record("test_03_post_not_public",
               not is_public_path("/api/product/1/reviews", "POST"),
               "POST /api/product/1/reviews 被误判为公开")

        # test 4: 非 /api 路径放行(docs/openapi)
        status, _, reached = await invoke(mw, make_scope("GET", "/docs"))
        record("test_04_non_api_path_passes",
               reached and status == 200,
               f"reached={reached}")

        # test 5: OPTIONS 预检请求放行(CORS 处理)
        status, _, reached = await invoke(
            mw, make_scope("OPTIONS", "/api/order/my"))
        record("test_05_options_preflight_passes",
               reached, f"reached={reached}")

        # test 6: 受保护路径无 token compat 模式放行(旧头兼容)
        status, _, reached = await invoke(
            mw, make_scope("GET", "/api/order/my",
                           headers={"X-Member-Id": "1"}))
        record("test_06_compat_protected_path_legacy_header",
               reached and status == 200,
               f"reached={reached}, status={status}")

        # test 7: 公开路径清单覆盖健康检查
        record("test_07_health_endpoints_public",
               all(p in PUBLIC_EXACT for p in (
                   "/api/decision/health", "/api/monitor/health",
                   "/api/maintenance/health")),
               "健康检查端点未全部列入公开清单")


# ============================================================
# 2. JWT 校验与身份注入
# ============================================================

class TestJwtInjection:
    async def run(self):
        reset_store()
        token = await login_token("13800000001", "test123456")

        # test 8: 有效 token → 注入 x-member-id / x-role
        mw, mock = build_middleware()
        status, _, reached = await invoke(
            mw, make_scope("GET", "/api/order/my",
                           headers={"Authorization": f"Bearer {token}"}))
        headers = _scope_headers(mock.last_scope)
        record("test_08_identity_injected",
               reached and headers.get("x-member-id") == "1"
               and headers.get("x-role") == "member",
               f"headers={headers}")

        # test 9: 伪造头覆盖(X-Member-Id/X-Role 被 JWT 声明替换)
        mw, mock = build_middleware()
        status, _, reached = await invoke(
            mw, make_scope("GET", "/api/order/my",
                           headers={
                               "Authorization": f"Bearer {token}",
                               "X-Member-Id": "99999",
                               "X-Role": "admin",
                           }))
        headers = _scope_headers(mock.last_scope)
        record("test_09_forged_headers_overridden",
               reached and headers.get("x-member-id") == "1"
               and headers.get("x-role") == "member",
               f"headers={headers}")

        # test 10: 注入头无重复(重复会变 "99999, 1" 破坏 int 解析)
        record("test_10_no_duplicate_headers",
               isinstance(headers.get("x-member-id"), str)
               and "," not in str(headers.get("x-member-id")),
               f"x-member-id={headers.get('x-member-id')!r}")

        # test 11: admin 角色注入(admin token → x-role=admin)
        _mock_store["members"][1]["role"] = "admin"
        admin_token = await login_token("13800000001", "test123456")
        mw, mock = build_middleware()
        status, _, reached = await invoke(
            mw, make_scope("GET", "/api/order/admin/list",
                           headers={"Authorization": f"Bearer {admin_token}"}))
        headers = _scope_headers(mock.last_scope)
        record("test_11_admin_role_injected",
               reached and headers.get("x-role") == "admin",
               f"headers={headers}")
        _mock_store["members"][1]["role"] = "member"

        # test 12: token 角色与存储不一致时以存储为准(角色变更即时生效)
        mw, mock = build_middleware()
        status, _, reached = await invoke(
            mw, make_scope("GET", "/api/order/my",
                           headers={"Authorization": f"Bearer {admin_token}"}))
        headers = _scope_headers(mock.last_scope)
        record("test_12_role_from_storage_not_token",
               reached and headers.get("x-role") == "member",
               f"headers={headers}")

        # test 13: compat 模式无 token 时旧头原样透传(不移除)
        mw, mock = build_middleware()
        status, _, reached = await invoke(
            mw, make_scope("GET", "/api/member/profile",
                           headers={"X-Member-Id": "1", "X-Role": "admin"}))
        headers = _scope_headers(mock.last_scope)
        record("test_13_compat_legacy_headers_passthrough",
               reached and headers.get("x-member-id") == "1"
               and headers.get("x-role") == "admin",
               f"headers={headers}")


# ============================================================
# 3. 无效 Token 拒绝
# ============================================================

class TestInvalidToken:
    async def run(self):
        reset_store()
        mw, mock = build_middleware()
        protected = "/api/order/my"

        # test 14: 乱码 token → 401, 不到达内层
        status, body, reached = await invoke(
            mw, make_scope("GET", protected,
                           headers={"Authorization":
                                    "Bearer not.a.valid.jwt"}))
        record("test_14_garbage_token_rejected",
               status == 401 and not reached,
               f"status={status}, reached={reached}, body={body}")

        # test 15: 过期 token → 401
        expired = create_token(1, "member", "access", ttl=-60)
        status, body, reached = await invoke(
            mw, make_scope("GET", protected,
                           headers={"Authorization": f"Bearer {expired}"}))
        record("test_15_expired_token_rejected",
               status == 401 and not reached,
               f"status={status}, body={body}")

        # test 16: 登出后 token 进黑名单 → 401
        svc = AuthService()
        pair = await svc.login("13800000001", "test123456")
        await svc.logout(pair["accessToken"], pair["refreshToken"])
        status, body, reached = await invoke(
            mw, make_scope("GET", protected,
                           headers={"Authorization":
                                    f"Bearer {pair['accessToken']}"}))
        record("test_16_revoked_token_rejected",
               status == 401 and not reached,
               f"status={status}, body={body}")

        # test 17: refresh token 冒充 access token → 401
        pair2 = await svc.login("13800000001", "test123456")
        status, body, reached = await invoke(
            mw, make_scope("GET", protected,
                           headers={"Authorization":
                                    f"Bearer {pair2['refreshToken']}"}))
        record("test_17_refresh_as_access_rejected",
               status == 401 and not reached,
               f"status={status}, body={body}")

        # test 18: 非 Bearer 格式(Basic) → 401
        status, body, reached = await invoke(
            mw, make_scope("GET", protected,
                           headers={"Authorization": "Basic dXNlcjpwYXNz"}))
        record("test_18_non_bearer_scheme_rejected",
               status == 401 and not reached,
               f"status={status}, body={body}")

        # test 19: Bearer 后为空 → 401
        status, body, reached = await invoke(
            mw, make_scope("GET", protected,
                           headers={"Authorization": "Bearer "}))
        record("test_19_empty_bearer_rejected",
               status == 401 and not reached,
               f"status={status}")

        # test 20: Token 有效但会员不存在 → 401
        ghost = create_token(99999, "member", "access")
        status, body, reached = await invoke(
            mw, make_scope("GET", protected,
                           headers={"Authorization": f"Bearer {ghost}"}))
        record("test_20_ghost_member_token_rejected",
               status == 401 and not reached,
               f"status={status}, body={body}")

        # test 21: 被禁用账号 token → 401(先登录拿 token, 再禁用账号)
        pair3 = await svc.login("13800000001", "test123456")
        _mock_store["members"][1]["status"] = 0
        status, body, reached = await invoke(
            mw, make_scope("GET", protected,
                           headers={"Authorization":
                                    f"Bearer {pair3['accessToken']}"}))
        record("test_21_disabled_member_token_rejected",
               status == 401 and not reached,
               f"status={status}, body={body}")


# ============================================================
# 4. strict 模式
# ============================================================

class TestStrictMode:
    async def run(self):
        reset_store()
        token = await login_token("13800000001", "test123456")
        try:
            os.environ["AUTH_MODE"] = "strict"
            mw, mock = build_middleware()

            # test 22: 无 token 受保护路径 → 401, 不到达内层
            status, body, reached = await invoke(
                mw, make_scope("GET", "/api/order/my"))
            record("test_22_strict_no_token_rejected",
                   status == 401 and not reached
                   and "Bearer" in str(body.get("detail", "")),
                   f"status={status}, reached={reached}, body={body}")

            # test 23: 无 token 公开路径 → 放行
            status, _, reached = await invoke(
                mw, make_scope("GET", "/api/product/list"))
            record("test_23_strict_public_path_allowed",
                   reached and status == 200,
                   f"reached={reached}")

            # test 24: 有效 token 受保护路径 → 放行
            status, _, reached = await invoke(
                mw, make_scope("GET", "/api/order/my",
                               headers={"Authorization":
                                        f"Bearer {token}"}))
            record("test_24_strict_valid_token_allowed",
                   reached and status == 200,
                   f"reached={reached}, status={status}")

            # test 25: strict 下旧 X-Member-Id 头被拒(不能绕过)
            status, body, reached = await invoke(
                mw, make_scope("GET", "/api/member/profile",
                               headers={"X-Member-Id": "1"}))
            record("test_25_strict_legacy_header_rejected",
                   status == 401 and not reached,
                   f"status={status}, reached={reached}")
        finally:
            os.environ["AUTH_MODE"] = "compat"

        # test 26: 恢复 compat 后旧头重新生效
        mw, mock = build_middleware()
        status, _, reached = await invoke(
            mw, make_scope("GET", "/api/member/profile",
                           headers={"X-Member-Id": "1"}))
        record("test_26_restore_compat_legacy_header_works",
               reached and status == 200,
               f"reached={reached}, status={status}")


# ============================================================
# 5. 纯净性与错误响应格式
# ============================================================

class TestPurity:
    async def run(self):
        # test 27: 模块零第三方依赖(不导入 fastapi/starlette)
        import core.auth_middleware as m
        loaded = {name for name in sys.modules
                  if name.split(".")[0] in ("fastapi", "starlette")}
        record("test_27_no_fastapi_dependency",
               not loaded, f"已加载第三方模块: {loaded}")

        # test 28: 401 响应为合法 JSON 且含 detail 字段(与 FastAPI 异常格式一致)
        mw, mock = build_middleware()
        status, body, reached = await invoke(
            mw, make_scope("GET", "/api/order/my",
                           headers={"Authorization": "Bearer bad.token"}))
        record("test_28_error_response_json_format",
               status == 401 and isinstance(body, dict)
               and "detail" in body,
               f"status={status}, body={body}")


# ============================================================
# 主流程
# ============================================================

async def main():
    print("=" * 60)
    print("JWT 认证中间件单元测试(28 项, 无需 fastapi)")
    print("=" * 60)

    reset_store()
    for test_cls in (TestPathPolicy, TestJwtInjection, TestInvalidToken,
                     TestStrictMode, TestPurity):
        print(f"\n[{test_cls.__name__}]")
        await test_cls().run()

    print("\n" + "=" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        print("失败项:")
        for line in RESULTS:
            if "\u2717" in line:
                print(line)
        sys.exit(1)
    print("全部通过 \u2713")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
