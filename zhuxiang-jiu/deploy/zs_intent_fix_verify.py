"""智搜意图修复·生产验证(4 个修复场景)"""
import asyncio
import json
import urllib.request

BASE = "https://zxjiu.com"


def query(text):
    req = urllib.request.Request(
        BASE + "/api/search-ai/query",
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


async def t():
    cases = [
        ("竹香酒怎么下单", "help", "操作意图优先"),
        ("退货怎么办理", "order", "退货高频场景"),
        ("退款多久到账", "order", "退款高频场景"),
        ("小孩能喝酒吗", "blocked", "未成年合规拦截"),
    ]
    ok = 0
    for text, expect, note in cases:
        d = query(text).get("data", {})
        hit = d.get("intent") == expect
        ok += hit
        print(f"  {'✓' if hit else '✗'} [{d.get('intent')}] {text}"
              f" (期望 {expect}, {note})")
    print(f"\n{ok}/4 通过")


asyncio.run(t())
