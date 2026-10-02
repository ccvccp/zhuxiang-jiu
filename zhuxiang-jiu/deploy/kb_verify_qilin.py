"""qilin 域检索验证: 瑞麒/瑞麟/麒麟/仁兽 可召回"""
import asyncio


async def t():
    from services.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    for q in ("瑞麒", "瑞麟 商标", "麒麟 仁兽", "麒麟送子"):
        rs = await svc.search(query=q, top_k=3, record_hit=False)
        print(f"\n查询[{q}] 命中{len(rs)}:")
        for r in rs[:3]:
            print(f"  - {r['question'][:36]} | {r['answer'][:44]}…"
                  f" (source={r.get('source')}, sim={r['similarity']})")


asyncio.run(t())
