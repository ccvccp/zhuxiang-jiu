"""36号·内容拒审(溯源违规内容的合规处置, 留痕审计)

用法: python3 reject_content.py <contentId> [reason]
运行: 生产服务器本机
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                  headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


cid = sys.argv[1]
reason = sys.argv[2] if len(sys.argv) > 2 else "溯源违规"
st, body = call("POST", "/api/auth/login",
                body={"phone": "13800000002",
                      "password": "test123456"})
tok = body.get("accessToken", "")
st, body = call("POST", f"/api/promo/contents/{cid}/review", tok,
                {"approved": False,
                 "reviewer": f"reject-{reason}"[:50]})
print(f"拒审 #{cid}: st={st} "
      f"status={(body.get('data') or {}).get('status')}")
