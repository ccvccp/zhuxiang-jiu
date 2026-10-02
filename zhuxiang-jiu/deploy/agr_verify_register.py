"""生产验证: 注册即签署 member 协议(端到端 HTTP)"""
import json
import random
import urllib.request

BASE = "http://localhost:8000"
PHONE = f"139{random.randint(10000000, 99999999)}"


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "X-Forwarded-For": "1.2.3.4"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


d = post("/api/entry/register", {
    "phone": PHONE, "password": "Test12345",
    "ageConfirmed": True}).get("data", {})
mid = d.get("memberId")
print("注册:", PHONE, "| memberId:", mid)

# 直调服务层查该用户签署记录
import asyncio


async def check():
    from services.agreement_service import AgreementService
    svc = AgreementService()
    rows = await svc.list_consents(user_id=mid)
    print("\n签署记录:", len(rows), "条")
    for c in rows:
        print(f"  - {c['agreementNo']} v{c['version']} | "
              f"方式={c['signMethod']} | ip={c['ip']} | {c['signedAt']}")


asyncio.run(check())
