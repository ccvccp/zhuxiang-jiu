"""zjian.html 401 自愈链等价验证"""
import json
import urllib.request

BASE = "http://127.0.0.1:8001"


def call(path, payload=None, token=None):
    req = urllib.request.Request(BASE + path)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            body = json.loads(body)
        except Exception:
            body = {}
        return e.code, body


ok = True
code, body = call("/api/auth/login", {"phone": "13800000002",
                                      "password": "test123456"})
print(f"1. login: {code} {'✓' if code == 200 else '✗'}")
ok &= code == 200
refresh_token = body.get("refreshToken")

code, _ = call("/api/zjian/verify", {"question": "酒精度多少"},
               token="invalid.token.here")
print(f"2. 无效token verify: {code} {'✓' if code == 401 else '✗'}")
ok &= code == 401

code, body = call("/api/auth/refresh",
                   {"refreshToken": refresh_token})
print(f"3. refresh: {code} "
      f"{'✓' if code == 200 and body.get('accessToken') else '✗'}")
ok &= bool(code == 200 and body.get("accessToken"))

code, body = call("/api/zjian/verify", {"question": "酒精度多少"},
                  token=body.get("accessToken"))
print(f"4. 自愈重试 verify: {code} zjianMode={body.get('zjianMode')} "
      f"{'✓' if code == 200 else '✗'}")
ok &= code == 200

print("=" * 40)
print("zjian 401 自愈链等价验证:",
      "全部通过" if ok else "存在失败")
raise SystemExit(0 if ok else 1)
