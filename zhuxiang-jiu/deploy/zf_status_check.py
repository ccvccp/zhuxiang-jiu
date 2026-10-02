"""智法·AI法务模块生产状态体检(直调服务层, 绕过 HTTP 认证)"""
import asyncio


async def t():
    from services.zf_evolution_service import ZfEvolutionService

    ev = ZfEvolutionService()
    st = await ev.status()
    print("== 大模型状态总览 ==")
    for k, v in st.items():
        print(f"  {k}: {v}")

    rows = await ev.precedents()
    print("\n== 判例库 ==", len(rows), "条")
    for x in rows[:6]:
        print("  -", x.get("caseId", x.get("id", "")), "|",
              str(x.get("title", x.get("summary", "")))[:48])

    tw = await ev.twin()
    print("\n== 数字孪生 ==\n ", {
        k: v for k, v in tw.items()
        if isinstance(v, (int, float, str))} if isinstance(tw, dict)
        else type(tw))


asyncio.run(t())
