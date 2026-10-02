"""知识库条目状态盘点: pending/approved/published/rejected 分布"""
import asyncio
from collections import Counter


async def t():
    from services.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    entries = await svc.repo.list_entries(limit=5000)
    c = Counter(e["status"] for e in entries)
    cs = Counter((e["status"], e.get("source")) for e in entries)
    print("总条目:", len(entries))
    print("按状态:", dict(c))
    print("按状态+来源:")
    for k, v in sorted(cs.items()):
        print(f"  {k}: {v}")


asyncio.run(t())
