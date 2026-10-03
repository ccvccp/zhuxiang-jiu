"""推广 AI 审线调整: 101 → 80(设计默认, 运营裁决 2026-10-03)

该值为 promo_repository 模块级常量(导入期读 env), 须重建容器.
"""
import subprocess
import sys
import time

HOST = "root@47.236.61.117"

CHANGE = """
cd /opt/zhuxiang
cp .env .env.bak-promo36-score-20261003
grep -n '^PROMO_COMPLIANCE_PASS_SCORE=' .env
sed -i 's/^PROMO_COMPLIANCE_PASS_SCORE=.*/PROMO_COMPLIANCE_PASS_SCORE=80/' .env
grep -n '^PROMO_COMPLIANCE_PASS_SCORE=' .env
docker compose up -d --force-recreate backend 2>&1 | tail -2
"""

VERIFY = """
sleep 7
echo "=== A. 生效值(容器内模块常量) ==="
docker exec zhuxiang-backend-1 python -c "
from repositories.promo_repository import PROMO_COMPLIANCE_PASS_SCORE as S
print('PASS_SCORE =', S)
assert S == 80, S
"
echo "=== B. 容器健康 ==="
docker ps --format '{{.Names}} {{.Status}}' | grep zhuxiang-backend
echo "=== C. 调度器全部在位 ==="
docker logs zhuxiang-backend-1 --since 2m 2>&1 | grep -cE "scheduler started" || echo 0
docker logs zhuxiang-backend-1 --since 2m 2>&1 | grep -E "scheduler started" | tail -8
"""


def run(script, timeout=600):
    r = subprocess.run(["ssh", HOST, script],
                       capture_output=True, text=True,
                       timeout=timeout)
    print(r.stdout)
    if r.returncode != 0:
        print("[stderr]", r.stderr.strip()[:600])
        raise SystemExit("step failed")
    return r


def main():
    run(CHANGE)
    time.sleep(3)
    run(VERIFY, timeout=300)
    print("AI 审线调整完成: 101 → 80")
    return 0


if __name__ == "__main__":
    sys.exit(main())
