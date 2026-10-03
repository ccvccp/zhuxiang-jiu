"""content 52 处置: review approve → 入队(now) → 出队发布(补完链路)"""
import subprocess

INNER = r'''
import asyncio, json

async def main():
    from services.promo_service import PromoService
    svc = PromoService()
    out = {}
    # 处置裁决: 数字溯源闸拦截(18岁无信源)——不可发布,
    # reject 留痕(不 bypass 红线)
    rv = await svc.review_content(
        52, approved=False, reviewer="站长(E2E半成品处置)")
    out["review"] = rv.get("status")
    print("@@BEGIN@@")
    print(json.dumps(out, ensure_ascii=False))
    print("@@END@@")

asyncio.run(main())
'''

import base64
b64 = base64.b64encode(INNER.encode()).decode()
cmd = (f"echo {b64} | base64 -d | "
       f"docker exec -i zhuxiang-backend-1 python -")
r = subprocess.run(["ssh", "root@47.236.61.117", cmd],
                   capture_output=True, text=True, timeout=120)
print(r.stdout)
print("[stderr]", r.stderr[:800])
