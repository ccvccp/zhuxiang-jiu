"""偏差③执行: LLM_MODEL_PROMO glm-4-flash → glm-5.3(设计默认)

依据: 双模型生产实测均可用; glm-5.3 为设计默认+质量基线 85(vs
4-flash 75)+输出无 markdown 围栏; 三级降级链(5.3→4-flash→规则)
天然保底; 内容量小(决策门控+日上限5)成本差可忽略。
"""
import subprocess
import time

HOST = "root@47.236.61.117"

CHANGE = """
cd /opt/zhuxiang
cp .env .env.bak-promo36-llm-20261003
sed -i 's/^LLM_MODEL_PROMO=.*/LLM_MODEL_PROMO=glm-5.3/' .env
grep -n '^LLM_MODEL_PROMO=' .env
docker compose up -d --force-recreate backend 2>&1 | tail -2
"""

VERIFY = """
sleep 7
echo "=== A. 生效值(容器内模块常量) ==="
docker exec zhuxiang-backend-1 python -c "
from repositories.promo_repository import (
    PROMO_LLM_MODEL, PROMO_LLM_FALLBACK_MODEL)
print('主档:', PROMO_LLM_MODEL, '| 备档:', PROMO_LLM_FALLBACK_MODEL)
assert PROMO_LLM_MODEL == 'glm-5.3'
assert PROMO_LLM_FALLBACK_MODEL == 'glm-4-flash'
"
echo "=== B. 容器健康 ==="
docker ps --format '{{.Names}} {{.Status}}' | grep zhuxiang-backend
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
    print("LLM 主档切换完成: glm-4-flash → glm-5.3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
