"""P2 批次一专项测试: 变更通知 + 搜索联想/产品对比 + 每日任务/兑换商城

运行:
    python test_p2_batch1.py

覆盖:
    1. 条款变更通知(agreement):
       - 分级提前期 minor=7/important=14/major=30 天
       - 显式生效日早于下限 → 409(合规红线)
       - 分级非法/条款未发布 → 409; 非管理员 → 403
       - 登记时站内信触达已同意旧版会员(notifiedCount)
       - 公开列表 status 筛选; 撤回(仅 pending); 重复撤回 → 409
       - 到期惰性推进 pending → effective
    2. 搜索联想 + 产品对比(product):
       - 前缀联想命中/空 keyword 热门词
       - 对比 2-4 款 + 差异统计; 数量/重复 → 409; 不存在 → 404
    3. 每日任务 + 兑换商城(points):
       - 任务面板(autoTracked/完成态); 上报发分 + 当日幂等 409
       - 签到类任务拒绝上报; 未登录 401; 任务不存在 404
       - 目录种子幂等灌注 + pointsCost 升序
       - 兑换: FIFO 扣批次/库存扣减/每日限购 409/积分不足 409
       - 实物 pending 待发货/券类 issued 即时
       - 记录查询: 本人 200 / 无头 401 / 越权查他人 403
"""
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")
os.environ["AGREEMENT_MODE"] = "assist"  # 决策面开启(默认 off 会 409 拦截)

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


ADMIN = {"X-Role": "admin"}
MEMBER = {"X-Member-Id": "99001"}


# ============================================================
# 1. 条款变更通知
# ============================================================

async def run_agreement_tests(client):
    print("[01 条款变更通知]")
    # 前置: 建条款 → 发布 → 会员同意 → 新版本 → 再发布
    r = client.post("/api/agreements", headers=ADMIN, json={
        "agreementNo": "T99", "name": "P2批次一测试条款",
        "type": "term", "content": "v1 正文"})
    check("创建条款 200", r.status_code == 200, str(r.status_code))
    ag_id = (r.json().get("data") or {}).get("id")

    # 未发布登记 → 409
    r = client.post(f"/api/agreements/{ag_id}/change-notice",
                    headers=ADMIN, json={"changeLevel": "minor"})
    check("未发布登记 → 409", r.status_code == 409, str(r.status_code))

    r = client.post(f"/api/agreements/{ag_id}/publish", headers=ADMIN)
    check("v1 发布 200", r.status_code == 200, str(r.status_code))

    r = client.post(f"/api/agreements/{ag_id}/consent", headers=MEMBER)
    check("会员同意 200", r.status_code == 200, str(r.status_code))

    r = client.post(f"/api/agreements/{ag_id}/versions", headers=ADMIN,
                    json={"content": "v2 正文", "changeLog": "修订条款"})
    check("新版本创建 200", r.status_code == 200, str(r.status_code))
    r = client.post(f"/api/agreements/{ag_id}/publish", headers=ADMIN)
    check("v2 发布 200", r.status_code == 200, str(r.status_code))

    # 分级非法 → 409
    r = client.post(f"/api/agreements/{ag_id}/change-notice",
                    headers=ADMIN, json={"changeLevel": "critical"})
    check("分级非法 → 409", r.status_code == 409, str(r.status_code))

    # 非管理员 → 403
    r = client.post(f"/api/agreements/{ag_id}/change-notice",
                    headers=MEMBER, json={"changeLevel": "minor"})
    check("非管理员登记 → 403", r.status_code == 403, str(r.status_code))

    # minor: 缺省生效日 = 登记+7 天, 站内信触达已同意会员
    r = client.post(f"/api/agreements/{ag_id}/change-notice",
                    headers=ADMIN,
                    json={"changeLevel": "minor", "changeLog": "文字优化"})
    check("minor 登记 200", r.status_code == 200, str(r.status_code))
    notice = (r.json().get("data") or {})
    nid = notice.get("id")
    check("minor 提前期=7天", notice.get("noticeDays") == 7,
          str(notice.get("noticeDays")))
    check("缺省生效日=登记+7天",
          notice.get("effectiveDate", "")[:10]
          == (datetime.fromisoformat(notice.get("createdAt"))
              + timedelta(days=7)).isoformat()[:10],
          str(notice.get("effectiveDate")))
    check("状态=pending", notice.get("status") == "pending",
          str(notice.get("status")))
    check("站内信触达已同意会员", (notice.get("notifiedCount") or 0) >= 1,
          str(notice.get("notifiedCount")))
    check("fromVersion 取归档旧版", notice.get("fromVersion") == "v1.0",
          str(notice.get("fromVersion")))

    # 显式生效日早于下限 → 409
    too_early = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    r = client.post(f"/api/agreements/{ag_id}/change-notice",
                    headers=ADMIN,
                    json={"changeLevel": "minor", "effectiveDate": too_early})
    check("生效日早于下限 → 409", r.status_code == 409, str(r.status_code))

    # important/major 提前期
    for level, days in (("important", 14), ("major", 30)):
        r = client.post(f"/api/agreements/{ag_id}/change-notice",
                        headers=ADMIN, json={"changeLevel": level})
        d = (r.json().get("data") or {})
        check(f"{level} 提前期={days}天", d.get("noticeDays") == days,
              str(d.get("noticeDays")))

    # 公开列表(无鉴权) + status 筛选
    r = client.get("/api/agreements/change-notices?status=pending")
    body = r.json()
    check("公开列表 200 且含 pending",
          r.status_code == 200
          and any(n.get("id") == nid for n in (body.get("data") or [])),
          str(r.status_code))

    # 撤回: 仅 pending 可撤, 重复撤回 → 409
    r = client.post(f"/api/agreements/change-notices/{nid}/cancel",
                    headers=ADMIN)
    check("撤回 200 → cancelled",
          r.status_code == 200
          and (r.json().get("data") or {}).get("status") == "cancelled",
          str(r.status_code))
    r = client.post(f"/api/agreements/change-notices/{nid}/cancel",
                    headers=ADMIN)
    check("重复撤回 → 409", r.status_code == 409, str(r.status_code))

    # 到期惰性推进: 回填历史生效时刻后查询 → effective
    from repositories.agreement_repository import AgreementRepository
    arepo = AgreementRepository()
    r = client.post(f"/api/agreements/{ag_id}/change-notice",
                    headers=ADMIN,
                    json={"changeLevel": "minor", "changeLog": "惰性推进用"})
    nid2 = (r.json().get("data") or {}).get("id")
    n2 = await arepo.get_change_notice(nid2)
    past = (datetime.now(UTC) - timedelta(days=1))
    n2["createdAt"] = (past - timedelta(days=10)).isoformat()
    n2["effectiveDate"] = past.isoformat()
    await arepo.save_change_notice(n2)
    # 惰性推进只作用于本次查询命中的记录: 查 pending 触发推进
    r = client.get("/api/agreements/change-notices?status=pending")
    in_pending = [n for n in (r.json().get("data") or [])
                  if n.get("id") == nid2]
    check("查询 pending 时推进 → 返回态 effective",
          r.status_code == 200 and in_pending
          and in_pending[0].get("status") == "effective",
          str((r.status_code,
               [(n.get("id"), n.get("status")) for n in in_pending])))
    r = client.get("/api/agreements/change-notices?status=effective")
    check("推进结果落库 → effective 可查",
          r.status_code == 200 and any(
              n.get("id") == nid2 for n in (r.json().get("data") or [])),
          str(r.status_code))


# ============================================================
# 2. 搜索联想 + 产品对比
# ============================================================

async def run_product_tests(client):
    print("[02 搜索联想 + 产品对比]")
    # 取真实产品
    r = client.get("/api/product/list")
    products = (r.json().get("products") or [])
    check("产品列表非空", len(products) >= 2, str(len(products)))
    p1, p2 = products[0], products[1]

    # 空关键词 → 热门词
    r = client.get("/api/product/search/suggest")
    body = r.json()
    check("空关键词热门词 200", r.status_code == 200
          and body.get("count", 0) > 0
          and len(body.get("suggests") or []) > 0,
          str((r.status_code, body.get("count"))))

    # 前缀联想: 用产品名前 2 字
    kw = (p1.get("name") or "")[:2]
    r = client.get(f"/api/product/search/suggest?keyword={kw}")
    body = r.json()
    check(f"前缀联想命中(keyword={kw})",
          r.status_code == 200 and any(
              kw in w for w in (body.get("suggests") or [])),
          str((r.status_code, body.get("suggests"))))

    # 对比: 2 款 200 + 差异统计
    r = client.get(f"/api/product/compare?ids={p1['product_id']},{p2['product_id']}")
    body = r.json()
    check("对比 2 款 200",
          r.status_code == 200 and body.get("count") == 2,
          str((r.status_code, body.get("count"))))
    check("差异统计(1+ 差异维度)", (body.get("diffCount") or 0) >= 1,
          str(body.get("diffCount")))
    dims = body.get("dimensions") or []
    if dims:
        check("差异维度带 diff 标记",
              any(d.get("diff") for d in dims),
              str([d.get("diff") for d in dims][:5]))
    else:
        check("差异维度带 diff 标记", False, "dimensions 为空")

    # 边界: 1 款 / 5 款 / 重复 → 409
    r = client.get(f"/api/product/compare?ids={p1['product_id']}")
    check("1 款对比 → 409", r.status_code == 409, str(r.status_code))
    five = ",".join([p1["product_id"], p2["product_id"],
                     products[2]["product_id"], products[3]["product_id"],
                     products[4]["product_id"]]
                    if len(products) >= 5
                    else [p1["product_id"]] * 5)
    r = client.get(f"/api/product/compare?ids={five}")
    check("5 款对比 → 409", r.status_code == 409, str(r.status_code))
    r = client.get(
        f"/api/product/compare?ids={p1['product_id']},{p1['product_id']}")
    check("重复产品对比 → 409", r.status_code == 409, str(r.status_code))

    # 不存在 → 404
    r = client.get(
        f"/api/product/compare?ids={p1['product_id']},NOT-EXIST-99")
    check("含不存在产品 → 404", r.status_code == 404, str(r.status_code))


# ============================================================
# 3. 每日任务 + 兑换商城
# ============================================================

async def run_points_tests(client):
    print("[03 每日任务 + 兑换商城]")
    from core.locks import get_lock
    from services.points_service import PointsService
    from repositories.points_repository import SOURCE_CREDIT
    psvc = PointsService()

    # ---- 每日任务 ----
    r = client.get("/api/points/daily-tasks")
    check("任务面板无头 → 401", r.status_code == 401, str(r.status_code))

    r = client.get("/api/points/daily-tasks", headers=MEMBER)
    body = (r.json().get("data") or {})
    tasks = body.get("tasks") or []
    check("任务面板 200 共 4 项", r.status_code == 200 and len(tasks) == 4,
          str((r.status_code, len(tasks))))
    check("签到任务 autoTracked",
          any(t.get("taskCode") == "signin" and t.get("autoTracked")
              for t in tasks), "")

    r = client.post("/api/points/daily-tasks/browse_product/complete",
                    headers=MEMBER)
    body = (r.json().get("data") or {})
    check("上报浏览任务 +5",
          r.status_code == 200 and body.get("pointsEarned") == 5,
          str((r.status_code, body.get("pointsEarned"))))

    r = client.post("/api/points/daily-tasks/browse_product/complete",
                    headers=MEMBER)
    check("当日重复上报 → 409", r.status_code == 409, str(r.status_code))

    r = client.post("/api/points/daily-tasks/signin/complete", headers=MEMBER)
    check("签到类任务拒绝上报 → 409", r.status_code == 409,
          str(r.status_code))

    r = client.post("/api/points/daily-tasks/not_exist/complete",
                    headers=MEMBER)
    check("任务不存在 → 404", r.status_code == 404, str(r.status_code))

    # 面板完成态回显
    r = client.get("/api/points/daily-tasks", headers=MEMBER)
    body = (r.json().get("data") or {})
    check("面板回显已完成 1 项",
          body.get("doneCount") == 1 and body.get("earnedToday") == 5,
          str((body.get("doneCount"), body.get("earnedToday"))))

    # ---- 兑换商城 ----
    r = client.get("/api/points/exchange/items")
    items = (r.json().get("items") or [])
    costs = [i.get("pointsCost") for i in items]
    check("目录种子灌注 4 项", len(items) == 4, str(len(items)))
    check("目录按 pointsCost 升序", costs == sorted(costs), str(costs))

    # 二次访问幂等(不重复灌注)
    r = client.get("/api/points/exchange/items")
    check("目录二次访问幂等", len((r.json().get("items") or [])) == 4, "")

    # 新用户 0 分兑换 → 409 积分不足
    r = client.post("/api/points/exchange",
                    headers={"X-Member-Id": "99002"},
                    json={"itemId": "EX-COUPON-10"})
    check("积分不足 → 409", r.status_code == 409, str(r.status_code))

    # 注分 12000(单批次) 供兑换
    uid = 99001
    async with get_lock(f"points:account:{uid}"):
        await psvc._earn_points(uid, 12000, SOURCE_CREDIT,
                                "p2test:credit:99001", "P2批次一测试注分")

    # 券类兑换: 即时 issued + FIFO 批次消耗 + 库存扣减
    from repositories.points_repository import PointsRepository
    prepo = PointsRepository()
    acc_before = (await prepo.get_account(uid) or {}).get("totalPoints", 0)
    r = client.post("/api/points/exchange", headers=MEMBER,
                    json={"itemId": "EX-COUPON-10"})
    body = (r.json().get("data") or {})
    check("券类兑换 200 → issued",
          r.status_code == 200 and body.get("status") == "issued",
          str((r.status_code, body.get("status"))))
    check(f"兑换扣分 1000 余额 {acc_before - 1000}",
          body.get("balance") == acc_before - 1000,
          str(body.get("balance")))
    batches = await prepo.list_expiring_batches(uid)
    # FIFO: +5 任务小批次先被完全耗尽(从 active 列表消失),
    # 12000 大批次消耗 995, 合计 1000
    big = [b for b in batches if b.get("points") == 12000]
    small_left = [b for b in batches if b.get("points") == 5]
    check("FIFO 先耗尽小批次再扣大批次",
          big and big[0].get("consumedPoints") == 995
          and not small_left,
          str([(b.get("points"), b.get("consumedPoints"))
               for b in batches]))
    item = await prepo.get_exchange_item("EX-COUPON-10")
    check("库存扣减 100→99", item.get("stock") == 99, str(item.get("stock")))

    # 每日限购(1/天) → 409
    r = client.post("/api/points/exchange", headers=MEMBER,
                    json={"itemId": "EX-COUPON-10"})
    check("超出每日限购 → 409", r.status_code == 409, str(r.status_code))

    # 实物兑换: pending 待发货 + 地址留痕
    r = client.post("/api/points/exchange", headers=MEMBER,
                    json={"itemId": "EX-SAMPLE-100",
                          "address": "竹香路 1 号"})
    body = (r.json().get("data") or {})
    check("实物兑换 → pending 待发货",
          r.status_code == 200 and body.get("status") == "pending",
          str((r.status_code, body.get("status"))))
    check(f"余额 {acc_before - 1000 - 2600}",
          body.get("balance") == acc_before - 1000 - 2600,
          str(body.get("balance")))

    # 库存不足: 置库存 1 后兑 2 件 → 409
    gi = await prepo.get_exchange_item("EX-GIFT-BOX")
    gi["stock"] = 1
    await prepo.save_exchange_item(gi)
    r = client.post("/api/points/exchange", headers=MEMBER,
                    json={"itemId": "EX-GIFT-BOX", "quantity": 2})
    check("库存不足 → 409", r.status_code == 409, str(r.status_code))

    # 数量非法(0) → 422(参数校验)
    r = client.post("/api/points/exchange", headers=MEMBER,
                    json={"itemId": "EX-COUPON-10", "quantity": 0})
    check("数量 0 → 422", r.status_code == 422, str(r.status_code))

    # 不存在商品 → 404
    r = client.post("/api/points/exchange", headers=MEMBER,
                    json={"itemId": "EX-NOT-EXIST"})
    check("兑换商品不存在 → 404", r.status_code == 404, str(r.status_code))

    # ---- 兑换记录 ----
    r = client.get("/api/points/exchange/records", headers=MEMBER)
    body = r.json()
    check("本人记录 200 共 2 条",
          r.status_code == 200 and body.get("count") == 2,
          str((r.status_code, body.get("count"))))
    r = client.get("/api/points/exchange/records?status=pending",
                   headers=MEMBER)
    body = r.json()
    check("status 筛选 pending=1",
          (body.get("count") or 0) == 1
          and (body.get("data") or [{}])[0].get("address") == "竹香路 1 号",
          str(body.get("count")))
    r = client.get("/api/points/exchange/records")
    check("记录无头 → 401", r.status_code == 401, str(r.status_code))
    r = client.get("/api/points/exchange/records?user_id=99002",
                   headers=MEMBER)
    check("越权查他人记录 → 403", r.status_code == 403, str(r.status_code))
    r = client.get("/api/points/exchange/records?user_id=99002",
                   headers={**MEMBER, "X-Role": "admin"})
    check("管理员查他人记录 200", r.status_code == 200, str(r.status_code))


async def main():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    await run_agreement_tests(client)
    await run_product_tests(client)
    await run_points_tests(client)

    print("\n" + "=" * 60)
    print("\n".join(RESULTS))
    print("=" * 60)
    print(f"PASS: {PASS}  FAIL: {FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
