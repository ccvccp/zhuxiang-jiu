"""协议模块生产现状盘点: 协议/版本/角色协议/签署记录"""
import asyncio


async def t():
    from services.agreement_service import AgreementService
    svc = AgreementService()

    ags = await svc.list_agreements(limit=50)
    print("== 协议 ==", len(ags))
    for a in ags[:12]:
        print(f"  - id={a.get('id')} | {str(a.get('name', ''))[:34]}"
              f" | type={a.get('type', '')} status={a.get('status', '')}"
              f" | ver={a.get('currentVersion', '')}")

    for role in ("member", "merchant", "supplier", "alliance",
                 "distributor"):
        try:
            ps = await svc.list_protocols(role=role)
            if ps:
                print(f"\n== 角色协议[{role}] ==", len(ps))
                for p in ps[:6]:
                    print("  -", str(p)[:110])
        except Exception as ex:
            print(f"角色[{role}]查询异常: {ex}")

    st = await svc.get_stats()
    print("\n== 统计 ==", st)

    cs = await svc.list_consents(limit=5)
    print("\n== 签署记录(近5) ==")
    for c in (cs if isinstance(cs, list) else cs.get("items", []))[:5]:
        print("  -", str(c)[:110])


asyncio.run(t())
