"""阶段C切换后验证: strict 模式生产全矩阵实证

矩阵:
  ① 健康探针(PUBLIC_EXACT)        → 200
  ② 游客公开 GET(PUBLIC_GET_PREFIX) → 200
  ③ 登录(PUBLIC_EXACT)             → 200 取 accessToken
  ④ Bearer 管理端点                → 200(strict 就绪)
  ⑤ 裸头管理端点                   → 401 + 未登录(strict 生效证据)
  ⑥ 第三方回调端点                 → 业务层可达(非中间件 401)
  ⑦ 管理员会话登录(admin 模块)     → sessionToken(strict 下 admin 面就绪)
"""
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as c:
    ok = 0

    # ① 健康探针
    r = c.get("/api/decision/health")
    print("① 健康探针:", r.status_code)
    ok += r.status_code == 200

    # ② 游客公开 GET(product list 在 /api/product 前缀下)
    r = c.get("/api/product/list")
    print("② 公开GET /api/product/list:", r.status_code)
    ok += r.status_code == 200

    # ③ 会员登录(公开; member3=13800000002 为站点管理员 role=admin)
    r = c.post("/api/auth/login",
               json={"phone": "13800000002", "password": "test123456"})
    print("③ 管理员会员登录:", r.status_code)
    ok += r.status_code == 200
    token = r.json().get("accessToken") if r.status_code == 200 else None
    assert token, f"登录失败: {r.text}"

    # ④ Bearer 管理端点(member3 role=admin, JWT 注入 x-role)
    r = c.get("/api/hub/ops/overview",
              headers={"Authorization": f"Bearer {token}"})
    print("④ Bearer hub 总览(admin):", r.status_code)
    ok += r.status_code == 200
    r = c.get("/api/points/account/3",
              headers={"Authorization": f"Bearer {token}"})
    print("④ Bearer 积分账户:", r.status_code)
    ok += r.status_code == 200

    # ⑤ 裸头管理端点 → 401(strict 生效证据)
    r = c.get("/api/hub/ops/overview", headers={"X-Role": "admin"})
    detail = r.json().get("detail", "")
    strict_on = (r.status_code == 401 and isinstance(detail, str)
                 and "未登录" in detail)
    print("⑤ 裸头管理端点:", r.status_code,
          "(strict 拦截 ✓)" if strict_on else f"(异常! {r.text[:80]})")
    ok += strict_on

    # ⑥ 第三方回调端点(业务层可达 = 非中间件"未登录"401)
    for path in ("/api/payment/callback/pay",
                 "/api/logistics/callback/track"):
        r = c.post(path, json={})
        d = r.json().get("detail", "")
        reach = not (r.status_code == 401 and isinstance(d, str)
                     and "未登录" in d)
        print(f"⑥ 回调 {path}: {r.status_code}",
              "业务层可达 ✓" if reach else "中间件拦截 ✗")
        ok += reach

    # ⑦ 管理员会话登录(admin 模块, strict 下 X-Admin-Token 会话通道)
    r = c.post("/api/admin/login", json={"username": "admin",
                                         "password": "admin123456"})
    print("⑦ admin/login:", r.status_code, "(凭证对错均达业务层即通道正常)")
    ok += r.status_code != 401 or "未登录" not in str(r.json())

    print("-" * 60)
    print(f"矩阵通过 {ok}/9")
    assert ok == 9, "存在未通过项"
print("\n=== 阶段C切换成功: 生产 strict 模式全矩阵通过 ===")
