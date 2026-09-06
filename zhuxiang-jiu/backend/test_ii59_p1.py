"""59号·AI智能服务编排模块 P1 专项测试
(客服会话引擎)

运行方式:
    python test_ii59_p1.py

覆盖(59号计划 §九 P1):
    - 意图消费路由: 58号 evaluate 纯消费
      +上游铁律(clarify/partial 不编排/
      boundary 拒绝/confirm_required 衔接)
    - TASK_TEMPLATES 任务编排(步骤推进
      +完成 resolved+失败 fail-soft)
    - 人工接管 escalate(脱敏移交+排队
      +off 态铁律)
    - 闭话+满意度采集(必采校验+反馈留痕)
    - HTTP 层: 4 新端点+鉴权+9 端点计数
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ.pop("II58_LLM_MODE", None)
os.environ.pop("II59_LLM_MODE", None)

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


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def seed_58_corpus(intent_id: str,
                         text: str,
                         sample_type: str
                         = "positive") -> int:
    """种 58号语料(意图命中域)"""
    from core.helpers import ts
    from repositories.ii58_repository import (
        Ii58Repository,
    )
    repo = Ii58Repository()
    corpus_id = await repo.next_corpus_id()
    await repo.save_corpus({
        "corpusId": corpus_id,
        "corpusVersion": 1,
        "intentId": intent_id,
        "sampleType": sample_type,
        "text": text,
        "weight": 1.0,
        "source": "manual",
        "originRef": "",
        "confusableTarget": None,
        "humanVerified": True,
        "humanSuggested": False,
        "status": "active",
        "createdAt": ts(),
        "updatedAt": ts(),
    })
    return corpus_id


async def new_session(member_id: int = 1
                     ) -> int:
    """开话(shadow 态)"""
    from services.ii59_service import (
        Ii59Service,
    )
    r = await Ii59Service().open_session(
        member_id=member_id)
    return r["sessionId"]


class TestRoute:
    """01 意图消费路由"""

    async def run(self):
        print("[01 意图路由]")
        reset_all()
        from services.ii59_conversation_service \
            import (
                Ii59ConversationService,
            )
        svc = Ii59ConversationService()

        # off 拒绝
        try:
            await svc.route_intent(1, "多少钱")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态路由拒绝", ok, err)

        os.environ["II59_MODE"] = "shadow"
        os.environ["II58_MODE"] = "shadow"
        sid = await new_session()

        # 空文本拒绝
        try:
            await svc.route_intent(sid, "  ")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不能为空" in str(e), \
                str(e)[:30]
        record("空文本拒绝", ok, err)

        # 404
        try:
            await svc.route_intent(999, "多少钱")
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("会话 404", ok, err)

        # ① clarify 态不编排(空语料库)
        r1 = await svc.route_intent(
            sid, "完全无关文本")
        record("clarify 不编排(保持 opened)",
               r1.get("routed") is False
               and r1.get("reason")
               == "upstream_clarify",
               str(r1.get("reason")))

        # ② 语料命中→resolved 路由
        await seed_58_corpus(
            "product.price_query", "多少钱")
        r2 = await svc.route_intent(sid, "多少钱")
        record("resolved 路由(routed+serving)",
               r2.get("routed") is True
               and int(r2.get("taskId")
                       or 0) > 0
               and r2.get("services")
               == ["sr.product_search"],
               str((r2.get("routed"),
                    r2.get("services"))))
        record("任务模板(价格查询两步)",
               (r2.get("template") or {})
               .get("label") == "价格查询流程"
               and len((r2.get("template")
                        or {}).get("steps")
                       or []) == 2,
               str(r2.get("template")))
        record("首步定位(search_product)",
               r2.get("currentStep")
               == "search_product",
               str(r2.get("currentStep")))

        # 会话态+taskStack
        from repositories.ii59_repository \
            import Ii59Repository
        repo = Ii59Repository()
        session = await repo.get_session(sid)
        record("会话 serving+taskStack",
               session.get("state") == "serving"
               and (session.get("taskStack")
                    or {}).get("taskId")
               == r2.get("taskId"),
               str(session.get("state")))

        # 任务实例结构
        task = await repo.get_task(
            r2.get("taskId"))
        record("任务实例(版本+步骤+归因)",
               task.get("templateVersion") == 1
               and task.get("steps")
               == ["search_product",
                   "render_price_card"]
               and "evalId" in (
                   task.get("attribution")
                   or {}),
               str((task.get("templateVersion"),
                    task.get("steps"))))

        # ③ 敏感意图 confirm 衔接
        sid3 = await new_session(member_id=3)
        await seed_58_corpus(
            "trust.convert_intent", "兑换信值")
        r3 = await svc.route_intent(
            sid3, "兑换信值")
        record("敏感意图(confirmRequired+"
               "双通道)",
               r3.get("confirmRequired")
               is True
               and r3.get("services")
               == ["cs.order_assist",
                   "rg.experience_gate"],
               str((r3.get("confirmRequired"),
                    r3.get("services"))))
        task3 = await repo.get_task(
            r3.get("taskId"))
        record("confirm 衔接(results._confirm)",
               "_confirm" in (
                   task3.get("results")
                   or {}),
               str(task3.get("results")))

        # ④ boundary 拦截拒绝路由
        sid4 = await new_session(member_id=4)
        await seed_58_corpus(
            "boundary.unauthorized",
            "删除所有会员数据")
        r4 = await svc.route_intent(
            sid4, "删除所有会员数据",
            member_role="guest")
        record("boundary 拒绝路由(铁律)",
               r4.get("routed") is False
               and r4.get("reason")
               == "boundary_intercepted",
               str(r4.get("reason")))
        session4 = await repo.get_session(sid4)
        record("拦截会话保持 opened",
               session4.get("state") == "opened",
               str(session4.get("state")))
        os.environ["II58_MODE"] = "off"
        os.environ["II59_MODE"] = "off"


class TestAdvance:
    """02 步骤推进+任务编排"""

    async def run(self):
        print("[02 步骤推进]")
        reset_all()
        from services.ii59_conversation_service \
            import (
                Ii59ConversationService,
            )
        svc = Ii59ConversationService()
        os.environ["II59_MODE"] = "shadow"
        os.environ["II58_MODE"] = "shadow"

        await seed_58_corpus(
            "product.price_query", "多少钱")
        sid = await new_session()
        r = await svc.route_intent(sid, "多少钱")
        tid = r.get("taskId")

        # 非法结果拒绝
        try:
            await svc.advance(sid, result="hacked")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法步骤结果" in str(e), \
                str(e)[:30]
        record("非法步骤结果拒绝", ok, err)

        # 步骤推进 1
        a1 = await svc.advance(sid, note="命中商品")
        record("步骤推进(search→render)",
               a1.get("currentStep")
               == "render_price_card"
               and a1.get("status") == "running",
               str((a1.get("currentStep"),
                    a1.get("status"))))

        # 步骤推进 2(完成→resolved)
        a2 = await svc.advance(sid, note="卡片渲染")
        record("任务完成(resolved)",
               a2.get("status") == "completed"
               and a2.get("sessionState")
               == "resolved",
               str((a2.get("status"),
                    a2.get("sessionState"))))

        # 任务留痕
        from repositories.ii59_repository \
            import Ii59Repository
        repo = Ii59Repository()
        task = await repo.get_task(tid)
        record("步骤结果留痕(results)",
               (task.get("results") or {})
               .get("search_product", {})
               .get("result") == "done"
               and (task.get("results")
                    or {}).get(
                   "render_price_card", {}
               ).get("result") == "done",
               str(task.get("results")))

        # 失败 fail-soft(转 escalated)
        sid2 = await new_session(member_id=2)
        await seed_58_corpus(
            "trust.balance_query", "查余额")
        r2 = await svc.route_intent(sid2, "查余额")
        f1 = await svc.advance(
            sid2, result="failed",
            note="账务服务超时")
        record("失败 fail-soft(escalated)",
               f1.get("state")
               == "escalated"
               and (f1.get("handoff")
                    or {}).get("reason"),
               str(f1.get("state")))

        # 脱敏移交(PII 处理)
        sid3 = await new_session(member_id=3)
        await seed_58_corpus(
            "trust.balance_query", "查余额")
        await svc.route_intent(sid3, "查余额")
        e1 = await svc.escalate(
            sid3,
            reason="会员手机 13800138000 投诉",
            context_note="卡号 "
                         "6222020200112233445")
        handoff = e1.get("handoff") or {}
        record("脱敏移交(PII mask)",
               "13800138000" not in str(handoff)
               and "6222020200112233445"
               not in str(handoff),
               str(handoff)[:60])
        record("排队位次(position)",
               int(e1.get("queuePosition")
                   or 0) >= 2,
               str(e1.get("queuePosition")))
        os.environ["II58_MODE"] = "off"
        os.environ["II59_MODE"] = "off"


class TestClose:
    """03 闭话+满意度"""

    async def run(self):
        print("[03 闭话满意度]")
        reset_all()
        from services.ii59_conversation_service \
            import (
                Ii59ConversationService,
            )
        svc = Ii59ConversationService()
        os.environ["II59_MODE"] = "shadow"
        os.environ["II58_MODE"] = "shadow"

        await seed_58_corpus(
            "product.price_query", "多少钱")
        sid = await new_session()
        await svc.route_intent(sid, "多少钱")
        await svc.advance(sid)
        await svc.advance(sid)   # resolved

        # 满意度缺失拒绝
        try:
            await svc.close(sid)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "满意度" in str(e), \
                str(e)[:30]
        record("满意度必采拒绝", ok, err)

        # 越界拒绝
        try:
            await svc.close(sid, satisfaction=6)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "[1,5]" in str(e), \
                str(e)[:30]
        record("满意度越界拒绝", ok, err)

        # off 态闭话亦可用(人工铁律)
        os.environ["II59_MODE"] = "off"
        c1 = await svc.close(
            sid, satisfaction=4.5,
            note="服务满意")
        record("off 态闭话(铁律)",
               c1.get("state") == "closed"
               and c1.get("satisfaction") == 4.5,
               str((c1.get("state"),
                    c1.get("satisfaction"))))
        os.environ["II59_MODE"] = "shadow"

        # 会话终态
        from repositories.ii59_repository \
            import Ii59Repository
        repo = Ii59Repository()
        session = await repo.get_session(sid)
        record("闭话终态(satisfaction 落库)",
               session.get("state") == "closed"
               and session.get("satisfaction")
               == 4.5,
               str((session.get("state"),
                    session.get(
                        "satisfaction"))))

        # 满意度反馈留痕(回流真值源)
        fbs = await repo.list_feedback(
            session_id=sid, limit=10)
        record("满意度反馈(kind=satisfaction)",
               len(fbs) == 1
               and fbs[0].get("kind")
               == "satisfaction"
               and fbs[0].get("satisfaction")
               == 4.5,
               str(len(fbs)))

        # 终态闭话拒绝
        try:
            await svc.close(sid,
                            satisfaction=3)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "状态" in str(e), \
                str(e)[:30]
        record("终态闭话拒绝", ok, err)

        # escalated 闭话(人工处置后)
        sid2 = await new_session(member_id=2)
        await svc.escalate(sid2, reason="投诉")
        c2 = await svc.close(
            sid2, satisfaction=2)
        record("escalated 闭话(可)",
               c2.get("state") == "closed",
               str(c2.get("state")))
        os.environ["II58_MODE"] = "off"


class TestHttp:
    """04 HTTP 层"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 决策面 off 409(先开话得 sid——
        # off 检查在会话存在性校验之前)
        os.environ["II59_MODE"] = "shadow"
        resp = client.post(
            "/api/ii59/sessions",
            json={"memberId": 9},
            headers=admin)
        probe_sid = (resp.json() or {}
                     ).get("sessionId")
        os.environ["II59_MODE"] = "off"
        for path in (
                f"/api/ii59/sessions/{probe_sid}"
                f"/route",
                f"/api/ii59/sessions/{probe_sid}"
                f"/advance"):
            resp = client.post(
                path, json={"text": "x"},
                headers=admin)
            record(f"HTTP {path.split('/')[-1]}"
                   f" off 409",
                   resp.status_code == 409,
                   str(resp.status_code))

        # shadow 全链
        os.environ["II59_MODE"] = "shadow"
        os.environ["II58_MODE"] = "shadow"
        resp = client.post(
            "/api/ii59/sessions",
            json={"memberId": 1},
            headers=admin)
        sid = (resp.json() or {}
               ).get("sessionId")

        # clarify 不编排
        resp = client.post(
            f"/api/ii59/sessions/{sid}/route",
            json={"text": "无关文本"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP clarify 不编排",
               resp.status_code == 200
               and body.get("routed") is False,
               str((resp.status_code,
                    body.get("routed"))))

        # resolved 路由+推进+闭话
        await seed_58_corpus(
            "product.price_query", "多少钱")
        resp = client.post(
            f"/api/ii59/sessions/{sid}/route",
            json={"text": "多少钱"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP route 200(routed)",
               resp.status_code == 200
               and body.get("routed") is True,
               str((resp.status_code,
                    body.get("routed"))))

        resp = client.post(
            f"/api/ii59/sessions/{sid}/advance",
            json={"result": "done"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP advance 200",
               resp.status_code == 200
               and body.get("currentStep")
               == "render_price_card",
               str(body.get("currentStep")))

        # escalate off 亦可用(铁律——
        # serving 态可接管)
        os.environ["II59_MODE"] = "off"
        resp = client.post(
            f"/api/ii59/sessions/{sid}/escalate",
            json={"reason": "测试"},
            headers=admin)
        record("HTTP escalate off 200(铁律)",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("state")
               == "escalated",
               str(resp.status_code))

        # escalated 人工处置→close(escalated
        # 在 close 合法域)
        os.environ["II59_MODE"] = "shadow"
        resp = client.post(
            f"/api/ii59/sessions/{sid}/close",
            json={"satisfaction": 4},
            headers=admin)
        body = resp.json() or {}
        record("HTTP close 200(铁律)",
               resp.status_code == 200
               and body.get("state")
               == "closed"
               and body.get("satisfaction")
               == 4,
               str((resp.status_code,
                    body.get("state"))))

        # 404(shadow 态——存在性校验生效)
        os.environ["II59_MODE"] = "shadow"
        resp = client.post(
            "/api/ii59/sessions/999/route",
            json={"text": "x"},
            headers=admin)
        record("HTTP route 404",
               resp.status_code == 404,
               str(resp.status_code))

        resp = client.post(
            "/api/ii59/sessions/999/close",
            json={"satisfaction": 3},
            headers=admin)
        record("HTTP close 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 鉴权 403
        for method, path in (
                ("POST",
                 "/api/ii59/sessions/1/route"),
                ("POST",
                 "/api/ii59/sessions/1"
                 "/advance"),
                ("POST",
                 "/api/ii59/sessions/1"
                 "/escalate"),
                ("POST",
                 "/api/ii59/sessions/1"
                 "/close")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 9 端点(P2 扩至 13——基线语义)
        from routes.ii59_routes import (
            router as ii_router,
        )
        count = sum(
            1 for r in ii_router.routes)
        record("59号路由累计 ≥9 端点",
               count >= 9, str(count))
        os.environ["II58_MODE"] = "off"
        os.environ["II59_MODE"] = "off"


async def run_all():
    await TestRoute().run()
    await TestAdvance().run()
    await TestClose().run()
    await TestHttp().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
