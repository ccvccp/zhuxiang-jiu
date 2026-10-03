"""智能推广(36号)检查升级·生产部署

改动面(2026-10-03):
    前端: ai-promo-radar/studio(html×2 未改, js×2 apiBase 同源
          默认修正)——四文件首次部署生产(404→200)
    env:  PROMO_CHANNEL_MODE 去重(双键 mock+real, 后者生效;
          去脏保留 real 现状值, 行为零变化)
"""
import subprocess
import sys

HOST = "root@47.236.61.117"

FILES = [
    ("ai-promo-radar.html",
     "/var/www/zxjiu/dist/ai-promo-radar.html"),
    ("ai-promo-studio.html",
     "/var/www/zxjiu/dist/ai-promo-studio.html"),
    ("js/promo-radar.js",
     "/var/www/zxjiu/dist/js/promo-radar.js"),
    ("js/promo-studio.js",
     "/var/www/zxjiu/dist/js/promo-studio.js"),
]

ENV_DEDUP = """
cd /opt/zhuxiang
cp .env .env.bak-promo36-20261003
# 去重: 仅保留最后生效的 PROMO_CHANNEL_MODE(real 现状), 删除
# 前面的重复 mock 行(行为零变化——env 解析本就后者生效)
sed -i '0,/^PROMO_CHANNEL_MODE=mock$/{/^PROMO_CHANNEL_MODE=mock$/d}' .env
grep -n 'PROMO_CHANNEL_MODE' .env
"""

VERIFY = """
echo "=== A. 四看板可达 ==="
curl -s -o /dev/null -w "%{http_code}" https://zxjiu.com/ai-promo-radar.html; echo ""
curl -s -o /dev/null -w "%{http_code}" https://zxjiu.com/ai-promo-studio.html; echo ""
curl -s -o /dev/null -w "%{http_code}" https://zxjiu.com/js/promo-radar.js; echo ""
curl -s -o /dev/null -w "%{http_code}" https://zxjiu.com/js/promo-studio.js; echo ""
echo "=== B. js 同源默认(不应有 localhost 硬默认) ==="
curl -s https://zxjiu.com/js/promo-radar.js | grep -c "|| 'http://localhost:8000'" || echo 0
echo "=== C. 调度器持续运转(雷达轮次) ==="
docker logs zhuxiang-backend-1 --since 60m 2>&1 | grep -E "promo_radar_scheduled|promo_publish_scheduled" | tail -4
echo "=== D. 留痕量复核 ==="
docker exec zhuxiang-redis-1 sh -c "redis-cli --scan --pattern 'zhuxiang:promo:promo_hotspots:*' | wc -l"
docker exec zhuxiang-redis-1 sh -c "redis-cli --scan --pattern 'zhuxiang:promo:promo_decisions:*' | wc -l"
"""


def run(cmd, timeout=600):
    print("$", (cmd if isinstance(cmd, str)
                else " ".join(cmd))[:110])
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=timeout)
    if r.stdout.strip():
        print(r.stdout.strip()[:2000])
    if r.returncode != 0:
        print("[stderr]", r.stderr.strip()[:600])
        raise SystemExit("step failed")
    return r


def main():
    run(["ssh", HOST, "mkdir -p /opt/zhuxiang/deploy_tmp/p36"])
    for local, remote in FILES:
        run(["scp", local,
             f"{HOST}:/opt/zhuxiang/deploy_tmp/p36/"],
            timeout=120)
        name = remote.rsplit("/", 1)[-1]
        run(["ssh", HOST,
             f"cp /opt/zhuxiang/deploy_tmp/p36/{name} {remote}"])
    print("--- files placed ---")
    run(["ssh", HOST, ENV_DEDUP])
    run(["ssh", HOST, VERIFY], timeout=300)
    print("\n部署验证完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
