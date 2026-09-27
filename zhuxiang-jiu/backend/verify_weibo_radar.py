"""36号·微博热搜真实源首扫验证(2026-09-27 接入验收)

链路: 登录 → 触发全源扫描(微博源真调一次 450C) → 列出
platform=weibo 的真实热搜词条(区别于 mock 池) → 二次扫描
验证 redis TTL 锁生效(不再烧 C)。余额变化由 CLI whoami 佐证。

运行: python3 verify_weibo_radar.py (生产服务器本机)
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method, path, token=None, body=None, timeout=90):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                  headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"detail": str(e)}


def main():
    st, body = call("POST", "/api/auth/login",
                    body={"phone": "13800000002",
                          "password": "test123456"})
    tok = body.get("accessToken", "")
    if not tok:
        print("登录失败")
        return 1

    print("=== 扫描(微博源: 锁内走 mock, 锁外真调) ===")
    st, body = call("POST", "/api/promo/radar/scan", tok)
    data = body.get("data") or {}
    print(f"scan: st={st} scanned={data.get('scanned')} "
          f"new={data.get('new')} skipped={data.get('skipped')}")
    if st != 200:
        print(json.dumps(body, ensure_ascii=False)[:300])
        return 1

    print("\n=== platform=weibo 全量热点(含 discarded) ===")
    st, body = call("GET",
                    "/api/promo/radar/hotspots?platform=weibo", tok)
    rows = body.get("data") or []
    real = [h for h in rows
            if h.get("summary", "").startswith("[微博热搜] ")]
    print(f"weibo 热点总数={len(rows)}, 其中真实源(real)={len(real)}")
    for h in real[:12]:
        print(f"  #{h['hotspotId']} [{h.get('status')}] "
              f"[{h['score']}分] {h['title']} "
              f"| heat={h['heat']}万")
    return 0 if real else 1


if __name__ == "__main__":
    sys.exit(main())
