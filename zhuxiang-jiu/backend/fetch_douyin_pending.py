"""36号·拉取 RPA 待发布清单(多平台, douyin 首发消费用)

运行: python3 fetch_douyin_pending.py (生产服务器本机)
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                  headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


tok = call("POST", "/api/auth/login",
           body={"phone": "13800000002",
                 "password": "test123456"})["accessToken"]
rows = call("GET", "/api/promo/rpa/pending", tok)["data"] or []
print(json.dumps(rows, ensure_ascii=False, indent=2))
