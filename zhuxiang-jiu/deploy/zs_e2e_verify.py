"""智搜 MVP·生产端到端验证(公开 HTTP, 游客口径)"""
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


CASES = [
    "麒麟和瑞麒瑞麟是什么关系",
    "竹奕酒多少钱一瓶",
    "我想做代理怎么申请",
    "你们的酒能治病吗",
    "会员有什么权益",
]


async def t():
    for text in CASES:
        b = query(text)
        d = b.get("data", {})
        print(f"\n[{text}]")
        print(f"  意图: {d.get('intent')}({d.get('intentName')}) "
              f"置信 {d.get('confidence')} | mode={d.get('zsMode')}")
        print(f"  回答: {str(d.get('answer', ''))[:76]}")
        rs = d.get("results", [])
        if rs:
            print(f"  结果: {len(rs)} 条, top: {rs[0]['title'][:32]}"
                  f" ({rs[0]['source'][:20]})")
        ac = d.get("actions", [])
        if ac:
            print(f"  动作: {[a['label'] for a in ac[:3]]}")


asyncio.run(t())
