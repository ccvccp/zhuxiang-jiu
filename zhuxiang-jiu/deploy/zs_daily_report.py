"""智搜观测数据采集(生产, 服务器侧 docker exec)——首次日报数据源

采集: status / intent-stats / decisions / feedbacks / bad-cases /
      ab-stats / ab-decisions / mode
通道: 容器内 AuthService 登录 admin → Bearer 调观测面
      (base64 传输规避多层引号剥离)
"""
import base64
import json
import subprocess

HOST = "root@47.236.61.117"

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
            phone="13800000731", password="test123456", role="admin")
    except ValueError:
        r = await AuthService().login("13800000731", "test123456")
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
'''


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
