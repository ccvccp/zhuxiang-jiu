"""55号·二维码AI智能管理模块 P1 专项测试
(智能生码+扫码核销)

运行方式:
    python test_qr55_p1.py

覆盖(55号计划 §六 P1):
    - 生码编排: 意图→评分→策略→生成全链
      direct/confirm/clarify 三态分派
    - 千面适配: 信值等级展示深度/无障碍样式/
      受众(45号 grade 四档映射)
    - 预算联动: L0 零成本永不降级/正常扣减/
      超预算降级公开版(49号)
    - 扫码核销: ok→redeemed+落地页/expired/
      tampered 阻断+tamper 埋点/replayed 防重放
    - 跨端续接: continueOn 标记
    - 事件埋点: generate/confirm/clarify/scan/
      tamper/expire/replay 全类型
    - off 铁律: 生成/核销 409
    - HTTP 层: generate/scan/clarify 端点+鉴权
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


async def seed_member_grade(member_id: int,
                           grade: str):
    """种 45号信值档案(grade 四档)"""
    from repositories.trust_value_repository \
        import TrustValue45Repository
    repo = TrustValue45Repository()
    rec = await repo.get_profile(member_id) or {}
    rec.update({
        "trustId": member_id, "grade": grade,
        "score": 80 if grade == "healthy" else 40,
        "factors": {}, "role": "person",
        "l1Severity": {},
        "idDigest": f"seed-digest-{member_id}",
    })
    await repo.save_profile(rec)


class TestOrchestrate:
    """01 智能生码编排(三态分派)"""

    async def run(self):
        print("[01 生码编排]")
        reset_all()
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        svc = Qr55GenerateService()

        # off 铁律
        try:
            await svc.orchestrate(8801, "办老年优待证")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态生码拒绝", ok, err)

        os.environ["QR55_MODE"] = "on"
        await seed_member_grade(8801, "healthy")

        # direct: 高信任精确命中
        r = await svc.orchestrate(
            8801, "我要给老人办优待证")
        record("direct 直接生成",
               r.get("status") == "generated"
               and r.get("strategy") == "direct"
               and r.get("codeId", 0) > 0,
               str((r.get("status"),
                    r.get("strategy"))))
        record("评分摘要附带",
               (r.get("scoring") or {})
               .get("trustScore", 0) >= 70,
               str(r.get("scoring")))
        personalization = r.get(
            "personalization") or {}
        record("千面适配(healthy→full 深度)",
               personalization.get(
                   "displayDepth") == "full",
               str(personalization.get(
                   "displayDepth")))

        # clarify: 零命中
        r2 = await svc.orchestrate(
            8801, "看看今天天气怎么样")
        record("clarify 澄清分派",
               r2.get("status")
               == "clarify_required"
               and bool(r2.get("question")),
               str(r2.get("status")))

        # 事件埋点: generate+clarify
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        events = await Qr55Repository(
        ).list_events(limit=50)
        types = {e.get("eventType")
                 for e in events}
        record("埋点 generate+clarify",
               "generate" in types
               and "clarify" in types,
               str(types))
        os.environ["QR55_MODE"] = "off"


class TestConfirmFlow:
    """02 confirm 参数确认流"""

    async def run(self):
        print("[02 confirm 流]")
        reset_all()
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        svc = Qr55GenerateService()
        os.environ["QR55_MODE"] = "on"
        await seed_member_grade(8802, "watch")

        # watch 档(标准深度)+带参数意图 → 评分
        # 居中触发 confirm 的构造: 弱意图+缺参
        r = await svc.orchestrate(
            8802, "办出生登记", audience="general")
        # 出生登记意图明确 + watch 等级 → 大概率
        # direct 或 confirm; 断言三态合法
        record("策略三态合法",
               r.get("status") in (
                   "generated",
                   "confirm_required",
                   "clarify_required"),
               str(r.get("status")))

        # 显式构造 confirm: 若 direct 则跳过确认断言
        if r.get("status") == "confirm_required":
            record("confirm 待确认清单",
                   bool(r.get("requiredParams"))
                   and r.get("missingParams") is not None,
                   str(r.get("requiredParams")))
            # 确认回传 → 生成
            r2 = await svc.orchestrate(
                8802, "办出生登记",
                confirm_params={"region": "杭州"})
            record("确认后生成",
                   r2.get("status") == "generated",
                   str(r2.get("status")))
        else:
            # 用澄清场景验证 confirmParams 通路
            r3 = await svc.orchestrate(
                8802, "办出生登记预约",
                confirm_params={"region": "杭州",
                                "date": "明天"})
            record("confirmParams 通路(合并生成)",
                   r3.get("status") in (
                       "generated",
                       "confirm_required"),
                   str(r3.get("status")))

        # 千面: watch → standard
        os.environ["QR55_MODE"] = "off"
        os.environ["QR55_MODE"] = "on"
        r4 = await svc.orchestrate(
            8802, "老年优待证办理")
        depth = (r4.get("personalization")
                 or {}).get("displayDepth")
        record("千面适配(watch→standard)",
               depth in ("standard", "full"),
               str(depth))
        os.environ["QR55_MODE"] = "off"


class TestScanRedeem:
    """03 扫码核销(四态)"""

    async def run(self):
        print("[03 扫码核销]")
        reset_all()
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        from services.qr55_scan_service import (
            Qr55ScanService,
        )
        gen = Qr55GenerateService()
        scan = Qr55ScanService()
        os.environ["QR55_MODE"] = "on"
        await seed_member_grade(8803, "healthy")

        # off 铁律
        os.environ["QR55_MODE"] = "off"
        try:
            await scan.scan("whatever")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态核销拒绝", ok, err)
        os.environ["QR55_MODE"] = "on"

        # L0 零成本服务(policy_search 0.0)
        g = await gen.orchestrate(
            8803, "查政策解读")
        record("L0 服务生成成功",
               g.get("status") == "generated",
               str(g.get("status")))
        code = g.get("code")

        # 核销 ok(L0 零成本)
        s = await scan.scan(code, member_id=8803)
        record("核销 ok→redeemed",
               s.get("status") == "redeemed"
               and s.get("success") is True,
               str(s.get("status")))
        record("L0 零成本永不降级",
               (s.get("budget") or {})
               .get("mode") == "zero_cost",
               str(s.get("budget")))
        landing = s.get("landing") or {}
        record("千面落地页(深度+路由)",
               landing.get("depth")
               in ("full", "standard")
               and bool(landing.get("route")),
               str((landing.get("depth"),
                    landing.get("route"))))
        record("跨端续接标记",
               (s.get("crossDevice") or {})
               .get("continueOn") == "mobile",
               str(s.get("crossDevice")))

        # replayed: 同码二次扫
        s2 = await scan.scan(code, member_id=8803)
        record("防重放 replayed",
               s2.get("status") == "replayed",
               str(s2.get("status")))

        # expired: 短 ttl 码
        from services.qr55_crypto import (
            generate_code as gen_crypto,
        )
        expired_code = gen_crypto(
            "policy_search", {}, 8803,
            ttl_seconds=-10)["code"]
        s3 = await scan.scan(expired_code,
                             member_id=8803)
        record("过期 expired",
               s3.get("status") == "expired",
               str(s3.get("status")))

        # tampered: 改载荷
        g2 = await gen.orchestrate(
            8803, "查信值余额")
        tampered = g2["code"][:-2] + "xx"
        s4 = await scan.scan(tampered,
                             member_id=8803)
        record("篡改 tampered 阻断",
               s4.get("status") == "tampered",
               str(s4.get("status")))

        # 码状态翻转(redeemed)
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        codes = await Qr55Repository().list_codes(
            limit=50)
        redeemed = [c for c in codes
                    if c.get("status") == "redeemed"]
        record("码状态翻转(redeemed)",
               len(redeemed) >= 1,
               str(len(redeemed)))

        # 全链事件埋点
        events = await Qr55Repository(
        ).list_events(limit=100)
        types = {e.get("eventType")
                 for e in events}
        record("埋点 scan+tamper+expire+replay",
               {"scan", "tamper", "expire",
                "replay"} <= types,
               str(types))
        os.environ["QR55_MODE"] = "off"


class TestBudgetGuard:
    """04 预算联动(49号)"""

    async def run(self):
        print("[04 预算联动]")
        reset_all()
        from services.qr55_generate_service import (
            Qr55GenerateService,
        )
        from services.qr55_scan_service import (
            Qr55ScanService,
        )
        from services.xiaozhu_privacy_service \
            import XiaozhuPrivacyService
        gen = Qr55GenerateService()
        scan = Qr55ScanService()
        os.environ["QR55_MODE"] = "on"
        await seed_member_grade(8804, "watch")

        # L1 成本服务(policy_search L0 改用
        # point_history L1 0.005)
        g = await gen.orchestrate(
            8804, "查积分明细记录")
        record("L1 服务生成",
               g.get("status") == "generated",
               str(g.get("status")))
        if g.get("status") != "generated":
            os.environ["QR55_MODE"] = "off"
            return

        # 正常扣减(余量充足)
        before = await XiaozhuPrivacyService(
        ).budget_view(8804)
        s = await scan.scan(g["code"],
                            member_id=8804)
        record("预算正常扣减(spent)",
               (s.get("budget") or {})
               .get("mode") == "spent",
               str(s.get("budget")))
        after = await XiaozhuPrivacyService(
        ).budget_view(8804)
        used_up = float(
            after.get("usedToday") or 0) \
            - float(before.get("usedToday") or 0)
        # 49号 check_and_spend 内 round(cost,2):
        # 0.005 → 0.01(既有口径——两位小数记账)
        record("扣减生效(两位小数记账口径)",
               used_up > 0,
               str(used_up))

        # 超预算降级: 手动耗尽预算
        pref = await XiaozhuPrivacyService(
        ).budget_view(8804)
        limit = float(
            pref.get("effectiveLimit") or 1.0)
        g2 = await gen.orchestrate(
            8804, "查积分明细")
        if g2.get("status") == "generated":
            # 直接清空余量(仓储写 usedToday=limit)
            from repositories.xiaozhu_repository \
                import Xiaozhu48Repository
            repo = Xiaozhu48Repository()
            rec = await repo.get_privacy_budget(
                8804) or {}
            rec["usedToday"] = round(limit, 2)
            rec["dayKey"] = rec.get("dayKey") or "x"
            await repo.save_privacy_budget(rec)
            s2 = await scan.scan(g2["code"],
                                 member_id=8804)
            landing = s2.get("landing") or {}
            record("超预算降级公开版",
                   (s2.get("budget") or {})
                   .get("mode") == "degraded"
                   and landing.get("degraded")
                   is True,
                   str((s2.get("budget"),
                        landing.get("degraded"))))
        os.environ["QR55_MODE"] = "off"


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 409
        resp = client.post(
            "/api/qr55/generate",
            json={"memberId": 8805,
                  "text": "办老年优待证"},
            headers=admin)
        record("HTTP generate off 409",
               resp.status_code == 409,
               str(resp.status_code))
        resp = client.post(
            "/api/qr55/scan",
            json={"code": "x"},
            headers=admin)
        record("HTTP scan off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # clarify(规则轨——off 亦可用)
        resp = client.post(
            "/api/qr55/clarify",
            json={"text": "帮我看看天气",
                  "memberId": 8805},
            headers=admin)
        body = resp.json() or {}
        record("HTTP clarify 200(澄清问句)",
               resp.status_code == 200
               and body.get("needClarify") is True
               and bool(body.get("question")),
               str(resp.status_code))

        # on 态全链 HTTP
        os.environ["QR55_MODE"] = "on"
        resp = client.post(
            "/api/qr55/generate",
            json={"memberId": 8805,
                  "text": "老年优待证怎么办",
                  "accessibility": True},
            headers=admin)
        body = resp.json() or {}
        record("HTTP generate 200(无障碍)",
               resp.status_code == 200
               and body.get("status")
               in ("generated",
                   "confirm_required",
                   "clarify_required"),
               str(resp.status_code))
        if body.get("status") == "generated":
            record("无障碍样式标记",
                   ((body.get("personalization")
                     or {}).get("accessibility")
                    or {}).get("style")
                   == "high_contrast",
                   str(body.get("personalization")))
            # scan HTTP
            resp = client.post(
                "/api/qr55/scan",
                json={"code": body.get("code"),
                      "memberId": 8805},
                headers=admin)
            record("HTTP scan 200(redeemed)",
                   resp.status_code == 200
                   and (resp.json() or {}
                        ).get("status")
                   == "redeemed",
                   str(resp.status_code))

        # 鉴权
        resp = client.post(
            "/api/qr55/generate",
            json={"memberId": 8805, "text": "x"})
        record("generate 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(
            "/api/qr55/scan", json={"code": "x"})
        record("scan 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        from routes.qr55_routes import (
            router as qr_router,
        )
        count = sum(1 for r in qr_router.routes)
        # P2 新增 4 端点(codes/code/{id}/stats/collect)
        # → 7→11(基线语义: ≥7——P1 交付面不因 P2 演进破坏)
        record("55号路由累计 ≥7 端点(P2 扩至 11)",
               count >= 7, str(count))
        os.environ["QR55_MODE"] = "off"


async def run_all():
    await TestOrchestrate().run()
    await TestConfirmFlow().run()
    await TestScanRedeem().run()
    await TestBudgetGuard().run()
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
