"""智码四件套升级·生产部署(scp→deploy_tmp→cp→build→.env→up)

改动面(2026-10-03):
    backend: qr70_mode_service(新)/qr70_scan_scheduler(新)/
             qr70_hub_service/qr70_routes/main/order_service
    前端:    qr70-dashboard.html + js/qr70-dashboard.js(新)
    测试:    test_zhima_upgrade(新) + qr70_p0~p8 自举(部署不带)
"""
import subprocess
import sys
import time

HOST = "root@47.236.61.117"

# (本地路径, 服务器目标绝对路径)
FILES = [
    ("backend/services/qr70_mode_service.py",
     "/opt/zhuxiang/zhuxiang-jiu/backend/services/qr70_mode_service.py"),
    ("backend/services/qr70_scan_scheduler.py",
     "/opt/zhuxiang/zhuxiang-jiu/backend/services/qr70_scan_scheduler.py"),
    ("backend/services/qr70_hub_service.py",
     "/opt/zhuxiang/zhuxiang-jiu/backend/services/qr70_hub_service.py"),
    ("backend/routes/qr70_routes.py",
     "/opt/zhuxiang/zhuxiang-jiu/backend/routes/qr70_routes.py"),
    ("backend/main.py",
     "/opt/zhuxiang/zhuxiang-jiu/backend/main.py"),
    ("backend/services/order_service.py",
     "/opt/zhuxiang/zhuxiang-jiu/backend/services/order_service.py"),
    ("qr70-dashboard.html",
     "/var/www/zxjiu/dist/qr70-dashboard.html"),
    ("js/qr70-dashboard.js",
     "/var/www/zxjiu/dist/js/qr70-dashboard.js"),
]

ENV_ENSURE = """
cd /opt/zhuxiang
grep -q '^QR70_MODE=' .env 2>/dev/null && sed -i 's/^QR70_MODE=.*/QR70_MODE=assist/' .env || echo 'QR70_MODE=assist' >> .env
grep -q '^QR70_SCAN_AUTO=' .env 2>/dev/null && sed -i 's/^QR70_SCAN_AUTO=.*/QR70_SCAN_AUTO=on/' .env || echo 'QR70_SCAN_AUTO=on' >> .env
grep -E '^QR70_' .env
"""

BUILD = """
cd /opt/zhuxiang
docker compose build backend 2>&1 | tail -3
docker compose up -d backend 2>&1 | tail -3
"""

VERIFY = """
sleep 6
echo "=== A. 调度器启动日志 ==="
docker logs zhuxiang-backend-1 --since 3m 2>&1 | grep -E "qr70_scan|Started" | tail -5
echo "=== B. 首轮扫描留痕(daily_scan) ==="
docker exec zhuxiang-redis-1 redis-cli --scan --pattern "*qr70:event*" | head -3
docker exec zhuxiang-redis-1 redis-cli --scan --pattern "*qr70:event*" | wc -l
echo "=== C. 模式读取(容器内) ==="
docker exec zhuxiang-backend-1 python -c "
import asyncio
async def m():
    from services.qr70_mode_service import current_mode
    print(await current_mode())
asyncio.run(m())
"
echo "=== D. 手动扫描触发(容器内) ==="
docker exec zhuxiang-backend-1 python -c "
import asyncio
async def m():
    from services.qr70_scan_scheduler import run_scan
    s = await run_scan()
    print('scan ok, codes=', s.get('codeSnapshot', {}).get('total'))
asyncio.run(m())
"
echo "=== E. 看板页面可达 ==="
curl -s -o /dev/null -w "%{http_code}" https://zxjiu.com/qr70-dashboard.html
echo ""
curl -s -o /dev/null -w "%{http_code}" https://zxjiu.com/js/qr70-dashboard.js
echo ""
"""


def run(cmd, **kw):
    print("$", (cmd if isinstance(cmd, str)
                else " ".join(cmd))[:120])
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=kw.get("timeout", 600))
    if r.stdout.strip():
        print(r.stdout.strip()[:3000])
    if r.returncode != 0:
        print("[stderr]", r.stderr.strip()[:800])
        raise SystemExit(f"step failed: {cmd[:80]}")
    return r


def main():
    # 1. scp 上传(经 deploy_tmp)
    run(["ssh", HOST, "mkdir -p /opt/zhuxiang/deploy_tmp/zm"])
    for local, remote in FILES:
        run(["scp", local, f"{HOST}:/opt/zhuxiang/deploy_tmp/zm/"],
            timeout=120)
        name = remote.rsplit("/", 1)[-1]
        run(["ssh", HOST,
             f"cp /opt/zhuxiang/deploy_tmp/zm/{name} {remote}"])
    print("--- files placed ---")

    # 2. .env 配置
    run(["ssh", HOST, ENV_ENSURE])

    # 3. build + up
    run(["ssh", HOST, BUILD], timeout=900)
    time.sleep(8)

    # 4. 验证
    run(["ssh", HOST, VERIFY], timeout=300)
    print("\n部署验证完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
