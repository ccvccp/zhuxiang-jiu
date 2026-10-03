"""交付后首夜稳态巡检 + 三模型调度器状态检查(2026-10-03 晚)

巡检面:
    1. 公开健康端点(HTTPS 直连)
    2. 智搜公开决策面冒烟(query/合规拦截)
    3. 六个新看板页面可达性
    4. SSH 服务器侧: 容器状态 / 错误日志扫描(300m) /
       三模型 scan scheduler 启动与留痕
"""
import json
import subprocess
import urllib.request

BASE = "https://zxjiu.com"
HOST = "root@47.236.61.117"
PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  OK {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} -- {str(detail)[:200]}")


def get(path, timeout=20):
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def post_json(path, payload, timeout=30):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main():
    # --- 1. 健康端点 ---
    for p in ("/api/monitor/health", "/api/decision/health",
              "/api/maintenance/health"):
        try:
            code, body = get(p)
            check(f"健康-{p}", code == 200, f"code={code}")
        except Exception as e:
            check(f"健康-{p}", False, e)

    # --- 2. 智搜冒烟 ---
    try:
        d = post_json("/api/search-ai/query",
                      {"text": "竹奕酒多少钱"}).get("data", {})
        check("智搜-决策面冒烟", bool(d.get("answer", "")),
              f"intent={d.get('intent')}")
        d = post_json("/api/search-ai/query",
                      {"text": "未成年人能买酒吗"}).get("data", {})
        check("智搜-合规拦截", d.get("intent") == "blocked",
              f"intent={d.get('intent')}")
    except Exception as e:
        check("智搜-冒烟", False, e)

    # --- 3. 看板可达 ---
    for page in ("legal-dashboard.html", "zhiyun-dashboard.html",
                 "zhisou-dashboard.html", "zy-dashboard.html",
                 "zhidan-dashboard.html", "zhike-dashboard.html"):
        try:
            code, _ = get(f"/{page}", timeout=15)
            check(f"看板-{page}", code == 200, f"code={code}")
        except Exception as e:
            check(f"看板-{page}", False, e)

    # --- 4. 服务器侧: 容器/日志/调度器 ---
    script = r'''
echo "=== A. 容器状态 ==="
docker ps --format "{{.Names}} {{.Status}}" | grep zhuxiang
echo ""
echo "=== B. 错误日志扫描(300m) ==="
docker logs zhuxiang-backend-1 --since 300m 2>&1 | grep -cE "Traceback|ERROR| 500 " || echo 0
docker logs zhuxiang-backend-1 --since 300m 2>&1 | grep -E "Traceback|ERROR" | tail -5
echo ""
echo "=== C. 三模型 scan scheduler ==="
docker logs zhuxiang-backend-1 --since 600m 2>&1 | grep -E "zy_scan|zd_scan|zk_scan" | tail -9
echo ""
echo "=== D. 首轮留痕仍在(计数) ==="
docker exec zhuxiang-redis-1 redis-cli --no-raw KEYS "zy_logs*" | head -3
docker exec zhuxiang-redis-1 redis-cli LLEN zhixiaoyuan:zy_logs 2>/dev/null || true
docker exec zhuxiang-redis-1 redis-cli --scan --pattern "*zd_checkups*" | head -3
docker exec zhuxiang-redis-1 redis-cli --scan --pattern "*zk_churn*" | head -3
'''
    try:
        r = subprocess.run(["ssh", HOST, script],
                           capture_output=True, text=True, timeout=180)
        print(r.stdout)
        if r.stderr.strip():
            print("[stderr]", r.stderr[:300])
        check("SSH-服务器侧巡检完成", True)
    except Exception as e:
        check("SSH-服务器侧巡检完成", False, e)

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
