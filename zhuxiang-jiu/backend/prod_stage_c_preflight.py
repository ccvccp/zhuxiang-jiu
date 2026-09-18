"""阶段C切换前置验证: 白名单补丁生效(compat 下回调端点可达)"""
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15) as c:
    # 健康探针
    r = c.get("/api/decision/health")
    print("health:", r.status_code)
    assert r.status_code == 200

    # 4 个回调端点无 token 可达(到达业务层, 非中间件拦截)
    # 判据: 中间件拦截 = 401 + detail 为字符串且含"未登录/Bearer";
    # 到达业务层 = 422(pydantic 校验, detail 为数组) / 401 验签拒绝
    # (PAY60 渠道模式强制, success/error 格式) 等
    for path in ("/api/payment/callback/pay",
                 "/api/payment/callback/refund",
                 "/api/payment/callback/payout",
                 "/api/logistics/callback/track"):
        r = c.post(path, json={})
        body = r.json()
        detail = body.get("detail", "")
        blocked = (r.status_code == 401 and isinstance(detail, str)
                   and ("未登录" in detail or "Bearer" in detail))
        print(f"callback {path}: {r.status_code} "
              f"{'中间件拦截!' if blocked else '业务层可达'}")
        assert not blocked, f"{path} 被中间件拦截: {r.text}"

    # 管理端点裸头仍 compat 通过(尚未切换)
    r = c.get("/api/hub/ops/overview", headers={"X-Role": "admin"})
    print("hub overview (compat 裸头):", r.status_code)
    assert r.status_code == 200

print("\n=== 白名单补丁生效, compat 正常, 可执行 strict 切换 ===")
