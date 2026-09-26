"""v92 数据飞轮运行报告生成(容器内直调服务层)

输出: Reward 评分流统计 + 失败挖掘聚类 + 今日轮次行为
"""
import asyncio

import services.xiaozhu_evolution_service as ev


async def main():
    svc = ev.XiaozhuEvolutionService()
    sm = await svc.smoothness_report(limit=500)
    print("== SMOOTHNESS ==")
    for k in ("n", "rewardAvg", "taskAvg", "effAvg",
              "smoothAvg", "byTrack"):
        print(f"{k}: {sm.get(k)}")
    for s in (sm.get("slowest") or [])[:5]:
        print("  slow:", s)

    fv = await svc.failures_view(limit=500)
    print("\n== FAILURES ==")
    print("total:", fv.get("total"),
          "byKind:", fv.get("byKind"))
    for p in (fv.get("topPhrases") or [])[:8]:
        print(f"  top: {p}")


asyncio.run(main())
