"""四个人物页源转 paused(主题过滤器已裁决拒绝, 停扫防周期性告警噪音)"""
import asyncio


async def t():
    from services.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    for s in await svc.list_crawl_sources(limit=50):
        if s["name"] in ("维基-郑板桥", "维基-文同", "维基-斑竹",
                         "维基-王徽之"):
            s["status"] = "paused"
            await svc.repo.save_crawl_source(s)
            print("paused:", s["name"])


asyncio.run(t())
