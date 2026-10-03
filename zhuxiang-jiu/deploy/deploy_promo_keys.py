"""推广通道凭证热配层·生产部署(2026-10-03 偏差②升级)"""
import subprocess
import sys
import time

HOST = "root@47.236.61.117"

FILES = [
    ("backend/services/promo_channel_service.py",
     "/opt/zhuxiang/zhuxiang-jiu/backend/services/"
     "promo_channel_service.py"),
    ("backend/routes/promo_routes.py",
     "/opt/zhuxiang/zhuxiang-jiu/backend/routes/promo_routes.py"),
]

BUILD = """
cd /opt/zhuxiang
docker compose build backend 2>&1 | tail -2
docker compose up -d backend 2>&1 | tail -2
"""

VERIFY = """
sleep 7
echo "=== A. 热配端点在位(admin 门控, 匿名 403 为预期) ==="
curl -s -o /dev/null -w "%{http_code}" https://zxjiu.com/api/promo/channels/keys; echo ""
echo "=== B. 容器内服务层验证(runtime>env 分层+视图) ==="
docker exec zhuxiang-backend-1 python -c "
import asyncio
async def m():
    from services.promo_channel_service import (
        PromoChannelService, set_runtime_key, clear_runtime_key,
        channel_key_async)
    v = await PromoChannelService().channel_keys_view()
    print('mode=', v['mode'], 'missingApiKeys=', v['missingApiKeys'])
    await set_runtime_key('weibo', 'prod-hot-key-4321')
    print('runtime key ->', (await channel_key_async('weibo'))[-4:])
    await clear_runtime_key('weibo')
    print('cleared -> empty:', (await channel_key_async('weibo')) == '')
asyncio.run(m())
"
echo "=== C. 容器健康 ==="
docker ps --format '{{.Names}} {{.Status}}' | grep zhuxiang-backend
"""


def run(cmd, timeout=900):
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=timeout)
    print(r.stdout)
    if r.returncode != 0:
        print("[stderr]", r.stderr.strip()[:600])
        raise SystemExit("step failed")
    return r


def main():
    run(["ssh", HOST, "mkdir -p /opt/zhuxiang/deploy_tmp/p36k"])
    for local, remote in FILES:
        run(["scp", local,
             f"{HOST}:/opt/zhuxiang/deploy_tmp/p36k/"], timeout=120)
        name = remote.rsplit("/", 1)[-1]
        run(["ssh", HOST,
             f"cp /opt/zhuxiang/deploy_tmp/p36k/{name} {remote}"])
    run(["ssh", HOST, BUILD])
    time.sleep(3)
    run(["ssh", HOST, VERIFY], timeout=300)
    print("\n部署验证完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
