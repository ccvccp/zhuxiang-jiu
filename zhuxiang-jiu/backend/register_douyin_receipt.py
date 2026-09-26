"""36号·抖音 RPA 发布回执登记(当前批次 → mode=rpa+作品URL)

2026-09-27 #24: 存量清洗内容补位发布(脚本标记治理后首个
双平台周期, 抖音位)。历史: #30 重发版见 git log。
运行: python3 register_douyin_receipt.py (生产服务器本机)
"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
CID = 24
NOTE_URL = "https://www.douyin.com/note/7689922787007270154"
NOTE_ID = "7689922787007270154"


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
        return e.code, json.loads(e.read().decode() or "{}")


tok = call("POST", "/api/auth/login",
           body={"phone": "13800000002",
                 "password": "test123456"})[1]["accessToken"]

st, body = call("POST", f"/api/promo/rpa/{CID}/receipt", tok,
                {"noteUrl": NOTE_URL, "noteId": NOTE_ID,
                 "error": ""})
print("receipt:", st, json.dumps(body, ensure_ascii=False))
receipt = ((body.get("data") or {}).get("receipt") or {})
ok = (st == 200 and receipt.get("mode") == "rpa"
      and receipt.get("url") == NOTE_URL)
print("登记校验:", "OK mode=rpa+url" if ok else "FAIL")

st2, body2 = call("GET", "/api/promo/rpa/pending", tok)
left = body2.get("data") or []
print(f"剩余待发布: {len(left)} 条 "
      f"{[r.get('platform') for r in left]}")
raise SystemExit(0 if ok and not left else 1)
