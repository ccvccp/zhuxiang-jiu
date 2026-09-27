"""36号·RPA 发布回执登记(平台无关, 命令行参数化)

用法:
    python3 register_rpa_receipt.py <contentId> <noteUrl> <noteId>

示例:
    python3 register_rpa_receipt.py 33 \
        https://www.xiaohongshu.com/explore/6ab87b7e000000001500ebfa \
        6ab87b7e000000001500ebfa

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


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    cid, note_url, note_id = sys.argv[1], sys.argv[2], sys.argv[3]
    st, body = call("POST", "/api/auth/login",
                    body={"phone": "13800000002",
                          "password": "test123456"})
    tok = body.get("accessToken", "")
    st, body = call("POST", f"/api/promo/rpa/{cid}/receipt", tok,
                    {"noteUrl": note_url, "noteId": note_id,
                     "error": ""})
    receipt = ((body.get("data") or {}).get("receipt") or {})
    ok = (st == 200 and receipt.get("mode") == "rpa"
          and receipt.get("url") == note_url)
    print("receipt:", st,
          json.dumps(body, ensure_ascii=False)[:300])
    print("登记校验:", "OK mode=rpa+url" if ok else "FAIL")
    st2, body2 = call("GET", "/api/promo/rpa/pending", tok)
    left = body2.get("data") or []
    print(f"剩余待发布: {len(left)} 条 "
          f"{[r.get('platform') for r in left]}")
    return 0 if ok and not left else 1


if __name__ == "__main__":
    sys.exit(main())
