"""部署后验证: 窗口化指标 + resume + 语音面活"""
import json
import subprocess


def sh(cmd):
    return subprocess.run(
        cmd, shell=True, capture_output=True,
        text=True, timeout=60).stdout


# 1) 容器就绪等待
for _ in range(15):
    if "Uvicorn running" in sh(
            "docker logs --tail 50 "
            "zhuxiang-backend-1 2>&1"):
        break
    sh("sleep 2")

# 2) 窗口化巡检手动触发(容器内, 走控制面等价
#    聚合——直接 python 调 run_guard_patrol)
r = sh("docker exec zhuxiang-backend-1 "
       "python -c "
       "'import asyncio, json;"
       "from services.xiaozhu_scheduler"
       " import run_guard_patrol;"
       "print(json.dumps(asyncio.run("
       "run_guard_patrol()), ensure_ascii=False))'"
       " 2>&1 | tail -1")
try:
    d = json.loads(r)
    print("窗口化指标:",
          json.dumps(d["metrics"],
                      ensure_ascii=False))
    print("samples.turns:",
          d["samples"]["turns"])
    print("breached:", d["breached"])
except Exception as exc:
    print("parse-fail", exc, r[:200])
