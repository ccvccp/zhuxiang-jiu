"""48号 P3-2·唤醒 EMA 平滑生产验证脚本(verify)

用法(部署后跑一次):
    Get-Content .\\verify_xiaozhu_wake_ema_live.py -Raw |
        ssh root@47.236.61.117 "docker exec -i zhuxiang-backend-1 python -"

六验证(81号 verify 惯例, 容器内服务层直调——off 态探测器 2 项):
    V1 开关读取     XIAOZHU_WAKE_EMA 默认 off / env 可调
    V2 链持久化往返 set/get + float 归一(Redis 路径)
    V3 冷启动语义   count<10 → 链 None(判定降级)
    V4 双过实证     低链 0.7 拦(emaBlock)/高链 0.7 放(emaPass)
    V5 直通实证     低链 1.0 照常唤醒(emaDirect, 真唤醒零伤害)
    V6 shadow 留痕  EMA off 态 warm 链 → emaWould* 不拦截
注: V4-V6 临时设 env+测试 member(91x)+会话自清, 不触生产数据。
"""

import asyncio
import os

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    print(("  ✓ " if ok else "  ✗ ") + name +
          ("" if ok else f" — {detail}"))
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


async def main():
    from repositories.xiaozhu_repository import Xiaozhu48Repository
    from services.xiaozhu_service import (
        XiaozhuService, wake_ema_mode,
        _WAKE_EMA_WARM_ROUNDS,
    )
    repo = Xiaozhu48Repository()

    print("=" * 60)
    print("[V1] 开关读取")
    os.environ.pop("XIAOZHU_WAKE_EMA", None)
    check("默认 off", wake_ema_mode() is False)
    os.environ["XIAOZHU_WAKE_EMA"] = "on"
    check("env on 生效", wake_ema_mode() is True)

    print("[V2] 链持久化往返(Redis)")
    await repo.set_wake_ema(911, {"value": 0.735,
                                  "count": 12, "updatedAt": "t"})
    c = await repo.get_wake_ema(911)
    check("set/get 往返", c and c["value"] == 0.735
          and c["count"] == 12,
          f"got {c}")

    print("[V3] 冷启动语义")
    await repo.set_wake_ema(912, {"value": 0.7, "count": 9})
    os.environ["XIAOZHU_WAKE_SCORE_MODE"] = "on"
    svc = XiaozhuService()
    r = await svc._wake_ema_chain(912, 0.7)   # 第 10 轮
    check("count=9→10 第 10 轮 warm",
          r is not None and r["count"] == _WAKE_EMA_WARM_ROUNDS,
          f"got {r}")

    async def _handle(mid, text, ema):
        os.environ["XIAOZHU_WAKE_SCORE_MODE"] = "on"
        os.environ["XIAOZHU_WAKE_EMA"] = ema
        _svc = XiaozhuService()
        s = await _svc.open_session(mid, "text")
        rr = await _svc.handle_text(s["sessionId"], text)
        await _svc.delete_session(
            s["sessionId"])                  # 自清(级联)
        return rr

    print("[V4] 双过实证(on 态)")
    await repo.set_wake_ema(913, {"value": 0.30, "count": 99})
    r = await _handle(913, "小主，查优惠", "on")     # raw=0.7 低链
    check("低链 0.7 → emaBlock 拦截",
          r.get("turn", {}).get("wakeTrack") == "emaBlock"
          and r.get("wakeHint") is True, str(r)[:80])
    await repo.set_wake_ema(914, {"value": 0.85, "count": 99})
    r = await _handle(914, "小主，查优惠", "on")     # 高链
    check("高链 0.7 → emaPass 放行",
          r.get("turn", {}).get("wakeTrack") == "emaPass"
          and r.get("wakeHint") is False, str(r)[:80])

    print("[V5] 直通实证(on 态)")
    await repo.set_wake_ema(915, {"value": 0.10, "count": 99})
    r = await _handle(915, "小竹，看新品", "on")     # raw=1.0
    check("低链 1.0 → emaDirect 照常唤醒",
          r.get("turn", {}).get("wakeTrack") == "emaDirect"
          and r.get("wakeHint") is False, str(r)[:80])

    print("[V6] shadow 留痕(off 态)")
    await repo.set_wake_ema(916, {"value": 0.30, "count": 99})
    r = await _handle(916, "小主，查优惠", "off")
    check("off 态 warm 链 → emaWouldBlock 不拦截",
          r.get("turn", {}).get("wakeTrack") == "emaWouldBlock"
          and r.get("wakeHint") is False, str(r)[:80])

    print("=" * 60)
    print(f"总计: {PASS} 通过 / {FAIL} 失败"
          + ("(全过, EMA off 态生产零影响)" if not FAIL else ""))


asyncio.run(main())
