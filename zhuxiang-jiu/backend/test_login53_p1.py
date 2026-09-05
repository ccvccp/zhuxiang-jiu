"""53号·小竹智能登录引擎 P1 专项测试
(多模态认证编排引擎)

运行方式:
    python test_login53_p1.py

覆盖(53号计划 §九 P1):
    - 四级响应全路径: silent(<25 签发)/
      one_tap(25-50 确认令牌二段式)/
      step_up(50-70 短信话术)/
      enhanced(风控标记强制多因子+去污名化)
    - 五通道校验: passkey(39号 bio)/face(50号
      liveness)/voice(双因子)/qr(39号票据一次性)
    - 安全兜底: 反欺诈安全挑战(liveness<0.5→
      随机动作→应答核销)/失败优雅降级
      (同通道 3 次→备选建议+安抚话术)
    - 预算扣减: 通道成本 49号 check_and_spend
      (耗尽→409+降级话术+事件)
    - 事件流水: 六字段对齐 49号审计口径
    - off 铁律+端点+零影响(宪法断言)
"""

import asyncio
import hashlib
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["LOGIN53_MODE"] = "off"

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


async def seed_member(member_id: int,
                      nickname: str = "编排测试",
                      created_days_ago: int = 90):
    """种子: 会员+入口档案(基线指纹+账龄)"""
    from datetime import datetime, timedelta
    from repositories.member_repository import (
        MemberRepository,
    )
    created = (datetime.now()
               - timedelta(days=created_days_ago)
               ).isoformat()
    await MemberRepository().save(member_id, {
        "id": member_id,
        "phone": f"139{member_id:08d}",
        "nickname": nickname, "role": "member",
        "created_at": created, "points": 100,
        "status": 1,
    })


async def seed_bio_credential(member_id: int,
                              bio_type="face_id",
                              credential_id="BIOtest0001"):
    """种子: 39号 bio 凭证(编排层校验数据源)"""
    from repositories.entry_repository import (
        EntryRepository,
    )
    await EntryRepository().save_bio({
        "credentialId": credential_id,
        "memberId": member_id, "bioType": bio_type,
        "deviceId": "dev-test",
        "publicKeyHash": "a" * 32,
        "name": "测试凭证", "status": "active",
        "mode": "mock", "enrolledAt": "2026-09-05",
    })
    return credential_id


async def seed_qr_confirmed(member_id: int,
                            ticket="LTticket0001"):
    """种子: 39号已确认扫码会话(编排 qr 通道)"""
    from repositories.entry_repository import (
        EntryRepository,
    )
    qr_id = "QRtest0001"
    ticket_hash = hashlib.sha256(
        ticket.encode()).hexdigest()[:32]
    await EntryRepository().save_qr({
        "qrId": qr_id, "seq": 1, "status": "confirmed",
        "creatorDevice": "",
        "confirmMemberId": member_id,
        "loginTicketHash": ticket_hash,
        "expiresAt": 9999999999,
        "createdAt": "2026-09-05",
    })
    return qr_id, ticket


class TestFourTierResponse:
    """01 四级响应全路径"""

    async def run(self):
        print("[01 四级响应]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        # off 态拒绝
        try:
            await svc.orchestrate(5410, "passkey")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态编排拒绝", ok, err)

        # 通道非法
        os.environ["LOGIN53_MODE"] = "on"
        try:
            await svc.orchestrate(5410, "palm")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "通道非法" in str(e), str(e)[:30]
        record("通道非法拒绝", ok, err)

        # silent 档: 老会员+无失败+日间+基线匹配
        await seed_member(5410)
        cred = await seed_bio_credential(5410)
        await svc.register_baseline_fingerprint(
            5410, "baseline-fp-5410")
        r = await svc.orchestrate(
            5410, "passkey",
            credential={"credentialId": cred},
            fingerprint="baseline-fp-5410", hour=12)
        record("silent 档直接签发",
               r.get("status") == "authenticated"
               and r.get("tier") == "silent"
               and (r.get("tokens") or {}).get(
                   "accessToken"),
               str(r.get("status")))
        record("silent 零成本通道 privacyCost=0",
               (r.get("event") or {})
               .get("privacyCost") == 0.0,
               str((r.get("event") or {})
                   .get("privacyCost")))
        record("成功话术绑定(passkey_silent)",
               (r.get("script") or {})
               .get("key") == "passkey_silent",
               str((r.get("script") or {}).get("key")))

        # 事件六字段
        ev = r.get("event") or {}
        record("事件六字段齐备(49号审计口径)",
               all(k in ev for k in (
                   "method", "riskScore", "decision",
                   "durationMs", "privacyCost",
                   "explainRef")) and ev.get("success")
               is True and ev.get("method") == "passkey",
               str(list(ev))[:60])

        # one_tap 档: 新设备+夜间+2 次失败
        reset_all()
        await seed_member(5411)
        cred2 = await seed_bio_credential(
            5411, credential_id="BIOtest0002")
        # 基线登记后用不同指纹(新设备)+夜间+失败计数
        await svc.register_baseline_fingerprint(
            5411, "baseline-fp-5411")
        await svc._bump_fail_count(5411, "voice")
        await svc._bump_fail_count(5411, "voice")
        r2 = await svc.orchestrate(
            5411, "passkey",
            credential={"credentialId": cred2},
            fingerprint="totally-different-fp",
            hour=2)
        tier2 = r2.get("tier")
        if tier2 == "one_tap":
            record("one_tap 档确认令牌二段式",
                   r2.get("status")
                   == "one_tap_pending"
                   and r2.get("confirmToken")
                   and r2.get("confirmTtl") == 60,
                   str(r2.get("status")))
            token = r2["confirmToken"]
            # 令牌核销 → 签发
            r2b = await svc.orchestrate(
                5411, "passkey",
                credential={"credentialId": cred2},
                fingerprint="totally-different-fp",
                confirm_token=token)
            record("确认令牌核销→签发",
                   r2b.get("status") == "authenticated"
                   and r2b.get("tier") == "one_tap",
                   str(r2b.get("status")))
            # 令牌一次性(重放拒绝)
            try:
                await svc.orchestrate(
                    5411, "passkey",
                    credential={
                        "credentialId": cred2},
                    confirm_token=token)
                ok, err = False, "未拒绝"
            except ValueError as e:
                ok, err = "无效或已使用" in str(e), ""
            record("确认令牌一次性(重放拒绝)",
                   ok, err)
        else:
            record("one_tap 档确认令牌二段式",
                   False, f"实际档位 {tier2}")

        # step_up 档: 高失败+新设备+夜间
        reset_all()
        await seed_member(5412)
        cred3 = await seed_bio_credential(
            5412, credential_id="BIOtest0003")
        await svc.register_baseline_fingerprint(
            5412, "baseline-fp-5412")
        for _ in range(10):
            await svc._bump_fail_count(5412, "voice")
        r3 = await svc.orchestrate(
            5412, "passkey",
            credential={"credentialId": cred3},
            fingerprint="mismatched-fp-here", hour=2)
        record("step_up 档短信话术",
               r3.get("status")
               == "step_up_required"
               and "step-up-verify" in str(
                   r3.get("nextStep")),
               str(r3.get("status")))

        # enhanced 档: 43号风控标记强制多因子
        reset_all()
        await seed_member(5413)
        cred4 = await seed_bio_credential(
            5413, credential_id="BIOtest0004")
        await svc.repo.save_profile({
            "memberId": 5413, "riskFlagged": 1,
            "accountAgeDays": 365})
        r4 = await svc.orchestrate(
            5413, "passkey",
            credential={"credentialId": cred4},
            hour=12)
        record("enhanced 档风控标记强制",
               r4.get("status")
               == "enhanced_required"
               and r4.get("tier") == "enhanced",
               str(r4.get("status")))
        record("enhanced 去污名化话术",
               "这不是您的错" in str(
                   (r4.get("script") or {})
                   .get("text")),
               str((r4.get("script") or {})
                   .get("text"))[:40])
        os.environ["LOGIN53_MODE"] = "off"


class TestChannelVerification:
    """02 五通道校验"""

    async def run(self):
        print("[02 五通道校验]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()
        os.environ["LOGIN53_MODE"] = "on"

        # passkey: 凭证缺失/不存在/归属不匹配
        await seed_member(5420)
        try:
            await svc.orchestrate(5420, "passkey")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "credentialId" in str(e), ""
        record("passkey 缺凭证拒绝", ok, err)
        try:
            await svc.orchestrate(
                5420, "passkey",
                credential={
                    "credentialId": "BIOnope999"})
            ok, err = False, "未拒绝"
        except KeyError as e:
            ok, err = "BIOnope999" in str(e), ""
        record("passkey 凭证不存在 404", ok, err)
        cred = await seed_bio_credential(
            5420, credential_id="BIOtest0020")
        try:
            await svc.orchestrate(
                5421, "passkey",
                credential={"credentialId": cred})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "归属不匹配" in str(e), ""
        record("passkey 归属不匹配拒绝", ok, err)

        # face: 活体达标/不足/TTS 疑似挑战
        r = await svc.orchestrate(
            5420, "face",
            credential={"liveness": 0.92}, hour=12)
        record("face 活体 0.92 通过",
               r.get("status") == "authenticated",
               str(r.get("status")))
        try:
            await svc.orchestrate(
                5420, "face",
                credential={"liveness": 0.6})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "活体分不足" in str(e), ""
        record("face 活体 0.6 不足拒绝", ok, err)

        # TTS 疑似 → 安全挑战(随机动作)
        r2 = await svc.orchestrate(
            5420, "face",
            credential={"liveness": 0.3})
        record("TTS 疑似→安全挑战(不直接报错)",
               r2.get("status")
               == "security_challenge"
               and r2.get("challengeAction")
               in svc.SECURITY_CHALLENGE_ACTIONS
               and r2.get("challengeToken"),
               str(r2.get("status")))
        action = r2["challengeAction"]
        # 应答核销: 正确动作 → 签发
        r3 = await svc.orchestrate(
            5420, "face",
            credential={"liveness": 0.3},
            challenge_response=action)
        record("挑战应答正确→签发",
               r3.get("status") == "authenticated"
               and (r3.get("verification") or {})
               .get("securityChallengePassed") is True,
               str(r3.get("status")))
        # 错误动作拒绝
        r4 = await svc.orchestrate(
            5420, "face",
            credential={"liveness": 0.3})
        action4 = r4["challengeAction"]
        try:
            await svc.orchestrate(
                5420, "face",
                credential={"liveness": 0.3},
                challenge_response="错误动作")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "动作不匹配" in str(e), ""
        record("挑战应答错误拒绝", ok, err)

        # voice: 声纹+语义双因子
        try:
            await svc.orchestrate(
                5420, "voice",
                credential={
                    "voiceConfirmed": True})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "语义动态口令" in str(e), ""
        record("voice 缺口令→双因子引导", ok, err)
        try:
            await svc.orchestrate(
                5420, "voice", credential={})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "声纹初筛未通过" in str(e), ""
        record("voice 声纹未过拒绝", ok, err)
        r5 = await svc.orchestrate(
            5420, "voice",
            credential={
                "voiceConfirmed": True,
                "spokenPhrase": "我回来了"}, hour=12)
        record("voice 双因子通过",
               r5.get("status") == "authenticated"
               and (r5.get("verification") or {})
               .get("dualFactor") is True,
               str(r5.get("status")))

        # qr: 票据一次性消费
        qr_id, ticket = await seed_qr_confirmed(5420)
        r6 = await svc.orchestrate(
            5420, "qr",
            credential={"qrId": qr_id,
                        "loginTicket": ticket},
            hour=12)
        record("qr 票据通过",
               r6.get("status") == "authenticated",
               str(r6.get("status")))
        try:
            await svc.orchestrate(
                5420, "qr",
                credential={"qrId": qr_id,
                            "loginTicket": ticket})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "会话未确认" in str(e), ""
        record("qr 票据一次性(重放拒绝)", ok, err)
        os.environ["LOGIN53_MODE"] = "off"


class TestBudgetAndFallback:
    """03 预算扣减+失败优雅降级"""

    async def run(self):
        print("[03 预算+降级]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()
        os.environ["LOGIN53_MODE"] = "on"
        await seed_member(5430)

        # 预算耗尽 → 409+降级话术+事件
        from core.helpers import ts
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        await Xiaozhu48Repository(
        ).save_privacy_budget({
            "memberId": 5430,
            "dayKey": ts()[:10],
            "preference": 1.0, "usedToday": 1.0,
            "budget": 1.0, "ts": ts(),
        })
        try:
            await svc.orchestrate(
                5430, "face",
                credential={"liveness": 0.92})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "预算不足" in str(e) \
                       and "基础认证" in str(e), \
                str(e)[:40]
        record("预算耗尽→409+降级话术", ok, err)
        events = await svc.repo.list_events(5430)
        budget_ev = [e for e in events
                     if e.get("decision")
                     == "budget_block"]
        record("预算事件留痕(budget_block)",
               budget_ev
               and budget_ev[0].get("explainRef")
               == "budget_exhausted",
               str(len(budget_ev)))

        # 失败优雅降级: 同通道 3 次→备选建议
        reset_all()
        await seed_member(5431)
        for i in range(3):
            try:
                await svc.orchestrate(
                    5431, "face",
                    credential={"liveness": 0.6})
            except ValueError:
                pass
        try:
            await svc.orchestrate(
                5431, "face",
                credential={"liveness": 0.6})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "切换备选通道" in str(e) \
                       and "安抚" not in str(e), \
                str(e)[:60]
        record("3 次失败→备选切换建议", ok, err)
        events2 = await svc.repo.list_events(5431)
        fail_ev = [e for e in events2
                   if e.get("decision")
                   == "credential_fail"]
        record("凭证失败事件留痕(4 条)",
               len(fail_ev) == 4,
               str(len(fail_ev)))
        os.environ["LOGIN53_MODE"] = "off"


class TestEventsAndEndpoints:
    """04 事件流水+端点+零影响"""

    async def run(self):
        print("[04 事件+端点]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        # off 态 orchestrate 409
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        member = {"X-Member-Id": "5440"}
        resp = client.post(
            "/api/login53/auth/orchestrate",
            json={"channel": "passkey"},
            headers=member)
        record("HTTP orchestrate off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # my/events 观测面(off 可访问)
        resp = client.get("/api/login53/my/events",
                          headers=member)
        record("HTTP my/events 200(观测面)",
               resp.status_code == 200
               and (resp.json() or {}).get(
                   "total") == 0,
               str(resp.status_code))

        # on 态端到端
        os.environ["LOGIN53_MODE"] = "on"
        await seed_member(5440)
        await seed_bio_credential(
            5440, credential_id="BIOtest0040")
        resp = client.post(
            "/api/login53/auth/orchestrate",
            json={"channel": "passkey",
                  "credential": {
                      "credentialId":
                          "BIOtest0040"},
                  "hour": 12},
            headers=member)
        body = resp.json() or {}
        orch = body.get("orchestration") or {}
        record("HTTP orchestrate 200(签发)",
               resp.status_code == 200
               and orch.get("status")
               == "authenticated",
               str(resp.status_code))

        # 事件回读(会员过滤)
        resp = client.get("/api/login53/my/events",
                          headers=member)
        body = resp.json() or {}
        events = body.get("events") or []
        record("事件回读(本人过滤+含六字段)",
               body.get("total") == 1
               and all(k in events[0] for k in (
                   "method", "riskScore", "decision",
                   "durationMs", "privacyCost",
                   "explainRef")) if events else False,
               str(body.get("total")))

        # 鉴权
        resp = client.post(
            "/api/login53/auth/orchestrate",
            json={"channel": "passkey"})
        record("orchestrate 无 Member 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.get("/api/login53/my/events")
        record("my/events 无 Member 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 凭证不存在 → 404
        resp = client.post(
            "/api/login53/auth/orchestrate",
            json={"channel": "passkey",
                  "credential": {
                      "credentialId": "BIOgone"}},
            headers=member)
        record("HTTP 凭证不存在 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 零影响: 宪法断言
        from routes.entry_routes import (
            router as entry_router,
        )
        entry_count = sum(
            1 for r in entry_router.routes)
        record("39号 entry 路由零改动(24)",
               entry_count == 24, str(entry_count))
        from services.xiaozhu_fc_registry import (
            TOOL_REGISTRY,
        )
        record("49号17工具零改动",
               len(TOOL_REGISTRY) == 17)
        from services.xiaozhu_voice50_rules import (
            VOICE_RULES,
        )
        record("50号14行为零改动",
               len(VOICE_RULES) == 14)
        os.environ["LOGIN53_MODE"] = "off"


async def run_all():
    await TestFourTierResponse().run()
    await TestChannelVerification().run()
    await TestBudgetAndFallback().run()
    await TestEventsAndEndpoints().run()


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
