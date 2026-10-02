"""智运看板数据源结构快照(直调服务层, 与看板 JS 取值逐一对照)"""
import asyncio
import json


async def t():
    from services.zw_mode_service import ZwModeService
    from services.zw_route_service import ZwRouteService
    from services.zw_track_service import ZwTrackService
    from services.zw_risk_service import ZwRiskService
    from services.zw_analysis_service import ZwAnalysisService

    ms = ZwModeService()
    mode = await ms.status_view()
    print("== mode.status_view 顶层键 ==")
    print(" ", sorted(mode.keys()))
    print("  mode/guardPaused:", mode.get("mode"), "/",
          mode.get("guardPaused"))

    r = ZwRouteService()
    scores = await r.carrier_scores()
    k0 = next(iter(scores))
    print("\n== carrier_scores 示例[" + k0 + "] ==")
    print(" ", sorted(scores[k0].keys()))

    health = await r.carrier_health()
    print("\n== carrier_health 顶层类型 ==", type(health).__name__)
    if isinstance(health, dict):
        print("  顶层键:", sorted(health.keys()))
        reps = health.get("reports")
        if reps:
            print("  reports[0] 键:", sorted(reps[0].keys()))
    elif isinstance(health, list) and health:
        print("  [0] 键:", sorted(health[0].keys()))

    dec = await r.route_decisions(limit=3)
    print("\n== route_decisions ==")
    if dec:
        print("  [0] 键:", sorted(dec[0].keys()))
        print("  decision 键:", sorted(dec[0].get("decision", {}).keys()))

    anom = await ZwTrackService().detect_anomalies(limit=10)
    print("\n== detect_anomalies ==", len(anom), "条")
    if anom:
        print("  [0] 键:", sorted(anom[0].keys()))

    rk = ZwRiskService()
    print("\n== risk claims 方法 ==")
    for name in dir(rk):
        if "claim" in name and not name.startswith("_"):
            print("  -", name)
    cl = await rk.claims(limit=3) if hasattr(rk, "claims") else []
    if cl:
        print("  claims[0] 键:", sorted(cl[0].keys()))

    an = ZwAnalysisService()
    cost = await an.cost_analysis()
    print("\n== cost_analysis 顶层键 ==")
    print(" ", sorted(cost.keys()) if isinstance(cost, dict) else type(cost))
    print(json.dumps({k: v for k, v in cost.items()
                      if isinstance(v, (int, float, str))},
                     ensure_ascii=False)[:300])


asyncio.run(t())
