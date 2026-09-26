"""36号·内容生产周期收尾(2026-09-27)

① 拒审 #32(GLM 轨溯源违规: 引用池外标准编号 GB/T 10781,
   三审闸门 409 拦截的合规处置)
② 启用 #24(存量 pending douyin 内容, 文案已过脚本标记清洗)
   过审入队出队 → rpa_pending 补齐抖音位

运行: python3 finalize_cycle_0927.py (生产服务器本机)
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "http://127.0.0.1:8000"
REVIEWER = "queue-cycle-0927"


def call(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                  headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"detail": f"{type(e).__name__}: {e}"}


def main():
    st, body = call("POST", "/api/auth/login",
                    body={"phone": "13800000002",
                          "password": "test123456"})
    token = body.get("accessToken", "")
    if not token:
        print("登录失败")
        return 1

    # ① 拒审 #32(留痕 GLM 轨溯源违规)
    st, body = call("GET", "/api/promo/contents/32", token)
    c32 = body.get("data") or {}
    print(f"#32 [{c32.get('platform')}] provenanceViolations="
          f"{c32.get('provenanceViolations')}")
    st, body = call("POST", "/api/promo/contents/32/review", token,
                    {"approved": False,
                     "reviewer": f"{REVIEWER}-reject"})
    print(f"拒审 #32: st={st} status={(body.get('data') or {}).get('status')}")

    # ② 启用 #24: 展示全文 → 过审 → 入队(立即到期) → 出队
    st, body = call("GET", "/api/promo/contents/24", token)
    c24 = body.get("data") or {}
    print(f"\n--- #24 [{c24.get('platform')}] "
          f"compliance={c24.get('complianceScore')} "
          f"copyHygieneAt={c24.get('copyHygieneAt', '')[:19]} ---")
    print(f"标题: {c24.get('title', '')}")
    print(f"正文: {c24.get('body', '')}")
    print(f"话题: {c24.get('hashtags', '')}")
    st, _ = call("POST", "/api/promo/contents/24/review", token,
                 {"approved": True, "reviewer": REVIEWER})
    print(f"过审 #24: st={st}")
    if st != 200:
        return 1
    past = (datetime.now(timezone.utc)
            - timedelta(minutes=1)).isoformat()
    st, _ = call("POST", "/api/promo/contents/24/publish", token,
                 {"publishAt": past})
    print(f"入队 #24: st={st} (409=单日上限)")
    if st != 200:
        return 1
    st, body = call("POST", "/api/promo/publish/process", token)
    for p in (body.get("data") or []):
        r = p.get("receipt") or {}
        print(f"出队 #{p.get('contentId')} [{p.get('platform')}] "
              f"mode={r.get('mode')}")

    # ③ 清单终态
    st, body = call("GET", "/api/promo/rpa/pending", token)
    rows = body.get("data") or []
    print(f"\nrpa_pending 清单: {len(rows)} 条")
    for row in rows:
        print(f"  #{row['contentId']} [{row['platform']}] "
              f"{row['title']}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
