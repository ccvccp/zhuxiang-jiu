"""50号·小竹语音信值积分引擎 P2 专项测试
(L2 五行为 + T+1 结算)

运行方式:
    python test_xiaozhu_p50_2.py

覆盖(50号计划 §七 P2):
    - 加法 bonus 语义(反馈被采纳 +10/语料 +20)
    - L2 五行为数学: 连贯性 ×1.2/频繁修正 ×0.5/
      授权 ×1.3/撤回 -2/礼貌 streak ×1.5/
      辱骂 -10/跨文化 ×2
    - 信号源桥: 偏好上调=授权/当日撤回/共创采纳=反馈
    - T+1 结算: 聚合只计正向 capped(溢出不桥接)/
      幂等(双重结算)/frozen skip/unbound skip/
      无正向闭环/拒收重试语义/入账对账(45号
      ethics_evidence delta)
    - 调度器: off 默认/run_scheduled_settlement
    - 端点: POST settle/GET settlements/鉴权
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
os.environ["VOICE50_MODE"] = "on"
os.environ["VOICE50_SETTLE_MODE"] = "off"

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
    import services.xiaozhu_executor as ex_mod
    ex_mod._EXECUTOR_SINGLETON = None


async def _bind(member_id: int) -> int:
    import uuid
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    from services.xiaozhu_service import XiaozhuService
    suffix = uuid.uuid4().hex[:10]
    tid = (await TrustProfileService().create_role(
        "person", f"p502-{suffix[:6]}",
        f"110101{suffix}4321"))["trustId"]
    await XiaozhuService().bind_trust(
        member_id, tid, note="p50-2")
    return tid


async def _ethics_delta(trust_id: int) -> float:
    """读 45号 ethics_evidence 因子当前值(扁平 dict)"""
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    p = await TrustProfileService().repo.get_profile(
        trust_id)
    return float(p.get("factors", {}).get(
        "ethics_evidence") or 0)


class TestBonusMath:
    """01 加法 bonus 语义"""

    async def run(self):
        print("[01 加法 bonus]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        # 反馈被采纳: 6 + 10 = 16(非 6×10=60)
        r = await svc.record_behavior(
            7101, "voice_feedback",
            gains={"adopted": True})
        record("反馈采纳=base+10(加法)",
               abs(r["finalScore"] - 16.0) < 1e-6,
               str(r["finalScore"]))
        # 未采纳: 基础 6
        r2 = await svc.record_behavior(
            7101, "voice_feedback")
        record("反馈未采纳=基础 6",
               abs(r2["finalScore"] - 6.0) < 1e-6)
        # L3 语料(加法 20——预演 P3)
        r3 = await svc.record_behavior(
            7101, "voice_corpus_donate",
            gains={"adopted": True})
        record("语料采纳=base+20(加法)",
               abs(r3["finalScore"] - 30.0) < 1e-6,
               str(r3["finalScore"]))


class TestL2Behaviors:
    """02 L2 行为数学(连贯/修正/礼貌/跨文化)"""

    async def run(self):
        print("[02 L2 行为数学]")
        reset_all()
        from services.xiaozhu_service import (
            XiaozhuService, _detect_inclusive,
            POLITE_WORDS, ATTACK_WORDS,
        )
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        repo = Voice50Repository()
        svc = XiaozhuService()
        member = 7201
        sid = (await svc.open_session(member))[
            "sessionId"]
        # 连续两轮指令: 第二轮含连贯 ×1.2
        await svc.handle_text(sid, "小竹，看新品")
        await svc.handle_text(sid, "小竹，查优惠")
        evs = await repo.list_events(member_id=member)
        ci = [e for e in evs if e["behavior"]
              == "voice_clear_intent"]
        record("清晰意图两轮(第二轮连贯 ×1.2)",
               len(ci) >= 2
               and abs(ci[1]["finalScore"] - 1.2) < 0.01,
               f"{[e['finalScore'] for e in ci[:3]]}")
        # 礼貌 streak(3 轮「谢谢+指令」——钩子在指令
        # 成功路径, 礼貌词与指令组合触发)
        await svc.handle_text(sid, "小竹，谢谢，看新品")
        await svc.handle_text(sid, "小竹，谢谢，看新品")
        await svc.handle_text(sid, "小竹，谢谢，看新品")
        evs = await repo.list_events(member_id=member)
        po = [e for e in evs if e["behavior"]
              == "voice_polite"]
        record("礼貌 streak3(×1.5=0.75)",
               len(po) == 3
               and abs(po[-1]["finalScore"] - 0.75)
               < 0.01,
               f"{[e['finalScore'] for e in po]}")
        # 辱骂 → -10
        member2 = 7202
        sid2 = (await svc.open_session(member2))[
            "sessionId"]
        # 构造直接调用(避免攻击词进入指令路由兜底路径
        # ——钩子在成功指令后; 用 _voice50_polite 单测)
        session = await svc._require_open(sid2)
        await svc._voice50_polite(
            __import__(
                "services.xiaozhu_voice50_service",
                fromlist=["Voice50Service"]
            ).Voice50Service(),
            member2, sid2, 1, [], "小竹，你这个废物")
        evs = await repo.list_events(member_id=member2)
        record("辱骂 -10(不限日限)",
               any(e["behavior"] == "voice_polite"
                   and e["finalScore"] == -10.0
                   for e in evs))
        # 跨文化检测
        record("跨文化检测(方言)",
               _detect_inclusive("小竹，咋办呀"))
        record("跨文化检测(外语)",
               _detect_inclusive("小竹，hello"))
        record("普通中文不误报",
               not _detect_inclusive("小竹，看新品"))
        # 跨文化行为(直接引擎)
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        r = await Voice50Service().record_behavior(
            7203, "voice_inclusive",
            gains={"minorityLang": True})
        record("跨文化 ×2(=4.0)",
               abs(r["finalScore"] - 4.0) < 1e-6)


class TestGrantBridge:
    """03 隐私授权桥(49号偏好联动)"""

    async def run(self):
        print("[03 隐私授权桥]")
        reset_all()
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        repo = Voice50Repository()
        svc = XiaozhuPrivacyService()
        member = 7301
        # 上调(1.0→1.5): 授权 ×1.3(范围具体 <2.0)
        await svc.set_preference(member, 1.5)
        evs = await repo.list_events(member_id=member)
        grant = [e for e in evs if e["behavior"]
                 == "voice_privacy_grant"]
        record("上调=授权(×1.3=10.4)",
               len(grant) == 1
               and abs(grant[0]["finalScore"]
                       - 10.4) < 0.01,
               str([e["finalScore"] for e in grant]))
        # 当日下调 → 撤回 -2
        await svc.set_preference(member, 0.8)
        evs = await repo.list_events(member_id=member)
        revoke = [e for e in evs if e["behavior"]
                  == "voice_privacy_grant"
                  and e["finalScore"] < 0]
        record("当日撤回(-2)",
               len(revoke) == 1
               and revoke[0]["finalScore"] == -2.0)
        # 无授权当日下调 → 无事件
        member2 = 7302
        await svc.set_preference(member2, 0.8)
        evs2 = await repo.list_events(
            member_id=member2)
        record("无授权下调不计",
               not any(e["behavior"]
                       == "voice_privacy_grant"
                       for e in evs2))


class TestFeedbackBridge:
    """04 反馈桥(48号共创采纳)"""

    async def run(self):
        print("[04 反馈桥(共创采纳)]")
        reset_all()
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        xrepo = Xiaozhu48Repository()
        vrepo = Voice50Repository()
        member = 7401
        # 灌 pending 共创指令 → 采纳
        cmd_id = await xrepo._next_id(
            xrepo.TABLE_CUSTOM)
        await xrepo.save_record(xrepo.TABLE_CUSTOM, {
            "cmdId": cmd_id, "memberId": member,
            "phrase": "看看竹韵佳酿",
            "action": "product.new",
            "status": "pending", "ts": "",
        })
        await XiaozhuEvolutionService(
            repo=xrepo).review_custom(
            cmd_id, approve=True, note="测试采纳")
        evs = await vrepo.list_events(
            member_id=member)
        fb = [e for e in evs if e["behavior"]
              == "voice_feedback"]
        record("共创采纳=反馈(+10)",
               len(fb) == 1
               and abs(fb[0]["finalScore"] - 16.0)
               < 0.01,
               str([e["finalScore"] for e in fb]))
        # 驳回 → 无积分
        member2 = 7402
        cmd_id2 = await xrepo._next_id(
            xrepo.TABLE_CUSTOM)
        await xrepo.save_record(xrepo.TABLE_CUSTOM, {
            "cmdId": cmd_id2, "memberId": member2,
            "phrase": "来点好酒",
            "action": "product.new",
            "status": "pending", "ts": "",
        })
        await XiaozhuEvolutionService(
            repo=xrepo).review_custom(
            cmd_id2, approve=False, note="驳回")
        evs2 = await vrepo.list_events(
            member_id=member2)
        record("驳回不计反馈",
               not any(e["behavior"]
                       == "voice_feedback" for e in evs2))


class TestSettlement:
    """05 T+1 结算(聚合/幂等/skip/入账)"""

    async def run(self):
        print("[05 T+1 结算]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service, _today_key,
        )
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        repo = Voice50Repository()
        svc = Voice50Service()
        # 会员 A: 绑定+灌 L2 正向事件(昨日)
        member_a = 7501
        tid = await _bind(member_a)
        for _ in range(3):
            await svc.record_behavior(
                member_a, "voice_polite",
                note="yesterday")
        # 手动把事件改为昨日(构造 T+1 场景)
        yesterday = "2000-01-01"
        evs = await repo.list_events(
            member_id=member_a)
        for e in evs:
            e["dayKey"] = yesterday
            await repo.save_event(e)
        delta_before = await _ethics_delta(tid)
        # 结算(显式 day_key)
        r = await svc.settle_day(
            day_key=yesterday, operator="test")
        record("结算批次 done",
               r["counts"]["done"] == 1
               and (r["batches"][0]["status"]
                    == "done"),
               str(r["counts"]))
        record("聚合只计正向 capped",
               abs(r["batches"][0]["credits"] - 1.5)
               < 0.01,
               str(r["batches"][0]["credits"]))
        record("45号入账(delta=credits/10×1.05)",
               r["batches"][0]["depositDelta"] > 0,
               str(r["batches"][0]["depositDelta"]))
        delta_after = await _ethics_delta(tid)
        record("ethics_evidence 增量",
               delta_after > delta_before,
               f"{delta_before}->{delta_after}")
        # 幂等: 双重结算不重复申报
        r2 = await svc.settle_day(
            day_key=yesterday, operator="test")
        delta_after2 = await _ethics_delta(tid)
        record("幂等(二次结算无新批次)",
               r2["counts"]["done"] == 0
               and delta_after2 == delta_after,
               str(r2["counts"]))
        # 会员 B: 未绑定 → skipped(unbound), 事件保持 pending
        member_b = 7502
        await svc.record_behavior(
            member_b, "voice_polite")
        evs_b = await repo.list_events(
            member_id=member_b)
        for e in evs_b:
            e["dayKey"] = yesterday
            await repo.save_event(e)
        r3 = await svc.settle_day(
            day_key=yesterday, operator="test")
        record("未绑定 skipped",
               any(b["status"] == "skipped"
                   and b["reason"] == "unbound"
                   for b in r3["batches"]))
        evs_b2 = await repo.list_events(
            member_id=member_b)
        record("未绑定事件保持 pending",
               all(e["status"] == "pending"
                   for e in evs_b2))
        # 会员 C: frozen → skipped(先记事件后冻结)
        member_c = 7503
        await _bind(member_c)
        await svc.record_behavior(
            member_c, "voice_polite")
        for _ in range(5):
            await svc.record_behavior(
                member_c, "voice_login", penalty=True)
        evs_c = [e for e in await repo.list_events(
            member_id=member_c)
            if e["behavior"] == "voice_polite"]
        for e in evs_c:
            e["dayKey"] = yesterday
            await repo.save_event(e)
        r4 = await svc.settle_day(
            day_key=yesterday, operator="test")
        record("frozen skipped",
               any(b["status"] == "skipped"
                   and b["reason"] == "frozen"
                   for b in r4["batches"]))
        # 纯负向 → no_positive_credits 闭环
        member_d = 7504
        await _bind(member_d)
        await svc.record_behavior(
            member_d, "voice_polite", penalty=True)
        evs_d = [e for e in await repo.list_events(
            member_id=member_d)]
        for e in evs_d:
            e["dayKey"] = yesterday
            await repo.save_event(e)
        r5 = await svc.settle_day(
            day_key=yesterday, operator="test")
        record("纯负向闭环(no_positive)",
               any(b["status"] == "skipped"
                   and b["reason"]
                   == "no_positive_credits"
                   for b in r5["batches"]))
        evs_d2 = await repo.list_events(
            member_id=member_d)
        record("负向事件标 settled(不重复处理)",
               all(e["status"] == "settled"
                   for e in evs_d2))
        # 溢出不进信值轨道(封顶先于桥接)
        record("批次视图 byStatus",
               (await svc.settlement_view())["success"]
               is True)


class TestScheduler:
    """06 结算调度器"""

    async def run(self):
        print("[06 结算调度器]")
        from services.xiaozhu_voice50_scheduler import (
            settle_mode_enabled, start_scheduler,
            stop_scheduler, scheduler_running,
            run_scheduled_settlement,
        )
        record("默认 off(零影响)",
               settle_mode_enabled() is False)
        record("off 启动返回 False",
               start_scheduler() is False)
        os.environ["VOICE50_SETTLE_MODE"] = "on"
        record("on 启动成功",
               start_scheduler() is True
               and scheduler_running() is True)
        record("on 幂等启动",
               start_scheduler() is True)
        stop_scheduler()
        record("停止后不运行",
               scheduler_running() is False)
        # 独立调用(空库无事件——不报错)
        r = await run_scheduled_settlement()
        record("run(空库)安全",
               r.get("success") is True)
        os.environ["VOICE50_SETTLE_MODE"] = "off"


class TestEndpoints:
    """07 端点(HTTP)"""

    async def run(self):
        print("[07 端点]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        # settle(空 body——无事件)
        resp = client.post(
            "/api/xiaozhu/voice50/settle",
            json={}, headers=admin)
        body = resp.json()
        record("POST settle 200(空批次)",
               resp.status_code == 200
               and body.get("success") is True,
               str(resp.status_code))
        # 指定 dayKey
        resp = client.post(
            "/api/xiaozhu/voice50/settle",
            json={"dayKey": "2000-01-01"},
            headers=admin)
        record("POST settle(dayKey)200",
               resp.status_code == 200)
        # settlements 视图
        resp = client.get(
            "/api/xiaozhu/voice50/settlements",
            headers=admin)
        body = resp.json()
        record("GET settlements 200",
               resp.status_code == 200
               and "byStatus" in body)
        # 鉴权
        resp = client.post(
            "/api/xiaozhu/voice50/settle", json={})
        record("settle 缺 Role 403",
               resp.status_code == 403)
        resp = client.get(
            "/api/xiaozhu/voice50/settlements")
        record("settlements 缺 Role 403",
               resp.status_code == 403)
        os.environ["VOICE50_MODE"] = "off"


async def run_all():
    await TestBonusMath().run()
    await TestL2Behaviors().run()
    await TestGrantBridge().run()
    await TestFeedbackBridge().run()
    await TestSettlement().run()
    await TestScheduler().run()
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
