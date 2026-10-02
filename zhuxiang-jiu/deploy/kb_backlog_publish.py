"""存量清理: 自动发布积压的 crawl/document pending 条目 + 验证

背景: D-16 渐进信任死锁导致 ~220 条抓取知识滞留候选池。
auto_publish_crawl_entries(合规分>=80 走 review→publish)已接入
调度器, 本脚本一次性清存量并抽查检索。
"""
import asyncio
from collections import Counter


async def t():
    from services.knowledge_service import KnowledgeService
    svc = KnowledgeService()

    r = await svc.auto_publish_crawl_entries()
    print("自动发布:", r["published"], "条 | 留候选:", r["skipped"], "条")

    entries = await svc.repo.list_entries(limit=1000)
    c = Counter(e["status"] for e in entries)
    print("状态分布:", dict(c))

    print("\n== 跨域检索抽查 ==")
    for q in ("竹叶黄酮 功效", "竹茹 中药", "白酒 酿造"):
        rs = await svc.search(query=q, top_k=2, record_hit=False)
        print(f"\n查询[{q}]:")
        for x in rs:
            print(f"  - {x['question'][:34]} (source={x['source']}, "
                  f"sim={x['similarity']})")

    ks = await svc.stats()
    print("\n== 知识库统计 ==")
    print("totalEntries:", ks.get("totalEntries"),
          "| bySource:", ks.get("bySource"))


asyncio.run(t())
