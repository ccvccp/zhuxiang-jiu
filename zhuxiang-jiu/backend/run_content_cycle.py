"""36号·内容生产周期执行(雷达→生成→过审→入队→rpa_pending)

一源多态: 同一热点生成 douyin + xiaohongshu 双平台内容, 过审
入队出队后停留 rpa_pending, 等待「发布抖音待发内容」/「发布
小红书待发内容」触发真实发布。全程输出完整文案供人工复核
(文案防线: 脚本标记已在生成出口剥除)。

运行: python3 run_content_cycle.py (生产服务器本机)
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "http://127.0.0.1:8000"
PLATFORMS = ["douyin", "xiaohongshu"]
REVIEWER = "queue-cycle-0927"


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
        return 0, {"detail": f"{type(e).__name__}: {e}"}


def main():
    st, body = call("POST", "/api/auth/login",
                    body={"phone": "13800000002",
                          "password": "test123456"})
    token = body.get("accessToken") \
        or (body.get("data") or {}).get("accessToken", "")
    if not token:
        print("登录失败:", st, str(body)[:120])
        return 1

    # 0. 回收超时残效: 近 30 分钟生成的 pending 内容直接复用
    #    (生成请求客户端超时后服务端可能已完成并落库, 重试会
    #     重复消耗热点冷却配额)
    st, body = call("GET",
                    "/api/promo/contents?status=pending&limit=20",
                    token)
    fresh = [c for c in (body.get("data") or [])
             if str(c.get("createdAt", "")) >=
             (datetime.now(timezone.utc)
              - timedelta(minutes=30)).isoformat()[:16]]
    contents = fresh or None
    if contents:
        print(f"复用超时批次残效内容: "
              f"{[c['contentId'] for c in contents]}")

    # 1. 雷达扫描(冷却期 409 容忍——调度器 15min 同源)
    if not contents:
        st, body = call("POST", "/api/promo/radar/scan", token)
        print(f"雷达扫描: st={st} "
              f"{str(body.get('error') or '')[:60]}")

    # 2. 遍历 engaged 热点生成(热点 48h 冷却上限 2 条;
    #    双平台 GLM 四步链耗时较长, 300s 超时)
    st, body = call("GET", "/api/promo/radar/hotspots?status=engaged",
                     token)
    hotspots = body.get("data") or []
    print(f"engaged 热点: {len(hotspots)} 个")
    for hs in (hotspots[:8] if not contents else []):
        st, body = call("POST", "/api/promo/contents/generate", token,
                        {"hotspotId": hs["hotspotId"],
                         "platforms": PLATFORMS}, timeout=300)
        if st == 200:
            data = body.get("data")
            if isinstance(data, dict):
                data = (data.get("contents")
                        or data.get("items") or [data])
            if data:
                contents = data
                print(f"热点 #{hs['hotspotId']} "
                      f"《{hs.get('title', '')[:24]}》 生成 "
                      f"{len(data)} 条")
                break
        detail = str(body.get("error") or body.get("detail") or body)[:80]
        print(f"热点 #{hs['hotspotId']} 不可用: {detail}")
    if not contents:
        print("无可用热点(冷却满额)或生成失败")
        return 1

    # 3. 逐条: 展示完整文案 → 过审 → 入队(立即到期)
    past = (datetime.now(timezone.utc)
            - timedelta(minutes=1)).isoformat()
    queued = []
    for c in contents:
        print(f"\n--- 内容 #{c['contentId']} [{c['platform']}] "
              f"compliance={c.get('complianceScore')} ---")
        print(f"标题: {c.get('title', '')}")
        print(f"正文: {c.get('body', '')}")
        print(f"话题: {c.get('hashtags', '')}")
        st, _ = call("POST",
                     f"/api/promo/contents/{c['contentId']}/review",
                     token,
                     {"approved": True, "reviewer": REVIEWER})
        if st != 200:
            print(f"!! 过审失败 st={st}")
            continue
        st, _ = call("POST",
                     f"/api/promo/contents/{c['contentId']}/publish",
                     token, {"publishAt": past})
        if st != 200:
            print(f"!! 入队失败 st={st} (409=单日上限)")
            continue
        queued.append(c["contentId"])
    if not queued:
        print("\n无可入队内容")
        return 1

    # 4. 出队(rpa_pending 回执)
    st, body = call("POST", "/api/promo/publish/process", token)
    for p in (body.get("data") or []):
        r = p.get("receipt") or {}
        print(f"出队 #{p.get('contentId')} [{p.get('platform')}] "
              f"mode={r.get('mode')}")

    # 5. 待发清单终态
    st, body = call("GET", "/api/promo/rpa/pending", token)
    rows = body.get("data") or []
    print(f"\nrpa_pending 清单: {len(rows)} 条")
    for row in rows:
        print(f"  #{row['contentId']} [{row['platform']}] "
              f"{row['title']}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
