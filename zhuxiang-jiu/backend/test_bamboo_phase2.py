"""竹信分二期专项测试(D-C 兑换折扣 + D-D 修复任务)

运行:
    python test_bamboo_phase2.py

覆盖:
    1. D-C 兑换竹信分折扣(points.exchange_item):
       无账户原价 / L3=95折 / L4=92折 / L5=90折 /
       折后价余额校验 / 记录与响应留痕(basePointsCost/discountRate/pointsSaved)
    2. D-D 信用修复任务(credit.repair_keep_checkin):
       非修复期 409 / 无账户 404 / 每日幂等 / 连续守约30天+20 /
       每修复周期至多一次 / 新周期重置累计
    3. 每日任务面板联动(points.list_daily_tasks):
       非修复期 4 项 / 修复期 5 项(streak/done/rewarded 回显) /
       守约任务拒绝上报(complete → 409)
    4. HTTP 面: POST /api/credit/repair/keep(401/200/409)
"""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")

PASS = 0
FAIL = 0
RESULTS = []

# D-C 用户: 99301 无账户 / 99302 L3 / 99303 L4 / 99304 L5
U0, U3, U4, U5 = 99301, 99302, 99303, 99304
# D-D 用户: 99311 修复主链 / 99312 HTTP 打卡
UR, UH = 99311, 99312


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def run(coro):
    """独立 loop 执行协程(带锁 service 直调专用, 同 test_bamboo_factors 范式)"""
    from core import locks as _locks
    _locks._async_locks.clear()
    try:
        return asyncio.run(coro)
    finally:
        _locks._async_locks.clear()


def _err(r):
    return (r.json().get("error") or r.json().get("detail") or "")


# ============================================================
# 前置构造
# ============================================================

async def _ensure(uid, level=None):
    """开通信账户(member 初始 350), 可选强制升到目标等级"""
    from services.credit_service import CreditService, ROLE_MEMBER
    svc = CreditService()
    await svc.repo.get_or_create_score(uid, ROLE_MEMBER)
    if level:
        cur = (await svc.repo.get_score(uid)).get("creditLevel")
        for lv in ("L2", "L3", "L4", "L5"):
            from repositories.credit_repository import (
                LEVEL_L2, LEVEL_L3, LEVEL_L4, LEVEL_L5,
            )
            rank = {"L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5}
            if rank[lv] > rank[cur]:
                await svc.upgrade_level(uid, lv, reason="二期专项")
            if lv == level:
                break
    return await svc.repo.get_score(uid)


async def _fund(uid, points):
    """注分(持账户锁, 生产 _earn_points 范式)"""
    from core.locks import get_lock
    from services.points_service import PointsService
    from repositories.points_repository import SOURCE_CREDIT
    async with get_lock(f"points:account:{uid}"):
        return await PointsService()._earn_points(
            uid, points, SOURCE_CREDIT,
            f"phase2:credit:{uid}", "竹信分二期专项注分")


async def _prep():
    from repositories.credit_repository import CreditRepository
    crepo = CreditRepository()
    # D-C: 无账户 99301 / L3 / L4 / L5
    await _ensure(U3, "L3")
    await _ensure(U4, "L4")
    await _ensure(U5, "L5")
    # D-D: 造修复期(L1→L2→L1 降级进入 60 天修复期)
    from services.credit_service import CreditService
    svc = CreditService()
    await _ensure(UR)
    await svc.upgrade_level(UR, "L2", reason="二期专项")
    await svc.downgrade_level(UR, "L1", reason="二期专项·造修复期")
    acct = await crepo.get_score(UR)
    # D-D HTTP 用户同样造修复期
    await _ensure(UH)
    await svc.upgrade_level(UH, "L2", reason="二期专项")
    await svc.downgrade_level(UH, "L1", reason="二期专项·造修复期")
    return {
        "repair_until": acct.get("repairUntil"),
        "score_r": acct.get("bambooScore"),
    }


# ============================================================
# 测试主体
# ============================================================

def main():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    prep = run(_prep())

    print("[01 D-C 兑换折扣]")
    from repositories.credit_repository import CreditRepository
    from services.points_service import PointsService
    from repositories.points_repository import PointsRepository
    psvc, prepo = PointsService(), PointsRepository()

    # 无信用账户 → 原价
    run(_fund(U0, 1000))
    r = client.post("/api/points/exchange",
                    headers={"X-Member-Id": str(U0)},
                    json={"itemId": "EX-COUPON-10"})
    body = (r.json().get("data") or {})
    check("无信用账户原价 1000",
          r.status_code == 200 and body.get("pointsCost") == 1000
          and body.get("discountRate") == 1.0
          and body.get("pointsSaved") == 0,
          str((r.status_code, body.get("pointsCost"),
               body.get("discountRate"))))

    # L3 → 95 折(折后价余额校验: 注 950 恰好够)
    run(_fund(U3, 950))
    r = client.post("/api/points/exchange",
                    headers={"X-Member-Id": str(U3)},
                    json={"itemId": "EX-COUPON-10"})
    body = (r.json().get("data") or {})
    check("L3 95折 950(base 1000)",
          r.status_code == 200 and body.get("pointsCost") == 950
          and body.get("basePointsCost") == 1000
          and body.get("discountRate") == 0.95
          and body.get("creditLevel") == "L3"
          and body.get("pointsSaved") == 50
          and body.get("balance") == 0,
          str((r.status_code, body.get("pointsCost"),
               body.get("balance"))))

    # 折后价不足仍 409: L3 再兑(注 900 < 950)
    run(_fund(U3, 900))
    r = client.post("/api/points/exchange",
                    headers={"X-Member-Id": str(U3)},
                    json={"itemId": "EX-VIP-MONTH"})
    check("L3 折后价不足 → 409", r.status_code == 409, str(r.status_code))

    # L4 → 92 折(SAMPLE 2600×0.92=2392)
    run(_fund(U4, 2392))
    r = client.post("/api/points/exchange",
                    headers={"X-Member-Id": str(U4)},
                    json={"itemId": "EX-SAMPLE-100",
                          "address": "竹香路 2 号"})
    body = (r.json().get("data") or {})
    check("L4 92折 2392(base 2600)",
          r.status_code == 200 and body.get("pointsCost") == 2392
          and body.get("basePointsCost") == 2600
          and body.get("discountRate") == 0.92
          and body.get("balance") == 0,
          str((r.status_code, body.get("pointsCost"))))

    # L5 → 90 折(VIP-MONTH 5000×0.90=4500)
    run(_fund(U5, 4500))
    r = client.post("/api/points/exchange",
                    headers={"X-Member-Id": str(U5)},
                    json={"itemId": "EX-VIP-MONTH"})
    body = (r.json().get("data") or {})
    check("L5 90折 4500(base 5000)",
          r.status_code == 200 and body.get("pointsCost") == 4500
          and body.get("discountRate") == 0.90
          and body.get("creditLevel") == "L5",
          str((r.status_code, body.get("pointsCost"))))

    # 兑换记录留痕
    async def _records():
        return await prepo.list_exchange_records(user_id=U3, limit=5)
    recs = run(_records())
    rec = recs[0] if recs else {}
    check("记录留痕 base/discount/level",
          rec.get("basePointsCost") == 1000
          and rec.get("discountRate") == 0.95
          and rec.get("creditLevel") == "L3"
          and rec.get("pointsCost") == 950,
          str({k: rec.get(k) for k in
               ("basePointsCost", "discountRate", "creditLevel",
                "pointsCost")}))

    print("[02 D-D 修复任务·打卡链]")
    from services.credit_service import (
        CreditService, REPAIR_KEEP_DAYS, REPAIR_KEEP_REWARD,
    )
    svc = CreditService()
    score_after_downgrade = prep["score_r"]

    # 非修复期(正常用户 99301 有兑换无降级——另造一个正常账户)
    async def _not_in_repair():
        from services.credit_service import ROLE_MEMBER
        await svc.repo.get_or_create_score(99399, ROLE_MEMBER)
        return await svc.repair_keep_checkin(99399)
    try:
        run(_not_in_repair())
        check("非修复期打卡 → ValueError", False, "未抛错")
    except ValueError:
        check("非修复期打卡 → ValueError", True)
    except Exception as exc:
        check("非修复期打卡 → ValueError", False, repr(exc))

    # 无账户 → KeyError
    try:
        run(svc.repair_keep_checkin(88888))
        check("无账户打卡 → KeyError", False, "未抛错")
    except KeyError:
        check("无账户打卡 → KeyError", True)
    except Exception as exc:
        check("无账户打卡 → KeyError", False, repr(exc))

    # 修复期首打卡 → streak 1
    res = run(svc.repair_keep_checkin(UR))
    check("修复期打卡 streak=1",
          res.get("streak") == 1 and res.get("targetDays") == 30
          and res.get("rewarded") is False,
          str(res))

    # 每日幂等
    try:
        run(svc.repair_keep_checkin(UR))
        check("同日重复打卡 → ValueError", False, "未抛错")
    except ValueError:
        check("同日重复打卡 → ValueError", True)
    except Exception as exc:
        check("同日重复打卡 → ValueError", False, repr(exc))

    # 预填 29 天历史 → 今日打卡达标回分(+20, 每周期一次)
    async def _prefill_29():
        acct = await crepo_get(UR)
        today = date.today()
        acct["repairKeepCycle"] = acct["repairUntil"]
        acct["repairKeepDates"] = [
            (today - timedelta(days=i)).isoformat()
            for i in range(1, REPAIR_KEEP_DAYS)   # 昨日回溯 29 天
        ]
        await CreditRepository().save_score(acct)
        return acct.get("bambooScore")

    async def crepo_get(uid):
        return await CreditRepository().get_score(uid)

    score_before_reward = run(_prefill_29())
    res = run(svc.repair_keep_checkin(UR))
    check(f"连续{REPAIR_KEEP_DAYS}天达标 +{REPAIR_KEEP_REWARD} 回分一次",
          res.get("streak") == REPAIR_KEEP_DAYS
          and res.get("rewarded") is True
          and res.get("scoreAfter")
          == score_before_reward + REPAIR_KEEP_REWARD,
          str((res.get("streak"), res.get("scoreAfter"),
               score_before_reward)))

    # 达标后再打卡(移除今日) → 不重复回分
    async def _drop_today():
        acct = await crepo_get(UR)
        acct["repairKeepDates"] = [
            d for d in acct["repairKeepDates"]
            if d != date.today().isoformat()]
        await CreditRepository().save_score(acct)
        return acct.get("bambooScore")

    score_no_double = run(_drop_today())
    res = run(svc.repair_keep_checkin(UR))
    check("每修复周期至多一次(不双发)",
          res.get("rewarded") is False
          and res.get("scoreAfter") is None
          and run(crepo_get(UR)).get("bambooScore") == score_no_double,
          str((res.get("rewarded"), score_no_double)))

    # 新修复周期(repairUntil 变化) → 累计清零重新开始
    async def _new_cycle():
        acct = await crepo_get(UR)
        acct["repairUntil"] = (datetime.utcnow()
                               + timedelta(days=30)).isoformat()
        await CreditRepository().save_score(acct)
        return acct.get("repairUntil")

    run(_new_cycle())
    res = run(svc.repair_keep_checkin(UR))
    check("新修复周期累计清零(streak=1)",
          res.get("streak") == 1 and res.get("rewarded") is False,
          str(res))

    print("[03 每日任务面板联动]")
    # 非修复期(99301 兑换过, 无降级) → 4 项
    r = client.get("/api/points/daily-tasks",
                   headers={"X-Member-Id": str(U0)})
    body = (r.json().get("data") or {})
    codes = [t.get("taskCode") for t in body.get("tasks") or []]
    check("非修复期面板 4 项(无守约任务)",
          r.status_code == 200 and len(codes) == 4
          and "credit_repair_keep" not in codes,
          str(codes))

    # 修复期(UR 已打卡今日) → 5 项 + streak/done 回显
    r = client.get("/api/points/daily-tasks",
                   headers={"X-Member-Id": str(UR)})
    body = (r.json().get("data") or {})
    tasks = body.get("tasks") or []
    tk = next((t for t in tasks
               if t.get("taskCode") == "credit_repair_keep"), None)
    check("修复期面板 5 项含守约任务",
          r.status_code == 200 and len(tasks) == 5 and tk is not None,
          str(len(tasks)))
    check("守约任务 done/streak/rewarded 回显",
          tk and tk.get("done") is True and tk.get("streak") == 1
          and tk.get("targetDays") == 30
          and tk.get("rewarded") is False
          and tk.get("autoTracked") is True,
          str(tk))

    # 守约任务拒绝上报
    r = client.post("/api/points/daily-tasks/credit_repair_keep/complete",
                    headers={"X-Member-Id": str(UR)})
    check("守约任务上报 → 409", r.status_code == 409, str(r.status_code))

    print("[04 HTTP 打卡端点]")
    r = client.post("/api/credit/repair/keep")
    check("无头 → 401", r.status_code == 401, str(r.status_code))
    r = client.post("/api/credit/repair/keep",
                    headers={"X-Member-Id": "abc"})
    check("非数字头 → 401", r.status_code == 401, str(r.status_code))
    r = client.post("/api/credit/repair/keep",
                    headers={"X-Member-Id": str(UH)})
    body = (r.json().get("data") or {})
    check("打卡 200 streak=1",
          r.status_code == 200 and body.get("streak") == 1,
          str((r.status_code, body)))
    r = client.post("/api/credit/repair/keep",
                    headers={"X-Member-Id": str(UH)})
    check("HTTP 同日重复 → 409", r.status_code == 409,
          str((r.status_code, _err(r))))
    r = client.post("/api/credit/repair/keep",
                    headers={"X-Member-Id": "99399"})
    check("HTTP 非修复期 → 409", r.status_code == 409,
          str((r.status_code, _err(r))))

    # 汇总
    print()
    print(f"竹信分二期专项: PASS={PASS} FAIL={FAIL}")
    for line in RESULTS:
        print(line)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
