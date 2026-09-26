"""热点 198 决策跟进 → 小红书内容生成(GLM 实轨首验)"""
import asyncio


async def main():
    from services.promo_service import PromoService
    from repositories.promo_repository import (
        PromoRepository,
    )
    svc = PromoService()
    repo = PromoRepository()

    hotspot = await repo.get_hotspot(198)
    print("热点 198:", hotspot.get("title"),
          "| score:", hotspot.get("score"),
          "| 状态:", hotspot.get("status"))

    # 决策(manual_queue 档留痕) → 人工裁决跟进
    d = await svc.decide_hotspot(hotspot)
    print("决策:", d.get("decision"), "|", d.get("reason")[:60])
    m = await svc.manual_decide(
        198, engage=True,
        note="国风主题与竹香国潮调性匹配, RPA 通道链路验证")
    print("人工裁决:", m.get("decision"), "|",
          m.get("status"))

    # 生成小红书内容(GLM 四步链)
    rows = await svc.generate_contents(
        198, platforms=("xiaohongshu",))
    print("\n生成内容:", len(rows), "条")
    for c in rows:
        print("contentId:", c.get("contentId"))
        print("  标题:", c.get("title"))
        print("  正文:", str(c.get("body"))[:150])
        print("  话题:", c.get("hashtags"))
        print("  短码:", c.get("shortCode"))
        print("  状态:", c.get("status"),
              "| 合规分:", c.get("complianceScore"))
        print("  agentTrace:", c.get("agentTrace"))
        print("  requiresManualReview:",
              c.get("requiresManualReview"))


asyncio.run(main())
