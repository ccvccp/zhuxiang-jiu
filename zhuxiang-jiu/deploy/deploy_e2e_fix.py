"""E2E 修复部署: 链专用超时(llm_client timeout 参数 + agent
LLM_TIMEOUT_PROMO=120) + .env 显式配置"""
import subprocess
import time

HOST = "root@47.236.61.117"

FILES = [
    ("backend/services/llm_client.py",
     "/opt/zhuxiang/zhuxiang-jiu/backend/services/llm_client.py"),
    ("backend/services/promo_agent_service.py",
     "/opt/zhuxiang/zhuxiang-jiu/backend/services/"
     "promo_agent_service.py"),
]

ENV_ADD = """
cd /opt/zhuxiang
grep -q '^LLM_TIMEOUT_PROMO=' .env 2>/dev/null || echo 'LLM_TIMEOUT_PROMO=120' >> .env
grep -nE '^LLM_TIMEOUT' .env
docker compose build backend 2>&1 | tail -2
docker compose up -d backend 2>&1 | tail -2
"""


def run(cmd, timeout=900):
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=timeout)
    print(r.stdout)
    if r.returncode != 0:
        print("[stderr]", r.stderr.strip()[:500])
        raise SystemExit("step failed")
    return r


def main():
    run(["ssh", HOST, "mkdir -p /opt/zhuxiang/deploy_tmp/e2e"])
    for local, remote in FILES:
        run(["scp", local,
             f"{HOST}:/opt/zhuxiang/deploy_tmp/e2e/"],
            timeout=120)
        name = remote.rsplit("/", 1)[-1]
        run(["ssh", HOST,
             f"cp /opt/zhuxiang/deploy_tmp/e2e/{name} {remote}"])
    run(["ssh", HOST, ENV_ADD])
    time.sleep(8)
    print("部署完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
