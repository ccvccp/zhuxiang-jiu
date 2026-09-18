"""location stores 白名单验证: 无 token 游客可达"""
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15) as c:
    r = c.get("/api/location/stores/nearby",
              params={"longitude": "117.12", "latitude": "36.66",
                      "radius_km": 10})
    print("stores/nearby 无头:", r.status_code)
    blocked = (r.status_code == 401
               and "未登录" in str(r.json().get("detail", "")))
    assert not blocked, r.text
    r = c.get("/api/location/stores/1")
    print("stores/1 无头:", r.status_code)
    blocked = (r.status_code == 401
               and "未登录" in str(r.json().get("detail", "")))
    assert not blocked, r.text
    # 对照: 管理面仍 strict
    r = c.get("/api/location/agents/nearby",
              params={"longitude": "117.12", "latitude": "36.66"})
    print("agents/nearby 无头(用户画像面, 预期 401):", r.status_code)
    assert r.status_code == 401
print("\n=== location stores 游客浏览面恢复 ===")
