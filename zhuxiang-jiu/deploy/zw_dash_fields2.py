"""智运看板复查快照②: 渲染转换逻辑相关的完整结构
    1. cost.suggestions[0] 元素结构(看板渲染议价建议文本)
    2. route_decisions 返回排序方向(看板 reverse 依据)
    3. anomalies 样本完整 JSON(severity/type 映射核对)
    4. claims 样本完整 JSON(渲染列核对)
    5. 用户侧 logistics.html: list_tracks 返回顺序(倒序假设核对)
"""
import asyncio
import json


async def t():
    from services.zw_route_service import ZwRouteService
    from services.zw_track_service import ZwTrackService
    from services.zw_risk_service import ZwRiskService
    from services.zw_analysis_service import ZwAnalysisService
    from repositories.logistics_repository import LogisticsRepository

    cost = await ZwAnalysisService().cost_analysis()
    print("== ① cost.suggestions ==", len(cost.get("suggestions", [])),
          "条")
    for s in cost.get("suggestions", [])[:3]:
        print(" ", json.dumps(s, ensure_ascii=False)[:180])
    print("  byMonth:", json.dumps(cost.get("byMonth", {}),
                                    ensure_ascii=False)[:150])

    dec = await ZwRouteService().route_decisions(limit=5)
    print("\n== ② decisions 返回顺序 ==")
    for r in dec[:5]:
        print("  id:", r["decisionId"], "|", r["decidedAt"][:19])

    anom = await ZwTrackService().detect_anomalies(limit=5)
    print("\n== ③ anomalies ==", len(anom), "条")
    for a in anom[:3]:
        print(" ", json.dumps(a, ensure_ascii=False)[:170])

    cl = await ZwRiskService().claims(limit=3)
    print("\n== ④ claims ==", len(cl), "条")
    for c in cl[:2]:
        print(" ", json.dumps({k: c.get(k) for k in
                               ("claimNo", "claimTypeName", "claimAmount",
                                "status", "description")},
                              ensure_ascii=False)[:200])

    tracks = await LogisticsRepository().list_tracks(
        "SF10261003ZY0001", limit=10)
    print("\n== ⑤ list_tracks 顺序 ==", len(tracks), "条")
    for x in tracks[:4]:
        print("  ", str(x.get("trackTime", ""))[:19], "|",
              x.get("description", ""))


asyncio.run(t())
