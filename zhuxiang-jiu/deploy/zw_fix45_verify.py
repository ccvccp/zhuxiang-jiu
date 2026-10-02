"""断层④⑤修复·生产验证: circuit 时效指标 + alert 告警链"""
import asyncio


async def t():
    # ④ circuit 三指标: avgTransitHours 应有真实样本(原恒 0)
    from services.zw_circuit_service import ZwCircuitService
    cs = ZwCircuitService()
    status = await cs.circuit_status()
    print("① circuit 渠道指标:")
    for c in status.get("carriers", [])[:5]:
        print(f"  {c.get('carrierName')}: state={c.get('state')}"
              f" | pickupRate={c.get('pickupRate')}"
              f" | avgTransitHours={c.get('avgTransitHours')}"
              f" | sample={c.get('sample')}")

    # ⑤ alert: 消费者告警(延误致歉依赖 warnings 键)
    from services.zw_alert_service import ZwAlertService
    al = ZwAlertService()
    result = await al.scan()
    alerts = result.get("alerts", result.get("items", [])
                        ) if isinstance(result, dict) else (result or [])
    print("\n② 角色提醒 scan:", {k: v for k, v in result.items()
                                if isinstance(v, (int, float, str))}
          if isinstance(result, dict) else len(alerts))
    for a in alerts[:6]:
        print(f"  [{a.get('role')}] {str(a.get('title', ''))[:24]} | "
              f"{str(a.get('body', ''))[:36]}…")


asyncio.run(t())
