"""竹文化人物页强制入白名单(跳过探测阈值, 主题过滤器裁决)

用户指示: 郑板桥/文同/斑竹/王徽之 人物典故页直接入白名单,
入库与否由服务层 topic_filter(主题域)+医药加严在抓取时裁决。
"""
import asyncio

CANDIDATES = [
    ("维基-郑板桥", "https://zh.wikipedia.org/wiki/郑板桥",
     ["bamboo_culture"]),
    ("维基-文同", "https://zh.wikipedia.org/wiki/文同",
     ["bamboo_culture"]),
    ("维基-斑竹", "https://zh.wikipedia.org/wiki/斑竹",
     ["bamboo_culture"]),
    ("维基-王徽之", "https://zh.wikipedia.org/wiki/王徽之",
     ["bamboo_culture"]),
]


async def t():
    from services.knowledge_service import KnowledgeService
    from services.knowledge_crawl_scheduler import run_crawl_scan

    svc = KnowledgeService()
    existing = {s["url"] for s in await svc.list_crawl_sources(limit=50)}
    for name, url, topics in CANDIDATES:
        if url not in existing:
            await svc.add_crawl_source(name, url, topics)
            print("✓ 强制入白名单:", name)
        else:
            print("已存在:", name)

    print("\n== 触发抓取(主题过滤器裁决) ==")
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
