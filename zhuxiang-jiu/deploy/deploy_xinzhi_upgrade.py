"""信值大模型检查升级·生产部署(scp→cp→build→.env→up→验证)

改动面(2026-10-03):
    backend: trust45_scan_scheduler(新)/main.py
    前端:    trust-dashboard + trust-risk-dashboard(html×2 未改,
             js×2 apiBase 同源默认修正——原 localhost 默认生产不可用)
"""
import subprocess
import sys
import time

HOST = "root@47.236.61.117"

FILES = [
    ("backend/services/trust45_scan_scheduler.py",
     "/opt/zhuxiang/zhuxiang-jiu/backend/services/"
     "trust45_scan_scheduler.py"),
    ("backend/main.py",
     "/opt/zhuxiang/zhuxiang-jiu/backend/main.py"),
    ("trust-dashboard.html",
     "/var/www/zxjiu/dist/trust-dashboard.html"),
    ("js/trust-dashboard.js",
     "/var/www/zxjiu/dist/js/trust-dashboard.js"),
    ("trust-risk-dashboard.html",
     "/var/www/zxjiu/dist/trust-risk-dashboard.html"),
    ("js/trust-risk-dashboard.js",
     "/var/www/zxjiu/dist/js/trust-risk-dashboard.js"),
]

ENV_ENSURE = """
cd /opt/zhuxiang
grep -q '^TRUST45_SCAN_AUTO=' .env 2>/dev/null && sed -i 's/^TRUST45_SCAN_AUTO=.*/TRUST45_SCAN_AUTO=on/' .env || echo 'TRUST45_SCAN_AUTO=on' >> .env
grep -E '^TRUST45_' .env
"""

BUILD = """
cd /opt/zhuxiang
docker compose build backend 2>&1 | tail -3
docker compose up -d backend 2>&1 | tail -3
"""

VERIFY = """
sleep 6
echo "=== A. 调度器启动与首轮扫描 ==="
docker logs zhuxiang-backend-1 --since 3m 2>&1 | grep -E "trust45_scan" | tail -4
echo "=== B. 快照键(容器内) ==="
docker exec zhuxiang-redis-1 redis-cli --strval-len 200 GET zhuxiang:trust45:daily_scan:last | head -c 300
echo ""
echo "=== C. 双看板可达 ==="
curl -s -o /dev/null -w "%{http_code}" https://zxjiu.com/trust-dashboard.html; echo ""
curl -s -o /dev/null -w "%{http_code}" https://zxjiu.com/trust-risk-dashboard.html; echo ""
curl -s -o /dev/null -w "%{http_code}" https://zxjiu.com/js/trust-dashboard.js; echo ""
curl -s -o /dev/null -w "%{http_code}" https://zxjiu.com/js/trust-risk-dashboard.js; echo ""
echo "=== D. js 同源默认(不应含 localhost 硬默认) ==="
curl -s https://zxjiu.com/js/trust-dashboard.js | grep -c "|| 'http://localhost:8000'" || echo 0
echo "=== E. 模式(容器内) ==="
docker exec zhuxiang-backend-1 python -c "
import asyncio
async def m():
    from services.trust45_mode_service import Trust45ModeService
    print(await Trust45ModeService().current_mode())
asyncio.run(m())
"
"""


def run(cmd, timeout=600):
    print("$", (cmd if isinstance(cmd, str)
                else " ".join(cmd))[:110])
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=timeout)
    if r.stdout.strip():
        print(r.stdout.strip()[:2500])
    if r.returncode != 0:
        print("[stderr]", r.stderr.strip()[:600])
        raise SystemExit("step failed")
    return r


def main():
    run(["ssh", HOST, "mkdir -p /opt/zhuxiang/deploy_tmp/xz"])
    for local, remote in FILES:
        run(["scp", local,
             f"{HOST}:/opt/zhuxiang/deploy_tmp/xz/"],
            timeout=120)
        name = remote.rsplit("/", 1)[-1]
        run(["ssh", HOST,
             f"cp /opt/zhuxiang/deploy_tmp/xz/{name} {remote}"])
    print("--- files placed ---")
    run(["ssh", HOST, ENV_ENSURE])
    run(["ssh", HOST, BUILD], timeout=900)
    time.sleep(8)
    run(["ssh", HOST, VERIFY], timeout=300)
    print("\n部署验证完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
