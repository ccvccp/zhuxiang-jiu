"""智搜观测数据采集(生产, 服务器侧 docker exec)——首次日报数据源

采集: status / intent-stats / decisions / feedbacks / bad-cases /
      ab-stats / ab-decisions / mode
通道: 容器内 AuthService 登录 admin → Bearer 调观测面
      (base64 传输规避多层引号剥离)
"""
import base64
import json
import os
import subprocess

HOST = "root@47.236.61.117"

# 凭证治理(2026-10-17): 明文默认已移除, 须 env 注入
# (经 .env 或环境变量; 键名对齐 backend/.env.example 第七节)
PHONE = os.environ.get("PROD_ADMIN_PHONE", "").strip()
PWD = os.environ.get("PROD_ADMIN_PASSWORD", "").strip()
# 自有 env 凭证, 仅放行数字/字母(拼入远端脚本, 防注入)
if not (PHONE and PWD and PHONE.isdigit() and PWD.isalnum()):
    raise SystemExit(
        "PROD_ADMIN_PHONE/PROD_ADMIN_PASSWORD 未配置或含非法字符"
        "(须纯数字手机号/字母数字密码; 见 backend/.env.example)")

INNER = r'''
import asyncio, json, urllib.request

BASE = "http://localhost:8000"

def http(path, token):
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

async def mint():
    from services.auth_service import AuthService
    try:
        r = await AuthService().register(
            phone="__PHONE__", password="__PWD__", role="admin")
    except ValueError:
        r = await AuthService().login("__PHONE__", "__PWD__")
    return r.get("accessToken", "")

token = asyncio.run(mint())
assert token, "mint failed"

EPS = [
    ("status", "/api/search-ai/status"),
    ("intent_stats", "/api/search-ai/intent-stats"),
    ("decisions", "/api/search-ai/decisions?limit=8"),
    ("feedbacks", "/api/search-ai/feedbacks?limit=8"),
    ("bad_cases", "/api/search-ai/bad-cases?limit=8"),
    ("ab_stats", "/api/search-ai/ab-stats"),
    ("ab_decisions", "/api/search-ai/ab-decisions?limit=8"),
    ("mode", "/api/search-ai/mode"),
]
out = {}
for name, path in EPS:
    try:
        out[name] = http(path, token)
    except Exception as e:
        out[name] = {"error": str(e)[:150]}
print("@@BEGIN@@")
print(json.dumps(out, ensure_ascii=False, default=str))
print("@@END@@")
'''.replace("__PHONE__", PHONE).replace("__PWD__", PWD)


def main():
    b64 = base64.b64encode(INNER.encode()).decode()
    cmd = (f"echo {b64} | base64 -d | "
           f"docker exec -i zhuxiang-backend-1 python -")
    r = subprocess.run(["ssh", HOST, cmd],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        print("[ssh stderr]", r.stderr[:400])
        raise SystemExit(1)
    out = r.stdout
    if "@@BEGIN@@" not in out:
        print(out[:1500])
        raise SystemExit(1)
    payload = out.split("@@BEGIN@@")[1].split("@@END@@")[0].strip()
    data = json.loads(payload)
    print(json.dumps(data, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
