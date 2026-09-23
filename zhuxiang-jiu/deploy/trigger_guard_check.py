"""触发护栏巡检(真实数据重新判定——admin 控制面)"""
import json
import urllib.request

BASE = "http://localhost:8000"


def req(method, path, headers=None, body=None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(
        BASE + path, data=data, headers=h,
        method=method)
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read().decode())


tok = req("POST", "/api/auth/login", body={
    "phone": "13800000002",
    "password": "test123456"})
token = tok.get("accessToken") \
    or (tok.get("data") or {}).get("accessToken")
r = req("POST", "/api/xiaozhu/mode/guard",
        headers={"Authorization": f"Bearer {token}",
                 "X-Role": "admin"},
        body={})
print(json.dumps(r, ensure_ascii=False, indent=1)[:1500])
