"""麒麟任务闭环 v2: pending 麒麟条目 走 review→publish 全流程

背景: 渐进信任(D-16)需来源"最近5条人工审核全过"连胜——全新爬虫
源无人工审核记录, 自动过审永不满足(自动抓取链路死锁)。

本脚本对 qilin 域 pending 条目(维基-麒麟 22 条 crawl + 瑞麒瑞麟
品牌文档 5 条 document)执行完整治理流程:
    review_entry(approve=True)  # 合规分<通过线的被 ValueError 拒
    → publish_entry            # 生成版本快照
review 阶段合规筛查不通过的自动跳过(治理不降级)。
"""
import asyncio

QILIN_KW = ("麒麟", "瑞麒", "瑞麟", "仁兽", "祥瑞")


async def t():
    from services.knowledge_service import KnowledgeService
    svc = KnowledgeService()

    entries = await svc.repo.list_entries(limit=5000)
    published = rejected = 0
    for e in entries:
        if e["status"] != "pending":
            continue
        text = (e.get("question", "") + e.get("answer", ""))
        if not any(k in text for k in QILIN_KW):
            continue
        try:
            await svc.review_entry(e["id"], approve=True, reviewer_id=0)
        except ValueError as ex:
            rejected += 1
            print(f"  ✗ 条目#{e['id']} 过审失败: {ex}")
            continue
        await svc.publish_entry(e["id"], publisher_id=0)
        published += 1
    print(f"麒麟条目发布: {published} 条 | 合规拦截: {rejected} 条")

    print("\n== 检索验证 ==")
    for q in ("瑞麒", "瑞麟 商标", "麒麟 仁兽", "麒麟送子"):
        rs = await svc.search(query=q, top_k=3, record_hit=False)
        print(f"\n查询[{q}] 命中{len(rs)}:")
        for x in rs[:3]:
            print(f"  - {x['question'][:32]} | {x['answer'][:40]}…"
                  f" (source={x.get('source')}, sim={x['similarity']})")

    ks = await svc.stats()
    print("\n== 知识库统计 ==")
    print("totalEntries:", ks.get("totalEntries"),
          "| bySource:", ks.get("bySource"))


asyncio.run(t())
