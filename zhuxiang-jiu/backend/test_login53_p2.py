"""53号·小竹智能登录引擎 P2 专项测试
(语音融合登录)

运行方式:
    python test_login53_p2.py

覆盖(53号计划 §九 P2):
    - 唤醒即认证: 唤醒词判定(48号 detect_wake
      近似音容错)+声纹初验(50号 verify proxy
      标注)+语义口令双因子(口令集匹配)+
      会话建立(48号 open_session)+编排签发
    - 双因子引导: 声纹过但口令缺失/错误 →
      dual_factor_required+expectedPhrases+
      voice_confirm 话术
    - 反语音霸权: 未唤醒前缀 → 拒绝提示
    - 登录后导览: 时段问候+信值/积分/待办
      个性化摘要+快捷指令
    - memberId→trustId 绑定桥(45号信值经
      48号 bindings 聚合)
    - off 铁律+端点+零影响(宪法断言)
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
                      nickname: str = "语音测试",
                      created_days_ago: int = 90):
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


async def seed_trust_binding(member_id: int,
                             trust_id: int = 1,
                             score: float = 782.0):
    """种子: 48号绑定桥+45号信值档案(导览数据源)"""
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    await Xiaozhu48Repository().save_binding({
        "memberId": member_id, "trustId": trust_id,
        "boundAt": "2026-09-01", "note": "p2-test",
    })
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    await TrustValue45Repository().save_profile({
        "trustId": trust_id, "role": "person",
        "name": f"用户{member_id}",
        "idDigest": f"digest-p2-{trust_id:04d}",
        "score": score, "grade": "trusted",
        "status": "active",
        "createdAt": "2026-08-01",
    })


class TestWakeLogin:
    """01 唤醒即认证"""

    async def run(self):
        print("[01 唤醒即认证]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        # off 态拒绝
        try:
            await svc.voice_wake_login(5450, "小竹，我回来了")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态唤醒登录拒绝", ok, err)

        os.environ["LOGIN53_MODE"] = "on"

        # 空语音拒绝
        try:
            await svc.voice_wake_login(5450, "  ")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不能为空" in str(e), ""
        record("空语音拒绝", ok, err)

        # 未唤醒(反语音霸权红线)
        await seed_member(5450)
        try:
            await svc.voice_wake_login(5450, "我回来了")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "未唤醒" in str(e) \
                       and "小竹" in str(e), \
                str(e)[:40]
        record("未唤醒前缀拒绝(反语音霸权)", ok, err)

        # 口令缺失 → 双因子引导
        r1 = await svc.voice_wake_login(
            5450, "小竹，查信值")
        record("口令缺失→双因子引导",
               r1.get("status")
               == "dual_factor_required"
               and (r1.get("expectedPhrases")
                    or [None])[0] == "我回来了",
               str(r1.get("status")))
        record("双因子话术(voice_confirm)",
               (r1.get("script") or {}).get("key")
               == "voice_confirm",
               str((r1.get("script") or {}).get("key")))
        vp = r1.get("voiceprint") or {}
        record("声纹初筛标注(proxy 不作凭证)",
               vp.get("initialScreen") is True
               and "不作凭证" in str(vp.get("note")),
               str(vp.get("note"))[:40])

        # 口令错误 → 双因子引导(事件留痕)
        r2 = await svc.voice_wake_login(
            5450, "小竹，我出门了")
        record("口令错误→双因子引导",
               r2.get("status")
               == "dual_factor_required",
               str(r2.get("status")))
        events = await svc.repo.list_events(5450)
        pending_ev = [e for e in events
                      if e.get("decision")
                      == "dual_factor_pending"]
        record("双因子事件留痕",
               len(pending_ev) == 2,
               str(len(pending_ev)))

        # 口令正确 → 完成登录(声纹+口令双因子)
        await seed_trust_binding(5450)
        r3 = await svc.voice_wake_login(
            5450, "小竹，我回来了", hour=12)
        record("口令正确→登录签发",
               r3.get("status") == "authenticated",
               str(r3.get("status")))
        record("voice 通道+双因子标注",
               r3.get("channel") == "voice"
               and (r3.get("voiceprint") or {})
               .get("dualFactor") is True,
               str(r3.get("channel")))
        record("语音会话建立(48号)",
               (r3.get("voiceSession") or {})
               .get("channel") == "voice"
               and (r3.get("voiceSession") or {})
               .get("sessionId"),
               str((r3.get("voiceSession") or {})
                   .get("sessionId")))

        # 导览首播绑定
        briefing = r3.get("briefing") or {}
        record("登录即导览首播(价值前置)",
               briefing.get("text")
               and "语音测试" in briefing["text"],
               str(briefing.get("text"))[:40])

        # 唤醒近似音容错(小竹竹)
        reset_all()
        await seed_member(5451)
        r4 = await svc.voice_wake_login(
            5451, "小竹竹，我回来了", hour=12)
        record("唤醒近似音容错(小竹竹)",
               r4.get("status") == "authenticated",
               str(r4.get("status")))

        # 另一口令(我到家了)
        reset_all()
        await seed_member(5452)
        r5 = await svc.voice_wake_login(
            5452, "小竹，我到家了", hour=12)
        record("口令集第二短语",
               r5.get("status") == "authenticated",
               str(r5.get("status")))
        os.environ["LOGIN53_MODE"] = "off"


class TestBriefing:
    """02 登录后语音导览"""

    async def run(self):
        print("[02 语音导览]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        try:
            await svc.voice_briefing(5460)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态导览拒绝", ok, err)

        os.environ["LOGIN53_MODE"] = "on"
        await seed_member(5460, nickname="竹香导览")
        await seed_trust_binding(5460, trust_id=2,
                                 score=900.0)

        # 语音积分(48号台账)
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        xrepo = Xiaozhu48Repository()
        await xrepo.add_points({
            "ledgerId": 1, "memberId": 5460,
            "kind": "voice_daily",
            "points": 320.0, "balance": 320.0,
            "balanceAfter": 320.0,
            "ts": "2026-09-05",
        })

        b = await svc.voice_briefing(5460)
        record("导览含昵称+信值分",
               "竹香导览" in b["text"]
               and "900" in b["text"],
               b["text"][:50])
        record("导览含语音积分",
               "320" in b["text"]
               and (b.get("summary") or {})
               .get("voicePoints") == 320.0,
               str((b.get("summary") or {})
                   .get("voicePoints")))
        record("导览含快捷指令(三条)",
               set(b.get("quickCommands") or {}) == {
                   "查详情", "去修复", "随便逛逛"},
               str(list((b.get("quickCommands")
                         or {}).keys())))
        record("导览时段问候",
               b.get("greeting") in {
                   "早上好", "中午好",
                   "下午好", "晚上好"},
               str(b.get("greeting")))

        # fail-soft: 无绑定/无档案(信值降级 —)
        await seed_member(5461, nickname="降级测试")
        b2 = await svc.voice_briefing(5461)
        record("无绑定 fail-soft(信值降级)",
               "—" in b2["text"],
               b2["text"][:50])

        # 会员不存在(昵称降级——fail-soft)
        b3 = await svc.voice_briefing(99999)
        record("无会员 fail-soft(缺省昵称)",
               "用户" in b3["text"],
               b3["text"][:40])
        os.environ["LOGIN53_MODE"] = "off"


class TestEndpoints:
    """03 端点+鉴权+零影响"""

    async def run(self):
        print("[03 端点+鉴权+零影响]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        member = {"X-Member-Id": "5470"}

        # off 态 409
        resp = client.post(
            "/api/login53/voice/wake-login",
            json={"utterance": "小竹，我回来了"},
            headers=member)
        record("HTTP wake-login off 409",
               resp.status_code == 409,
               str(resp.status_code))
        resp = client.get(
            "/api/login53/voice/briefing",
            headers=member)
        record("HTTP briefing off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # on 态端到端
        os.environ["LOGIN53_MODE"] = "on"
        await seed_member(5470, nickname="端到端")
        await seed_trust_binding(5470, trust_id=3)

        # 双因子引导(HTTP)
        resp = client.post(
            "/api/login53/voice/wake-login",
            json={"utterance": "小竹，帮我查分"},
            headers=member)
        body = resp.json() or {}
        record("HTTP 双因子引导 200",
               resp.status_code == 200
               and (body.get("wakeLogin") or {})
               .get("status")
               == "dual_factor_required",
               str(resp.status_code))

        # 完整登录(HTTP)
        resp = client.post(
            "/api/login53/voice/wake-login",
            json={"utterance": "小竹，我回来了",
                  "hour": 12},
            headers=member)
        body = resp.json() or {}
        wl = body.get("wakeLogin") or {}
        record("HTTP wake-login 200(签发+导览)",
               resp.status_code == 200
               and wl.get("status")
               == "authenticated"
               and wl.get("briefing"),
               str(resp.status_code))

        # 导览端点
        resp = client.get(
            "/api/login53/voice/briefing",
            headers=member)
        body = resp.json() or {}
        record("HTTP briefing 200(快捷指令)",
               resp.status_code == 200
               and set(((body.get("briefing")
                         or {}).get(
                   "quickCommands") or {})) == {
                   "查详情", "去修复", "随便逛逛"},
               str(resp.status_code))

        # 未唤醒 409
        resp = client.post(
            "/api/login53/voice/wake-login",
            json={"utterance": "我回来了"},
            headers=member)
        record("HTTP 未唤醒 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 鉴权
        resp = client.post(
            "/api/login53/voice/wake-login",
            json={"utterance": "小竹，我回来了"})
        record("wake-login 无 Member 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.get("/api/login53/voice/briefing")
        record("briefing 无 Member 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 零影响: 宪法断言
        from services.xiaozhu_service import (
            WAKE_WORDS,
        )
        record("48号唤醒词表零改动",
               "小竹" in WAKE_WORDS)
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
        os.environ["LOGIN53_MODE"] = "off"


async def run_all():
    await TestWakeLogin().run()
    await TestBriefing().run()
    await TestEndpoints().run()


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
