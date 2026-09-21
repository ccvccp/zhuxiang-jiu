"""活动管理后台·编辑与发奖记录列表测试(Service 层 + HTTP 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_activity_admin_update.py

覆盖:
    1. 编辑活动: 全字段更新 / 部分更新(未传字段不变) / 非草稿拒绝 /
       活动不存在 404 / 白名单防御(type/status/usedBudget 被忽略) /
       返回含 registrationCount
    2. 管理端发奖记录: 全量列表 / 状态过滤
    3. HTTP 层: PUT 编辑(200/403/404/409/exclude_unset 语义) /
       GET prize-records(200/403)
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.activity_service import ActivityService
from repositories.activity_repository import (
    ActivityRepository, PRIZE_STATUS_PENDING, PRIZE_STATUS_ISSUED,
)
from repositories.store import reset_store

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


async def run_service():
    reset_store()
    svc = ActivityService()
    repo = ActivityRepository()

    # ============================================================
    # 1. 编辑活动
    # ============================================================
    a = await svc.create_activity(
        name="原名称", type_="promotion", description="原描述",
        start_time="2026-10-01T00:00", end_time="2026-10-07T23:59",
        budget=100.0, rules={"满减": "满300减30"},
        applicable_scope={"scope": "all"},
    )

    # 1.1 全字段更新
    updated = await svc.update_activity(a["id"], {
        "name": "新名称", "description": "新描述",
        "startTime": "2026-10-02T00:00", "endTime": "2026-10-08T23:59",
        "budget": 200.0, "rules": {"满减": "满200减20"},
        "applicableScope": {"scope": "member"},
    })
    check("编辑: 全字段更新生效",
          updated["name"] == "新名称" and updated["description"] == "新描述"
          and updated["budget"] == 200.0
          and updated["rules"] == {"满减": "满200减20"}
          and updated["applicableScope"] == {"scope": "member"},
          f"{updated}")
    check("编辑: type/status 不可改且未变",
          updated["type"] == "promotion" and updated["status"] == "draft")

    # 1.2 部分更新: 仅传 name, 其余保持原值
    updated2 = await svc.update_activity(a["id"], {"name": "再改名"})
    check("编辑: 部分更新仅改 name",
          updated2["name"] == "再改名"
          and updated2["description"] == "新描述"
          and updated2["budget"] == 200.0,
          f"{updated2['name']}/{updated2['description']}/{updated2['budget']}")

    # 1.3 白名单防御: 越权字段被忽略
    updated3 = await svc.update_activity(a["id"], {
        "type": "lottery", "status": "ongoing",
        "usedBudget": 999.0, "id": 88888,
    })
    check("编辑: 越权字段被忽略(白名单)",
          updated3["type"] == "promotion" and updated3["status"] == "draft"
          and updated3["usedBudget"] == 0.0 and updated3["id"] == a["id"],
          f"type={updated3['type']} status={updated3['status']}")

    # 1.4 返回含 registrationCount
    check("编辑: 返回含 registrationCount",
          updated3.get("registrationCount") == 0)

    # 1.5 活动不存在
    try:
        await svc.update_activity(999999, {"name": "x"})
        check("编辑: 活动不存在 KeyError", False)
    except KeyError:
        check("编辑: 活动不存在 KeyError", True)

    # ============================================================
    # 1.7 起止时间校验
    # ============================================================
    try:
        await svc.create_activity(name="乱序", type_="promotion",
                                  start_time="2026-10-07T00:00",
                                  end_time="2026-10-01T00:00")
        check("时间: 结束早于开始拒绝(创建)", False)
    except ValueError as e:
        check("时间: 结束早于开始拒绝(创建)", "须晚于" in str(e))

    try:
        await svc.create_activity(name="非法格式", type_="promotion",
                                  start_time="not-a-date")
        check("时间: 非法格式拒绝(创建)", False)
    except ValueError as e:
        check("时间: 非法格式拒绝(创建)", "格式非法" in str(e))

    # 允许为空(草稿可不完整)
    empty = await svc.create_activity(name="无时间", type_="promotion")
    check("时间: 允许为空(草稿)", empty["status"] == "draft")

    # 编辑: 仅改其一, 合并后乱序须拒绝
    ok_act = await svc.create_activity(
        name="时间窗", type_="promotion",
        start_time="2026-10-01T00:00", end_time="2026-10-07T00:00")
    try:
        await svc.update_activity(ok_act["id"],
                                  {"startTime": "2026-10-08T00:00"})
        check("时间: 合并后乱序拒绝(编辑)", False)
    except ValueError as e:
        check("时间: 合并后乱序拒绝(编辑)", "须晚于" in str(e))

    try:
        await svc.update_activity(ok_act["id"],
                                  {"endTime": "bad-date"})
        check("时间: 非法格式拒绝(编辑)", False)
    except ValueError as e:
        check("时间: 非法格式拒绝(编辑)", "格式非法" in str(e))

    # 合法编辑(整体前移时间窗)应通过
    moved = await svc.update_activity(ok_act["id"],
                                      {"startTime": "2026-09-28T00:00"})
    check("时间: 合法时间窗编辑通过",
          moved["startTime"] == "2026-09-28T00:00")

    # 1.6 非 draft 拒绝
    await svc.transition_status(a["id"], "registering")
    try:
        await svc.update_activity(a["id"], {"name": "x"})
        check("编辑: 非草稿状态拒绝", False)
    except ValueError as e:
        check("编辑: 非草稿状态拒绝", "仅草稿" in str(e))

    # ============================================================
    # 2. 管理端发奖记录列表
    # ============================================================
    now = "2026-09-21T10:00:00"
    await repo.add_prize_record({
        "recordNo": "PR-TEST-01", "activityId": 1, "userId": 1,
        "prizeId": 1, "prizeName": "实物酒", "prizeType": "product",
        "prizeValue": 88.0, "status": PRIZE_STATUS_PENDING,
        "createdAt": now,
    })
    await repo.add_prize_record({
        "recordNo": "PR-TEST-02", "activityId": 1, "userId": 2,
        "prizeId": 2, "prizeName": "积分", "prizeType": "points",
        "prizeValue": 100.0, "status": PRIZE_STATUS_ISSUED,
        "createdAt": "2026-09-21T11:00:00",
    })

    records = await svc.list_prize_records()
    check("发奖记录: 全量列表", len(records) == 2,
          f"len={len(records)}")

    pending = await svc.list_prize_records(status=PRIZE_STATUS_PENDING)
    check("发奖记录: 状态过滤",
          len(pending) == 1 and pending[0]["recordNo"] == "PR-TEST-01",
          f"len={len(pending)}")

    # 降序排列(11:00 在前)
    check("发奖记录: 按时间降序",
          records[0]["recordNo"] == "PR-TEST-02")


def run_http():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    async def _prepare():
        reset_store()
        svc = ActivityService()
        a = await svc.create_activity(
            name="HTTP测试", type_="arena", sub_type="L01",
            budget=50.0, rules={"rule": 1})
        return a["id"], svc

    aid, svc = asyncio.run(_prepare())

    # 无 admin 403
    r = client.put(f"/api/activity/admin/update/{aid}",
                   json={"name": "x"})
    check("HTTP 编辑: 无 admin 403", r.status_code == 403, f"{r.status_code}")

    # 不存在 404(中间件统一包装: {"success": false, "error": ...})
    r = client.put("/api/activity/admin/update/999999",
                   json={"name": "x"}, headers={"X-Role": "admin"})
    err_msg = r.json().get("error") or r.json().get("detail") or ""
    check("HTTP 编辑: 不存在 404", r.status_code == 404
          and "活动不存在" in err_msg, f"{r.status_code}")

    # 正常 200 + exclude_unset 语义(body 只传 name, 其余保持创建值)
    r = client.put(f"/api/activity/admin/update/{aid}",
                   json={"name": "改名成功"},
                   headers={"X-Role": "admin"})
    body = r.json()
    check("HTTP 编辑: 200 更新成功", r.status_code == 200
          and body["data"]["name"] == "改名成功", f"{r.status_code} {r.text[:120]}")
    check("HTTP 编辑: exclude_unset 未传字段保持原值",
          body["data"]["type"] == "arena"
          and body["data"]["subType"] == "L01"
          and body["data"]["budget"] == 50.0
          and body["data"]["rules"] == {"rule": 1},
          f"{body['data']}")

    # 非 draft 409
    async def _to_registering():
        await svc.transition_status(aid, "registering")
    asyncio.run(_to_registering())
    r = client.put(f"/api/activity/admin/update/{aid}",
                   json={"name": "y"}, headers={"X-Role": "admin"})
    check("HTTP 编辑: 非草稿 409", r.status_code == 409, f"{r.status_code}")

    # 起止时间校验: 创建乱序 409
    r = client.post("/api/activity/admin/create",
                    json={"name": "乱序", "type": "promotion",
                          "startTime": "2026-10-07T00:00",
                          "endTime": "2026-10-01T00:00"},
                    headers={"X-Role": "admin"})
    err409 = r.json().get("detail") or r.json().get("error") or ""
    check("HTTP 时间: 创建乱序 409", r.status_code == 409
          and "须晚于" in err409, f"{r.status_code} {err409[:60]}")

    # 起止时间校验: 创建非法格式 409
    r = client.post("/api/activity/admin/create",
                    json={"name": "非法", "type": "promotion",
                          "startTime": "not-a-date"},
                    headers={"X-Role": "admin"})
    check("HTTP 时间: 创建非法格式 409", r.status_code == 409,
          f"{r.status_code}")

    # 起止时间校验: 编辑合并后乱序 409
    async def _prep_timed():
        return (await svc.create_activity(
            name="时间窗", type_="promotion",
            start_time="2026-10-01T00:00",
            end_time="2026-10-07T00:00"))["id"]
    tid = asyncio.run(_prep_timed())
    r = client.put(f"/api/activity/admin/update/{tid}",
                   json={"startTime": "2026-10-08T00:00"},
                   headers={"X-Role": "admin"})
    check("HTTP 时间: 编辑合并乱序 409", r.status_code == 409,
          f"{r.status_code}")

    # prize-records: 无 admin 403
    r = client.get("/api/activity/admin/prize-records")
    check("HTTP 发奖记录: 无 admin 403", r.status_code == 403,
          f"{r.status_code}")

    # prize-records: 200
    r = client.get("/api/activity/admin/prize-records",
                   headers={"X-Role": "admin"})
    check("HTTP 发奖记录: 200", r.status_code == 200
          and r.json()["success"] is True, f"{r.status_code}")


def main():
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
