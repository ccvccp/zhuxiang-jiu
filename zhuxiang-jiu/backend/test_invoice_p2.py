"""42号·AI无感开票模块 P2 专项测试(学习回流: 申诉裁决真值→第25档案)

运行方式:
    python test_invoice_p2.py

覆盖:
    - collect_appeal_feedback(已裁决回流/pending跳过/幂等appealFed/
      approved→expected=auto_issue/rejected→expected=reject)
    - run_learning(Hedge 一轮学习/权重更新)
    - learning_status(计数/权重视图)
    - HTTP 层(collect/run/status 三端点 + 鉴权)
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


async def setup_appeals(svc, osvc):
    """前置: 1 个误拦(approved) + 1 个拦对(rejected) + 1 个 pending"""
    from repositories.store import _mock_store
    pid = next(iter(_mock_store["products"].keys()))

    async def new_order():
        result = await osvc.create(
            1, items=[{"productId": pid, "productName": "竹香酒",
                       "quantity": 2, "unitPrice": 100.0}],
            address={"name": "张三", "phone": "13800000001",
                     "province": "山东省", "city": "泰安市",
                     "district": "泰山区", "detail": "竹香路 1 号"})
        oid = result["orderId"]
        await osvc.pay(oid, "wechat")
        return oid

    # 预热 5 张(拉满频次) + 构造 reject 决策(改判口径同 P1)
    for _ in range(5):
        oid = await new_order()
        await svc.on_order_completed(oid)

    async def make_reject():
        oid = await new_order()
        await svc.on_order_completed(
            oid, amount=200.0, order_risk_action="block")
        decision = await svc.repo.get_decision(oid)
        decision["action"] = "reject"
        await svc.repo.save_decision(decision)
        return oid

    # 误拦(恢复)
    oid1 = await make_reject()
    r = await svc.submit_appeal(1, oid1, "真实采购误拦")
    await svc.decide_appeal(r["appeal"]["appealId"], True,
                            reviewer="admin", note="核实恢复")
    # 拦对(维持)
    oid2 = await make_reject()
    r = await svc.submit_appeal(1, oid2, "拆分开票")
    await svc.decide_appeal(r["appeal"]["appealId"], False,
                            note="确认拆分")
    # 待裁决(应跳过)
    oid3 = await make_reject()
    await svc.submit_appeal(1, oid3, "待处理")
    return oid1, oid2, oid3


class TestLearning:
    async def run(self):
        from services.invoice_service import Invoice42Service
        from services.order_service import OrderService

        svc = Invoice42Service()
        await svc.add_title(1, "company", "泰安竹香酒业",
                           "91370900MA3TEST01")
        osvc = OrderService()
        oid1, oid2, oid3 = await setup_appeals(svc, osvc)

        # 批量回流: 2 submitted(1 approved 误判 + 1 rejected 命中)
        # + 1 pending skipped + 5 张正常决策无申诉不参与
        r = await svc.collect_appeal_feedback()
        record("回流-提交数", r["submitted"] == 2,
               str(r))
        record("回流-跳过数", r["skipped"] == 1,
               str(r))
        record("回流-结果数", len(r["results"]) == 2)

        # 反馈语义: 误拦(approved)→correct=False / 拦对(rejected)→correct=True
        fb = r["results"]
        record("回流-误拦负反馈", any(
            x.get("correct") is False for x in fb), str(fb)[:200])
        record("回流-拦对正反馈", any(
            x.get("correct") is True for x in fb), str(fb)[:200])

        # 幂等: 二次 collect 全 skip(2 已回流 + 1 pending)
        r = await svc.collect_appeal_feedback()
        record("回流-幂等", r["submitted"] == 0 and r["skipped"] == 3,
               str(r))

        # 状态: 2 decided 2 fed
        st = await svc.learning_status()
        record("状态-计数", st["appeals"]["decided"] == 2
               and st["appeals"]["fed"] == 2, str(st.get("appeals")))
        record("状态-档案元数据",
               st["registry"]["batch"] == 9, str(st.get("registry")))
        record("状态-权重视图",
               "current" in st.get("weights", {})
               or isinstance(st.get("weights"), dict),
               str(st.get("weights"))[:100])

        # Hedge 学习一轮(测试口径: 先调低 min_feedback)
        from services.ai_learning_service import update_learning_config
        await update_learning_config("invoice_decision_gate",
                                     {"min_feedback": 1})
        r = await svc.run_learning()
        record("学习-一轮成功", r.get("success") is True
               or "updated" in r or "cycle" in r, str(r)[:150])

        # 学习后权重仍归一
        from services.ai_learning_service import get_weights_view
        view = await get_weights_view("invoice_decision_gate")
        record("学习-权重视图可读", isinstance(view, dict),
               str(view)[:100])


class TestHttpRoutes:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.invoice_routes import register_invoice_routes
        from services.invoice_service import Invoice42Service
        from services.order_service import OrderService

        app = FastAPI()
        register_invoice_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        svc = Invoice42Service()
        await svc.add_title(1, "company", "泰安竹香酒业",
                           "91370900MA3TEST01")
        osvc = OrderService()
        oid1, oid2, oid3 = await setup_appeals(svc, osvc)

        # collect
        resp = client.post("/api/invoice/admin/learning/collect",
                           headers=admin)
        body = resp.json()
        record("HTTP-collect回流", resp.status_code == 200
               and body["submitted"] == 2, str(body)[:120])

        # 幂等
        resp = client.post("/api/invoice/admin/learning/collect",
                           headers=admin)
        record("HTTP-collect幂等", resp.status_code == 200
               and resp.json()["submitted"] == 0,
               str(resp.json())[:100])

        # run(测试口径: 先调低 min_feedback)
        from services.ai_learning_service import update_learning_config
        await update_learning_config("invoice_decision_gate",
                                     {"min_feedback": 1})
        resp = client.post("/api/invoice/admin/learning/run",
                           headers=admin)
        record("HTTP-run学习", resp.status_code == 200,
               str(resp.text[:120]))

        # status
        resp = client.get("/api/invoice/admin/learning/status",
                           headers=admin)
        body = resp.json()
        record("HTTP-status", resp.status_code == 200
               and body["appeals"]["fed"] == 2,
               str(body.get("appeals")))

        # 鉴权
        resp = client.post("/api/invoice/admin/learning/collect")
        record("HTTP-collect非admin403", resp.status_code == 403,
               str(resp.status_code))


async def main():
    test_classes = [
        ("学习回流", TestLearning),
        ("HTTP层", TestHttpRoutes),
    ]
    print("=" * 62)
    print("42号·AI无感开票模块 P2 专项测试(申诉裁决真值→第25档案)")
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
