"""43号·AI智能安全管理模块 P1 专项测试

运行方式:
    python test_security_p1.py

覆盖(设计文档 §6.1 P1 范围):
    - 挑战验证: mock应答通过→通行证/应答空409/通行证豁免挑战档/
      通行证不豁免block档/verify事件留痕
    - 误报申诉: 提交(仅challenge/block档/当事人/一事件一申诉幂等)
      → 裁决(approve恢复信誉+解封 / reject归档) → 已裁决409
    - 事件裁决: confirm/false_positive/误报自动恢复/已裁决409
    - 管理端: 手动封禁/解封(未封404)/钉住解钉
    - 态势统计: 误报率口径(对齐42号范式)
    - 会员状态: 信誉/封禁/通行证/我的事件申诉
    - HTTP 层: 14端点鉴权与语义
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["SECURITY_GATEWAY_MODE"] = "on"
os.environ["SECURITY_ENFORCE_LEVEL"] = "observe"
os.environ["SECURITY_REPUTATION_COOLDOWN"] = "0"
os.environ["SECURITY_RECOVER_EVERY"] = "100"
os.environ["SECURITY_BAN_TTL"] = "900"

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


class TestChallenge:
    async def run(self, svc):
        from services.security_service import ACTION_CHALLENGE

        print("[01 挑战验证]")
        # 应答为空 → 409
        try:
            await svc.verify_challenge("1.1.1.1", token="t", answer="")
            record("空应答拒绝", False, "应抛 ValueError")
        except ValueError:
            record("空应答拒绝", True)

        # mock 应答通过 → 通行证
        r = await svc.verify_challenge("1.1.1.1", token="t", answer="ok")
        record("验证通过", r["success"] is True)
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()
        record("通行证已颁发",
               await repo.has_challenge_pass("1.1.1.1") is True)

        # verify 事件留痕(真人信号 confirmed)
        events = await svc.list_events(limit=10)
        verify_ev = [e for e in events
                     if e.get("action") == "verify_pass"]
        record("verify留痕confirmed", verify_ev
               and verify_ev[-1].get("verdict") == "confirmed")

        # 通行证豁免挑战档(enforce 模式下)
        os.environ["SECURITY_ENFORCE_LEVEL"] = "enforce"
        try:
            # 构造纯挑战档(特征命中单类, 其余因子正常)
            r = await svc.process_request(
                "1.1.1.1", method="GET", path="/api/product/search",
                query="kw=%27%20OR%201%3D1%20--", ua="Mozilla/5.0",
                member_id=1, hour=14)
            record("通行证豁免挑战",
                   r["action"] != ACTION_CHALLENGE, str(r["action"]))
            # 豁免事件留痕 challenge_exempt(审计可见)
            record("豁免事件留痕(exempt)",
                   (r.get("event") or {}).get("action")
                   == "challenge_exempt",
                   str(r.get("event"))[:80])
            # 豁免事件可申诉(mock 口径)
            ev = r.get("event") or {}
            if ev.get("eventId"):
                r2 = await svc.submit_appeal(1, ev["eventId"],
                                             reason="豁免事件申诉")
                record("豁免事件可申诉", r2["success"] is True,
                       str(r2)[:80])

            # 通行证不豁免 block(探针+注入+扫描器多特征)
            r = await svc.process_request(
                "2.2.2.2", method="GET", path="/wp-admin/setup.php",
                query="id=1 UNION SELECT * FROM users",
                ua="sqlmap/1.7", hour=3)
            record("通行证不豁免block(新IP)",
                   r["action"] == "block", str(r["action"]))
        finally:
            os.environ["SECURITY_ENFORCE_LEVEL"] = "observe"


class TestAppeal:
    async def run(self, svc):
        print("[02 误报申诉]")
        # 造一个会员 1 的 challenge 事件(enforce 下真实管线)
        os.environ["SECURITY_ENFORCE_LEVEL"] = "enforce"
        try:
            r = await svc.process_request(
                "3.3.3.1", method="GET", path="/api/product/search",
                query="kw=%27%20OR%201%3D1%20--",
                ua="Mozilla/5.0", member_id=1, hour=14)
        finally:
            os.environ["SECURITY_ENFORCE_LEVEL"] = "observe"
        event = r.get("event") or {}
        record("前置-挑战事件留痕", bool(event.get("eventId")),
               str(event)[:80])
        eid = event.get("eventId")

        # 提交申诉
        r = await svc.submit_appeal(1, eid, reason="正常搜索被拦")
        record("申诉提交", r["success"] is True)
        aid = r["appeal"]["appealId"]

        # 幂等: 重复申诉 409
        try:
            await svc.submit_appeal(1, eid, reason="再次申诉")
            record("重复申诉拒绝", False, "应抛 ValueError")
        except ValueError:
            record("重复申诉拒绝", True)

        # 非当事人 409
        try:
            await svc.submit_appeal(2, eid)
            record("非当事人拒绝", False, "应抛 ValueError")
        except ValueError:
            record("非当事人拒绝", True)

        # 事件不存在 404
        try:
            await svc.submit_appeal(1, 99999)
            record("事件不存在404", False, "应抛 KeyError")
        except KeyError:
            record("事件不存在404", True)

        # 裁决: 误报恢复(approve) → 信誉返还 + 事件 false_positive
        rep_before = (await svc.ensure_reputation("3.3.3.1"))["score"]
        r = await svc.decide_appeal(aid, True, reviewer="admin",
                                    note="核实误报")
        record("申诉裁决approve",
               r["appeal"]["status"] == "approved")
        ev = await svc.repo.get_event(eid)
        record("事件置false_positive",
               ev.get("verdict") == "false_positive")
        rep_after = (await svc.ensure_reputation("3.3.3.1"))["score"]
        record("误报信誉返还", rep_after > rep_before,
               f"{rep_before}→{rep_after}")

        # 已裁决申诉再裁决 409
        try:
            await svc.decide_appeal(aid, True)
            record("重复裁决拒绝", False, "应抛 ValueError")
        except ValueError:
            record("重复裁决拒绝", True)

        # reject 路径: 造新事件 + 申诉 + 维持拦截
        os.environ["SECURITY_ENFORCE_LEVEL"] = "enforce"
        try:
            r = await svc.process_request(
                "3.3.3.2", method="GET", path="/api/product/search",
                query="kw=%27%20OR%201%3D1%20--",
                ua="Mozilla/5.0", member_id=1, hour=14)
        finally:
            os.environ["SECURITY_ENFORCE_LEVEL"] = "observe"
        eid2 = (r.get("event") or {}).get("eventId")
        r = await svc.submit_appeal(1, eid2, reason="再试试")
        aid2 = r["appeal"]["appealId"]
        rep_before = (await svc.ensure_reputation("3.3.3.2"))["score"]
        r = await svc.decide_appeal(aid2, False, reviewer="admin")
        record("申诉裁决reject归档",
               r["appeal"]["status"] == "rejected")
        ev = await svc.repo.get_event(eid2)
        record("事件置confirmed", ev.get("verdict") == "confirmed")
        rep_after = (await svc.ensure_reputation("3.3.3.2"))["score"]
        record("维持拦截不返还", rep_after == rep_before,
               f"{rep_before}→{rep_after}")


class TestEventDecide:
    async def run(self, svc):
        print("[03 事件直接裁决]")
        # 造事件(observe 下也留痕)
        r = await svc.process_request(
            "4.4.4.1", method="GET", path="/api/product/search",
            query="kw=%27%20OR%201%3D1%20--", ua="Mozilla/5.0", hour=14)
        eid = (r.get("event") or {}).get("eventId")
        record("前置-事件留痕", bool(eid))

        # 误报裁决 → 信誉不降(observe 未扣) + verdict 更新
        r = await svc.decide_event(eid, False, reviewer="admin",
                                   note="误判")
        record("事件误报裁决",
               (r["event"].get("verdict")) == "false_positive")

        # 已裁决 409
        try:
            await svc.decide_event(eid, True)
            record("事件重复裁决拒绝", False, "应抛 ValueError")
        except ValueError:
            record("事件重复裁决拒绝", True)

        # confirm 路径
        r = await svc.process_request(
            "4.4.4.2", method="GET", path="/api/product/search",
            query="kw=%27%20OR%201%3D1%20--", ua="Mozilla/5.0", hour=14)
        eid2 = (r.get("event") or {}).get("eventId")
        r = await svc.decide_event(eid2, True, reviewer="admin")
        record("事件确认攻击",
               r["event"].get("verdict") == "confirmed")

        # 不存在 404
        try:
            await svc.decide_event(99999, True)
            record("事件不存在404", False, "应抛 KeyError")
        except KeyError:
            record("事件不存在404", True)


class TestAdminOps:
    async def run(self, svc):
        print("[04 管理端IP处置]")
        r = await svc.admin_ban_ip("5.5.5.1", reason="手动测试")
        record("手动封禁", r["success"] is True)
        record("封禁生效",
               await svc.is_blocked("5.5.5.1") is True)
        r = await svc.admin_unban_ip("5.5.5.1")
        record("手动解封", r["success"] is True)
        try:
            await svc.admin_unban_ip("5.5.5.1")
            record("解封未封404", False, "应抛 KeyError")
        except KeyError:
            record("解封未封404", True)

        # 钉住/解钉
        rep = await svc.pin_reputation("5.5.5.2", True)
        record("钉住", rep.get("pinned") is True)
        rep = await svc.pin_reputation("5.5.5.2", False)
        record("解钉", rep.get("pinned") is False)

        # 态势统计: 误报率口径
        stats = await svc.stats()
        record("误报率字段", "falsePositiveRate" in stats["events"])
        record("申诉统计字段", "appeals" in stats
               and "pending" in stats["appeals"])
        record("裁决计数", "confirmed" in stats["events"]
               and "falsePositive" in stats["events"])

        # 会员状态
        status = await svc.my_status(1, ip="3.3.3.1")
        record("会员状态四要素", all(k in status for k in (
            "reputation", "blocked", "challengePass", "myEvents")))
        record("我的申诉统计", "myAppeals" in status
               and status["myAppeals"]["total"] >= 1,
               str(status.get("myAppeals")))


class TestHttpRoutes:
    async def run(self):
        print("[05 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes
        from services.security_service import Security43Service

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        member = {"X-Member-Id": "1"}

        # 鉴权
        resp = client.get("/api/security/admin/dashboard")
        record("HTTP-缺Role 403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.get("/api/security/status")
        record("HTTP-缺Member 403", resp.status_code == 403)

        # 挑战验证(mock 应答)
        resp = client.post("/api/security/challenge/verify",
                           json={"token": "t", "answer": "ok"})
        record("HTTP-挑战验证", resp.status_code == 200,
               str(resp.text)[:100])

        # 状态
        resp = client.get("/api/security/status",
                          headers=member)
        record("HTTP-我的状态", resp.status_code == 200
               and resp.json().get("success") is True,
               str(resp.text)[:100])

        # 造事件 + 申诉(HTTP 层)
        svc = Security43Service()
        r = await svc.process_request(
            "6.6.6.1", method="GET", path="/api/product/search",
            query="kw=%27%20OR%201%3D1%20--", ua="Mozilla/5.0",
            member_id=1, hour=14)
        eid = (r.get("event") or {}).get("eventId")

        resp = client.post("/api/security/appeals",
                           json={"eventId": eid, "reason": "HTTP申诉"},
                           headers=member)
        body = resp.json()
        record("HTTP-申诉提交", resp.status_code == 200
               and body.get("appeal", {}).get("status") == "pending",
               str(body)[:120])
        aid = body.get("appeal", {}).get("appealId")

        resp = client.get("/api/security/appeals", headers=member)
        record("HTTP-我的申诉", resp.status_code == 200
               and resp.json().get("total") >= 1)

        # 管理端
        resp = client.get("/api/security/admin/dashboard",
                          headers=admin)
        record("HTTP-态势总览", resp.status_code == 200
               and "falsePositiveRate" in resp.json()["events"])
        resp = client.get("/api/security/admin/events?action=challenge",
                          headers=admin)
        record("HTTP-事件流水过滤", resp.status_code == 200
               and resp.json().get("total") >= 1)
        resp = client.post(
            f"/api/security/admin/events/{eid}/decide",
            json={"confirm": False, "reviewer": "admin",
                  "note": "误报"}, headers=admin)
        record("HTTP-事件裁决", resp.status_code == 200)
        resp = client.get("/api/security/admin/ips", headers=admin)
        record("HTTP-IP列表", resp.status_code == 200
               and resp.json().get("total") >= 1)
        resp = client.post("/api/security/admin/ips/7.7.7.7/ban",
                           json={"reason": "HTTP测试"}, headers=admin)
        record("HTTP-手动封禁", resp.status_code == 200)
        resp = client.post("/api/security/admin/ips/7.7.7.7/unban",
                           headers=admin)
        record("HTTP-手动解封", resp.status_code == 200)
        resp = client.post("/api/security/admin/ips/7.7.7.7/pin",
                           json={"pinned": True}, headers=admin)
        record("HTTP-钉住", resp.status_code == 200)
        resp = client.get("/api/security/admin/blocks", headers=admin)
        record("HTTP-封禁列表", resp.status_code == 200)
        resp = client.get("/api/security/admin/appeals",
                          headers=admin)
        record("HTTP-申诉队列", resp.status_code == 200
               and resp.json().get("total") >= 1)
        resp = client.post(
            f"/api/security/admin/appeals/{aid}/decide",
            json={"approve": True, "reviewer": "admin"},
            headers=admin)
        record("HTTP-申诉裁决", resp.status_code == 200
               and resp.json().get("appeal", {}).get("status")
               == "approved", str(resp.text)[:120])


async def run_all():
    from services.security_service import Security43Service
    svc = Security43Service()
    await TestChallenge().run(svc)
    await TestAppeal().run(svc)
    await TestEventDecide().run(svc)
    await TestAdminOps().run(svc)
    await TestHttpRoutes().run()


def main():
    reset_store()
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
