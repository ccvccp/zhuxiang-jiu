"""P1-12 活动发奖全流程测试(Service 层 + HTTP 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_activity_lottery.py

覆盖(设计文档 §3.3/§7.3):
    1. 奖品池配置: 成功 / 非抽奖活动拒 / 概率超 100% 拒 /
       单奖超 ¥5000 拒(合规红线) / 概率公示查询
    2. 抽奖执行: 活动未进行拒 / 每日 3 次上限 / 未配置奖品池拒
    3. 中奖计算: 100% 概率必中 / remaining 扣减 / usedBudget 累加 /
       全部抽完后 probability 归零谢谢参与
    4. 发奖分派: 积分自动到账(余额断言) / 优惠券记录落地 /
       实物 pending 待发放
    5. 发货登记: 成功 pending→shipped / 非邮寄拒 / 签收确认闭环 /
       越权签收拒
    6. HTTP 层: 配置(admin)/抽奖(本人)/我的奖品/发货/签收全链路
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.activity_service import ActivityService
from services.points_service import PointsService
from repositories.activity_repository import (
    ActivityRepository, PRIZE_STATUS_PENDING, PRIZE_STATUS_ISSUED,
    PRIZE_STATUS_SHIPPED, PRIZE_STATUS_SIGNED,
)
from repositories.store import _mock_store, reset_store

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


USER = 9001


async def _ongoing_lottery(svc):
    """创建已进行中的抽奖活动"""
    a = await svc.create_activity(name="测试大转盘", type_="lottery")
    await svc.transition_status(a["id"], "registering")
    await svc.transition_status(a["id"], "ongoing")
    return a


PRIZES_FULL = [
    {"prizeName": "积分奖", "prizeType": "points", "prizeValue": 100,
     "probability": 100, "dailyLimit": 0, "totalLimit": 10},
]


async def run_service():
    svc = ActivityService()
    repo = ActivityRepository()

    # ============================================================
    # 1. 奖品池配置
    # ============================================================
    a = await _ongoing_lottery(svc)
    # 非抽奖活动拒
    promo = await svc.create_activity(name="促销", type_="promotion")
    try:
        await svc.configure_prizes(promo["id"], PRIZES_FULL)
        check("配置: 非抽奖活动拒绝", False)
    except ValueError as e:
        check("配置: 非抽奖活动拒绝", "抽奖" in str(e))

    # 概率超 100% 拒
    try:
        await svc.configure_prizes(a["id"], [
            {"prizeName": "A", "prizeType": "coupon", "prizeValue": 10,
             "probability": 60, "dailyLimit": 0, "totalLimit": 10},
            {"prizeName": "B", "prizeType": "coupon", "prizeValue": 10,
             "probability": 60, "dailyLimit": 0, "totalLimit": 10},
        ])
        check("配置: 概率超 100% 拒绝", False)
    except ValueError:
        check("配置: 概率超 100% 拒绝", True)

    # 单奖超 ¥5000 拒
    try:
        await svc.configure_prizes(a["id"], [
            {"prizeName": "大奖", "prizeType": "product", "prizeValue": 6000,
             "probability": 1, "dailyLimit": 0, "totalLimit": 1},
        ])
        check("配置: 单奖超 ¥5000 拒绝(合规)", False)
    except ValueError as e:
        check("配置: 单奖超 ¥5000 拒绝(合规)", "合规" in str(e))

    # 成功配置(100% 概率积分奖)
    r = await svc.configure_prizes(a["id"], PRIZES_FULL)
    check("配置: 成功(概率合计 100%)",
          r["prizeCount"] == 1 and r["totalProbability"] == 100)

    # 概率公示
    pool = await svc.get_prize_pool(a["id"])
    check("配置: 奖品池公示查询", len(pool) == 1
          and pool[0]["prizeName"] == "积分奖")

    # ============================================================
    # 2. 抽奖执行校验
    # ============================================================
    # 活动未进行拒(新建 draft 活动)
    draft = await svc.create_activity(name="未开始", type_="lottery")
    try:
        await svc.draw_lottery(draft["id"], USER)
        check("抽奖: 活动未进行拒绝", False)
    except ValueError as e:
        check("抽奖: 活动未进行拒绝", "状态" in str(e))

    # 未配置奖品池拒
    await svc.transition_status(draft["id"], "registering")
    await svc.transition_status(draft["id"], "ongoing")
    try:
        await svc.draw_lottery(draft["id"], USER)
        check("抽奖: 未配置奖品池拒绝", False)
    except ValueError as e:
        check("抽奖: 未配置奖品池拒绝", "奖品池" in str(e))

    # ============================================================
    # 3. 中奖计算 + 发奖分派(100% 概率积分奖)
    # ============================================================
    # 记录抽奖前积分余额
    from repositories.points_repository import PointsRepository
    points_repo = PointsRepository()
    account_before = await points_repo.get_account(USER)
    balance_before = account_before.get("totalPoints", 0) if account_before else 0

    r = await svc.draw_lottery(a["id"], USER)
    check("抽奖: 100% 概率必中", r["won"] is True
          and r["prizeName"] == "积分奖", f"r={r}")
    check("抽奖: 积分自动到账", r["status"] == PRIZE_STATUS_ISSUED
          and r["prizeType"] == "points")

    # 积分余额断言
    account_after = await points_repo.get_account(USER)
    balance_after = account_after.get("totalPoints", 0) if account_after else 0
    check("抽奖: 积分余额+100", balance_after - balance_before == 100,
          f"before={balance_before} after={balance_after}")

    # remaining 扣减
    pool = await svc.get_prize_pool(a["id"])
    check("抽奖: remaining 扣减", pool[0]["remaining"] == 9)

    # usedBudget 累加
    activity = await repo.get_activity(a["id"])
    check("抽奖: usedBudget 累加", activity["usedBudget"] == 100)

    # 我的奖品
    mine = await svc.list_my_prizes(USER)
    check("奖品: 我的奖品含记录", mine["total"] == 1
          and mine["prizes"][0]["recordNo"].startswith("JZ"))

    # ============================================================
    # 4. 实物奖品 pending + 发货 + 签收
    # ============================================================
    b = await _ongoing_lottery(svc)
    await svc.configure_prizes(b["id"], [
        {"prizeName": "竹香尊享礼盒", "prizeType": "product",
         "prizeValue": 268, "probability": 100, "dailyLimit": 0,
         "totalLimit": 5},
    ])
    r = await svc.draw_lottery(b["id"], USER)
    check("实物: 中奖落 pending", r["won"] is True
          and r["status"] == PRIZE_STATUS_PENDING)
    record_no = r["recordNo"]

    # 发货登记
    d = await svc.deliver_prize(record_no, "SF1234567890")
    check("发货: pending→shipped", d["status"] == PRIZE_STATUS_SHIPPED
          and d["waybillNo"] == "SF1234567890")

    # 重复发货拒
    try:
        await svc.deliver_prize(record_no, "SF999")
        check("发货: 重复发货拒绝", False)
    except ValueError:
        check("发货: 重复发货拒绝", True)

    # 越权签收拒
    try:
        await svc.confirm_prize_received(record_no, USER + 999)
        check("签收: 越权拒绝", False)
    except ValueError:
        check("签收: 越权拒绝", True)

    # 签收闭环
    d = await svc.confirm_prize_received(record_no, USER)
    check("签收: shipped→signed", d["status"] == PRIZE_STATUS_SIGNED)

    # ============================================================
    # 5. 每日 3 次上限
    # ============================================================
    c = await _ongoing_lottery(svc)
    await svc.configure_prizes(c["id"], [
        {"prizeName": "优惠券", "prizeType": "coupon", "prizeValue": 50,
         "probability": 100, "dailyLimit": 0, "totalLimit": 100},
    ])
    r1 = await svc.draw_lottery(c["id"], USER)
    r2 = await svc.draw_lottery(c["id"], USER)
    check("次数: 第 1/2 次成功", r1["won"] and r2["won"]
          and r2["drawsRemainingToday"] == 1)
    # 第 3 次后(共 3 次)再抽拒
    await svc.draw_lottery(c["id"], USER)
    try:
        await svc.draw_lottery(c["id"], USER)
        check("次数: 每日 3 次上限", False)
    except ValueError as e:
        check("次数: 每日 3 次上限", "次数已用尽" in str(e))

    # 优惠券记录落地
    mine = await svc.list_my_prizes(USER)
    coupon_prizes = [p for p in mine["prizes"]
                     if p["prizeType"] == "coupon"]
    check("优惠券: 中奖记录含 couponNo",
          all(p.get("couponNo", "").startswith("CP") for p in coupon_prizes)
          and len(coupon_prizes) >= 1, f"n={len(coupon_prizes)}")
    coupons = _mock_store.get("activity_lottery_coupons", {})
    check("优惠券: 记录存储落地",
          any(c["source"] == "lottery" for c in coupons.values()))

    # ============================================================
    # 6. 奖品抽完 → 谢谢参与
    # ============================================================
    e = await _ongoing_lottery(svc)
    await svc.configure_prizes(e["id"], [
        {"prizeName": "唯一奖", "prizeType": "points", "prizeValue": 10,
         "probability": 100, "dailyLimit": 0, "totalLimit": 1},
    ])
    r1 = await svc.draw_lottery(e["id"], USER)
    r2 = await svc.draw_lottery(e["id"], USER)
    check("限量: 抽完归零谢谢参与", r1["won"] is True
          and r2["won"] is False and "谢谢参与" in r2["msg"], f"r2={r2}")


def run_http():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    async def _prepare():
        reset_store()
        svc = ActivityService()
        a = await _ongoing_lottery(svc)
        await svc.configure_prizes(a["id"], PRIZES_FULL)
        return a["id"]

    aid = asyncio.run(_prepare())

    # 配置端点: 无 admin 403
    r = client.post(f"/api/activity/admin/prizes/{aid}",
                    json={"prizes": PRIZES_FULL})
    check("HTTP 配置: 无 admin 403", r.status_code == 403, f"{r.status_code}")

    # 奖品池公示(公开)
    r = client.get(f"/api/activity/lottery/{aid}/prizes")
    check("HTTP 公示: 200 含概率", r.status_code == 200
          and r.json()["data"][0]["probability"] == 100, f"{r.status_code}")

    # 抽奖: 无头 401
    r = client.post("/api/activity/lottery/draw",
                    json={"activityId": aid, "userId": USER})
    check("HTTP 抽奖: 无头 401", r.status_code == 401, f"{r.status_code}")

    # 抽奖: 他人 403
    r = client.post("/api/activity/lottery/draw",
                    json={"activityId": aid, "userId": USER},
                    headers={"X-Member-Id": str(USER + 1)})
    check("HTTP 抽奖: 他人 403", r.status_code == 403, f"{r.status_code}")

    # 抽奖: 本人 200 中奖
    r = client.post("/api/activity/lottery/draw",
                    json={"activityId": aid, "userId": USER},
                    headers={"X-Member-Id": str(USER)})
    body = r.json()
    check("HTTP 抽奖: 200 必中积分", r.status_code == 200
          and body["data"]["won"] and body["data"]["status"] == "issued",
          f"{r.status_code} {r.text[:150]}")
    record_no = body["data"]["recordNo"]

    # 我的奖品
    r = client.get("/api/activity/prizes/mine",
                   headers={"X-Member-Id": str(USER)})
    check("HTTP 我的奖品: 200", r.status_code == 200
          and r.json()["data"]["total"] >= 1, f"{r.status_code}")

    # 发货: 非邮寄类(积分已 issued)拒绝
    r = client.post(f"/api/activity/admin/prize/{record_no}/deliver",
                    json={"waybillNo": "SF1"},
                    headers={"X-Role": "admin"})
    check("HTTP 发货: 非邮寄 409", r.status_code == 409, f"{r.status_code}")


def main():
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
