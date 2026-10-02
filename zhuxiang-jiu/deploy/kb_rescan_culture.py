"""关键词表扩充后: 重新激活四人物源并重扫(新表裁决)"""
import asyncio


async def t():
    from services.knowledge_service import KnowledgeService
    from services.knowledge_crawl_scheduler import run_crawl_scan

    svc = KnowledgeService()
    for s in await svc.list_crawl_sources(limit=50):
        if s["name"] in ("维基-郑板桥", "维基-文同", "维基-斑竹",
                         "维基-王徽之") and s["status"] == "paused":
            s["status"] = "active"
            await svc.repo.save_crawl_source(s)
            print("重新激活:", s["name"])

    print("\n== 新关键词表下重扫(主题过滤器再裁决) ==")
    st = await run_crawl_scan()
    for r in st["lastResults"]:
        if r["sourceId"] >= 16 or r["error"]:
            print(f"  源#{r['sourceId']} {r['name']}: "
                  f"入库 {r['ingested']} / 跳过 {r['skipped']}"
                  + (f" / 错误: {r['error'][:60]}" if r["error"] else ""))
    ks = await svc.stats()
    print("== 知识库统计 ==")
    print("totalEntries:", ks.get("totalEntries"),
          "| bySource:", ks.get("bySource"))


asyncio.run(t())
