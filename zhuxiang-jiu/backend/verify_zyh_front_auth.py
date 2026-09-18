"""zyh.html 401 自愈链等价验证(模拟 apiFetch: 无效 token → 401 → refresh → retry)"""
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

# 1. 登录
code, body = call("/api/auth/login", {"phone": "13800000002",
                                      "password": "test123456"})
print(f"1. login: {code} {'✓' if code == 200 else '✗'}")
ok &= code == 200
refresh_token = body.get("refreshToken")

# 2. 无效 token 调 chat → 应 401
code, _ = call("/api/zyh/chat", {"prompt": "竹香是什么"}, token="invalid.token.here")
print(f"2. 无效token chat: {code} {'✓' if code == 401 else '✗'}")
ok &= code == 401

# 3. refresh 换新
code, body = call("/api/auth/refresh", {"refreshToken": refresh_token})
print(f"3. refresh: {code} {'✓' if code == 200 and body.get('accessToken') else '✗'}")
ok &= bool(code == 200 and body.get("accessToken"))

# 4. 新 token 重试 chat → 应 200
code, body = call("/api/zyh/chat", {"prompt": "竹香是什么"},
                  token=body.get("accessToken"))
zyh_mode = body.get("zyhMode")
print(f"4. 自愈重试 chat: {code} zyhMode={zyh_mode} "
      f"{'✓' if code == 200 else '✗'}")
ok &= code == 200

print("=" * 40)
print("401 自愈链等价验证:", "全部通过" if ok else "存在失败")
raise SystemExit(0 if ok else 1)
