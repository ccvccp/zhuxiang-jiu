"""42号·AI无感开票模块 P1 专项测试(申诉通道 + 裁决 + 误拦截率)

运行方式:
    python test_invoice_p1.py

覆盖(拦截面板操作指南 §三 四步处置法):
    - 申诉提交(reject 档可申诉/非本人拒绝/非 reject 拒绝/
      重复申诉拒绝/决策不存在 404)
    - 申诉裁决(误拦恢复 approved/维持拦截 rejected/已裁决拒绝/
      不存在 404/决策流水申诉恢复标注)
    - 误拦截率统计(approved/rejectTotal)
    - 恢复后手动补开(申诉恢复 → request 补开)
    - HTTP 层(申诉提交/我的申诉/队列/裁决端点)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


async def make_reject_decision(svc, osvc, member_id=1):
    """前置: 造一个被拦截(reject)的订单决策

    可靠口径: 高频(24h 5 张)+block 得 55 分(manual_queue)后,
    直接改判为 reject(评分快照保留 55 分原貌——申诉场景关注
    档位而非分数构造方式, 决策流水的 action/factors 均真实)。
    """
    from repositories.store import _mock_store
    pid = next(iter(_mock_store["products"].keys()))

    async def new_order():
        result = await osvc.create(
            member_id,
            items=[{"productId": pid, "productName": "竹香酒",
                    "quantity": 2, "unitPrice": 100.0}],
            address={"name": "张三", "phone": "13800000001",
                     "province": "山东省", "city": "泰安市",
                     "district": "泰山区", "detail": "竹香路 1 号"})
        oid = result["orderId"]
        await osvc.pay(oid, "wechat")
        return oid

    # 预热 5 张正常票(拉满频次因子归零, 使目标单 55 分)
    for _ in range(5):
        oid = await new_order()
        await svc.on_order_completed(oid)

    # 目标订单: 高频(0)+block(0)+其余满分 → 55 分 manual_queue
    # → 改判 reject(模拟更严阈值下的真实拦截, 快照留痕)
    order_id = await new_order()
    r = await svc.on_order_completed(
        order_id, amount=200.0, order_risk_action="block")
    decision = await svc.repo.get_decision(order_id)
    decision["action"] = "reject"
    decision["detail"] = "测试构造拦截(高频+风控block)"
    await svc.repo.save_decision(decision)
    r["decision"] = decision
    return order_id, r


class TestAppeal:
    async def run(self):
        from services.invoice_service import Invoice42Service
        from services.order_service import OrderService

        svc = Invoice42Service()
        await svc.add_title(1, "company", "泰安竹香酒业",
                           "91370900MA3TEST01")
        osvc = OrderService()

        order_id, r = await make_reject_decision(svc, osvc)
        record("申诉前置-reject决策",
               r["decision"]["action"] == "reject",
               str(r["decision"].get("action")))

        # 正常申诉
        r = await svc.submit_appeal(1, order_id,
                                    "订单真实, 风控误判")
        appeal = r["appeal"]
        record("申诉-提交成功", appeal["status"] == "pending"
               and appeal["reason"] == "订单真实, 风控误判")
        appeal_id = appeal["appealId"]

        # 重复申诉 → 409
        try:
            await svc.submit_appeal(1, order_id, "再申诉")
            record("申诉-重复拒绝", False, "未抛出")
        except ValueError:
            record("申诉-重复拒绝", True)

        # 非本人 → 409
        try:
            await svc.submit_appeal(2, order_id, "冒充")
            record("申诉-非本人拒绝", False, "未抛出")
        except ValueError:
            record("申诉-非本人拒绝", True)

        # 决策不存在 → 404
        try:
            await svc.submit_appeal(1, "RT99999999")
            record("申诉-决策不存在404", False, "未抛出")
        except KeyError:
            record("申诉-决策不存在404", True)

        # 裁决: 误拦恢复
        r = await svc.decide_appeal(appeal_id, True,
                                    reviewer="财务张",
                                    note="核实为真实采购")
        appeal = r["appeal"]
        record("裁决-误拦恢复approved",
               appeal["status"] == "approved"
               and appeal["reviewer"] == "财务张"
               and "核实" in appeal["reviewNote"])
        # 决策流水标注
        decision = await svc.repo.get_decision(order_id)
        record("裁决-决策流水恢复标注",
               "申诉恢复" in decision.get("detail", ""),
               str(decision.get("detail")))

        # 已裁决再裁决 → 409
        try:
            await svc.decide_appeal(appeal_id, False)
            record("裁决-重复拒绝", False, "未抛出")
        except ValueError:
            record("裁决-重复拒绝", True)

        # 恢复后会员手动补开(四步法第 3 步路径 A)
        r = await svc.request_invoice(1, order_id)
        record("恢复-手动补开", r["success"] is True
               and bool(r["invoice"].get("invoiceNo")))
        decision = await svc.repo.get_decision(order_id)
        record("恢复-流水有发票号",
               bool(decision.get("invoiceNo")))

        # 维持拦截案例(高频下新订单必然低分; block 保 reject)
        order_id2, _ = await make_reject_decision(svc, osvc)
        r = await svc.submit_appeal(1, order_id2, "拆分开票误拦? ")
        aid2 = r["appeal"]["appealId"]
        r = await svc.decide_appeal(aid2, False, note="确认拆分, 维持")
        record("裁决-维持拦截rejected",
               r["appeal"]["status"] == "rejected")

        # 统计: 误拦截率 = approved(1) / reject(≥2)
        stats = await svc.admin_stats()
        record("统计-申诉计数", stats["appeals"]["total"] == 2
               and stats["appeals"]["approved"] == 1,
               str(stats.get("appeals")))
        record("统计-误拦截率", 0 < stats["falsePositiveRate"] <= 1,
               str(stats.get("falsePositiveRate")))

        # 我的申诉
        mine = await svc.my_appeals(1)
        record("查询-我的申诉", len(mine) == 2)

        # 申诉不存在裁决 → 404
        try:
            await svc.decide_appeal(9999, True)
            record("裁决-不存在404", False, "未抛出")
        except KeyError:
            record("裁决-不存在404", True)

        # 非 reject 档申诉 → 409(新会员正常单 auto_issue)
        from services.order_service import OrderService as OS
        from repositories.store import _mock_store as MS
        pid = next(iter(MS["products"].keys()))
        osvc2 = OS()
        result3 = await osvc2.create(
            2, items=[{"productId": pid, "productName": "竹香酒",
                       "quantity": 10, "unitPrice": 90.0}],
            address={"name": "李四", "phone": "13800000002",
                     "province": "山东省", "city": "泰安市",
                     "district": "泰山区", "detail": "竹香路 2 号"})
        order_id3 = result3["orderId"]
        await osvc2.pay(order_id3, "wechat")
        await svc.add_title(2, "personal", "李四")
        r = await svc.on_order_completed(order_id3,
                                         member_id=2)
        record("非reject前置-auto",
               r["decision"]["action"] == "auto_issue",
               str(r["decision"].get("action")))
        try:
            await svc.submit_appeal(2, order_id3)
            record("申诉-非reject档拒绝", False, "未抛出")
        except ValueError:
            record("申诉-非reject档拒绝", True)


class TestHttpRoutes:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.invoice_routes import register_invoice_routes
        from services.invoice_service import Invoice42Service
        from services.order_service import OrderService
        from repositories.store import _mock_store

        app = FastAPI()
        register_invoice_routes(app)
        client = TestClient(app)
        member = {"X-Member-Id": "1"}
        admin = {"X-Role": "admin"}

        # 前置: reject 决策(高频+block)
        svc = Invoice42Service()
        await svc.add_title(1, "company", "泰安竹香酒业",
                           "91370900MA3TEST01")
        osvc = OrderService()
        order_id, _ = await make_reject_decision(svc, osvc)

        # HTTP 申诉提交
        resp = client.post(f"/api/invoice/orders/{order_id}/appeal",
                           headers=member,
                           json={"reason": "真实采购被误拦"})
        body = resp.json()
        record("HTTP-申诉提交", resp.status_code == 200
               and body["appeal"]["status"] == "pending",
               str(resp.text[:120]))
        appeal_id = body["appeal"]["appealId"]

        # 重复 → 409
        resp = client.post(f"/api/invoice/orders/{order_id}/appeal",
                           headers=member, json={"reason": "x"})
        record("HTTP-重复申诉409", resp.status_code == 409,
               str(resp.status_code))

        # 我的申诉
        resp = client.get("/api/invoice/appeals", headers=member)
        record("HTTP-我的申诉", resp.status_code == 200
               and resp.json()["total"] == 1)

        # 管理端队列(pending 过滤)
        resp = client.get("/api/invoice/admin/appeals", headers=admin,
                          params={"status": "pending"})
        record("HTTP-申诉队列", resp.status_code == 200
               and resp.json()["total"] == 1,
               str(resp.json().get("total")))

        # 裁决(恢复)
        resp = client.post(
            f"/api/invoice/admin/appeals/{appeal_id}/decide",
            headers=admin,
            json={"approve": True, "reviewer": "admin",
                  "note": "核实恢复"})
        record("HTTP-裁决恢复", resp.status_code == 200
               and resp.json()["appeal"]["status"] == "approved",
               str(resp.text[:120]))

        # 已裁决 → 409
        resp = client.post(
            f"/api/invoice/admin/appeals/{appeal_id}/decide",
            headers=admin, json={"approve": False})
        record("HTTP-重复裁决409", resp.status_code == 409,
               str(resp.status_code))

        # 统计含申诉与误拦截率
        resp = client.get("/api/invoice/admin/stats", headers=admin)
        body = resp.json()
        record("HTTP-统计误拦截率", resp.status_code == 200
               and "falsePositiveRate" in body
               and body["appeals"]["approved"] == 1,
               str(body.get("appeals")))

        # 恢复后手动补开
        resp = client.post(f"/api/invoice/orders/{order_id}/request",
                           headers=member)
        record("HTTP-恢复后补开", resp.status_code == 200
               and bool(resp.json()["invoice"].get("invoiceNo")),
               str(resp.text[:120]))

        # 鉴权
        resp = client.get("/api/invoice/admin/appeals")
        record("HTTP-队列非admin403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(f"/api/invoice/orders/{order_id}/appeal",
                           json={"reason": "x"})
        record("HTTP-申诉缺头403", resp.status_code == 403,
               str(resp.status_code))


async def main():
    test_classes = [
        ("申诉与裁决", TestAppeal),
        ("HTTP层", TestHttpRoutes),
    ]
    print("=" * 62)
    print("42号·AI无感开票模块 P1 专项测试(申诉+裁决+误拦截率)")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, repr(e))

    print("\n" + "-" * 62)
    print("\n".join(RESULTS))
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
