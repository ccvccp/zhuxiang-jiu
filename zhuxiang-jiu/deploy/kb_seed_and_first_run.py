"""生产种子源配置 + 首轮自动抓取触发 + 结果核对(容器内执行)

种子源(白名单制, 主题域适配 TOPIC_DOMAINS; 选稳定可拉取的权威页):
    1. 维基百科-白酒   → wine(酿造/蒸馏/品鉴/窖藏)
    2. 维基百科-竹     → bamboo(竹材/竹叶/竹林/竹产业)
    3. 维基百科-竹林七贤 → bamboo_culture(竹林七贤/文人/雅士)
"""
import asyncio


async def t():
    from services.knowledge_service import KnowledgeService
    from services.knowledge_crawl_scheduler import run_crawl_scan

    svc = KnowledgeService()

    seeds = [
        ("维基百科-白酒", "https://zh.wikipedia.org/wiki/白酒", ["wine"]),
        ("维基百科-竹", "https://zh.wikipedia.org/wiki/竹",
         ["bamboo", "bamboo_culture"]),
        ("维基百科-竹林七贤", "https://zh.wikipedia.org/wiki/竹林七贤",
         ["bamboo_culture"]),
    ]
    existing = {s["url"] for s in await svc.list_crawl_sources(limit=50)}
    for name, url, topics in seeds:
        if url in existing:
            print("已存在:", name)
            continue
        s = await svc.add_crawl_source(name, url, topics)
        print("种子源已配:", s["id"], name, topics)

    print("== 触发首轮自动抓取(真实网络) ==")
    st = await run_crawl_scan()
    for r in st["lastResults"]:
        print(f"  源#{r['sourceId']} {r['name']}: "
              f"入库 {r['ingested']} / 跳过 {r['skipped']}"
              + (f" / 错误: {r['error'][:60]}" if r["error"] else ""))
    print("轮次:", st["runs"], "累计入库:", st["lastIngested"])

    print("== 知识库统计(抓取后) ==")
    ks = await svc.stats()
    print("totalEntries:", ks.get("totalEntries"))
    print("bySource:", ks.get("bySource"))


asyncio.run(t())
