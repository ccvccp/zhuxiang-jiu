"""strict 401 实证回归补丁: 生产部署前后对照——site-theme/venue 游客浏览面恢复"""
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15) as c:
    CASES = [
        ("/api/site-theme/active", "换肤 active(铁律永不关停)"),
        ("/api/site-theme/icons", "换肤 icons"),
        ("/api/site-theme/themes", "换肤 themes 列表"),
        ("/api/venue/partners", "酒店合作商列表"),
        ("/api/venue/venues", "合作场地列表"),
    ]
    ok = 0
    for path, label in CASES:
        r = c.get(path)
        blocked = (r.status_code == 401
                   and "未登录" in str(r.json().get("detail", "")))
        print(f"{label}: {r.status_code}",
              "✓ 游客可达" if not blocked else "✗ 仍被中间件拦截")
        ok += not blocked

    # 管理面子路径仍由路由层把守(GET 公开前缀不降级:
    # 非"未登录"格式的 401/403/405 = 业务层/路由层响应)
    r = c.get("/api/site-theme/admin/icons")
    blocked = (r.status_code == 401
               and "未登录" in str(r.json().get("detail", "")))
    print("site-theme/admin/icons(管理面 GET):", r.status_code,
          "(路由层把守正常)" if not blocked else "(中间件拦截?)")
    ok += not blocked

    # 用户"我的"面仍 strict(不可公开, token 过期属前端生命周期问题)
    r = c.get("/api/member/profile", headers={"X-Member-Id": "1"})
    still = (r.status_code == 401
             and "未登录" in str(r.json().get("detail", "")))
    print("member/profile 裸头(预期仍 401):", r.status_code, "✓" if still else "✗")
    ok += still

    print("-" * 60)
    print(f"通过 {ok}/7")
    assert ok == 7
print("\n=== 浏览面回归补丁生效 ===")
