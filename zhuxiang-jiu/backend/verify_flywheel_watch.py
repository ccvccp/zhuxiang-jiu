"""飞轮观察项验证: 换一款/嗯归零 + repeat×v90 自愈关联

按部署时间窗对比: v86(~09:30)/v90(~09:40)/v91-C(~09:55)
前后各短语频次——归零/下降即闭环证据
"""
import asyncio

import services.xiaozhu_evolution_service as ev

# 今日部署时间线(近似)
CUT_V90 = "2026-09-26T09:40"   # v90 残化流自愈上线
CUT_V91C = "2026-09-26T09:55"  # v91-C 换一款长度修复


def bucket(ts: str) -> str:
    if not ts:
        return "unknown"
    if ts < CUT_V90:
        return "pre-v90(修复前)"
    if ts < CUT_V91C:
        return "v90期"
    return "post-v91C(最新)"


async def main():
    svc = ev.XiaozhuEvolutionService()
    cases = await svc.repo.list_records(
        svc.repo.TABLE_FAILURES, limit=800)
    watch = {"换一款": {}, "嗯": {}, "请我查看": {}}
    repeat_by: dict = {}
    for c in cases:
        k = c.get("kind")
        if k == "turn_score":
            continue
        b = bucket(str(c.get("ts") or ""))
        p = (c.get("rawText") or "")[:6]
        for w in watch:
            if w in p:
                watch[w][b] = watch[w].get(b, 0) + 1
        if k == "repeat":
            repeat_by[b] = repeat_by.get(b, 0) + 1
    print("== 观察短语 × 时间窗 ==")
    for w, m in watch.items():
        print(f"「{w}」: {m}")
    print(f"\nrepeat 按时间窗: {repeat_by}")
    print("\nCUT: v90=", CUT_V90, " v91C=", CUT_V91C)


asyncio.run(main())
