"""53号·小竹智能登录引擎 P5 专项测试
(效果评估+监控看板+红队收官)

运行方式:
    python test_login53_p5.py

覆盖(53号计划 §九 P5):
    - 六指标计算: events 聚合口径(成功率/耗时/
      留存/语音占比/投诉率 proxy/信任增益 proxy)
      +空态满分+快照留痕递增+达标判定
    - 监控看板: 六指标+通道占比+风险分布+
      角色四态分布+驻留统计+空态
    - 红队收官: 53号自身零侵入断言——伪造
      confirmToken 重放/跨会员凭证盗用/
      通道伪造/预算绕过尝试全部拒绝
    - off 铁律+观测面+端点+零影响(宪法断言)
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
                      nickname: str = "评估测试",
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


async def seed_bio(member_id: int,
                   credential_id: str):
    from repositories.entry_repository import (
        EntryRepository,
    )
    await EntryRepository().save_bio({
        "credentialId": credential_id,
        "memberId": member_id, "bioType": "face_id",
        "deviceId": "dev-eval",
        "publicKeyHash": "b" * 32,
        "name": "评估凭证", "status": "active",
        "mode": "mock", "enrolledAt": "2026-09-05",
    })


class TestMetrics:
    """01 六指标计算"""

    async def run(self):
        print("[01 六指标计算]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        # off 态拒绝
        try:
            await svc.compute_metrics()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态指标计算拒绝", ok, err)

        os.environ["LOGIN53_MODE"] = "on"

        # 空态: 全满分口径
        r0 = await svc.compute_metrics()
        snap0 = r0["snapshot"]
        m0 = snap0["metrics"]
        record("空态满分(成功率/留存=1.0)",
               m0["login_success_rate"]["value"] == 1.0
               and m0["retention_5min_rate"]
               ["value"] == 1.0,
               str(m0["login_success_rate"]))
        record("空态投诉率=0",
               m0["complaint_rate"]["value"] == 0.0,
               str(m0["complaint_rate"]["value"]))
        record("六指标齐备",
               set(m0) == {
                   "login_success_rate",
                   "avg_login_duration",
                   "retention_5min_rate",
                   "voice_login_share",
                   "complaint_rate",
                   "trust_gain_delta"},
               str(list(m0)))
        record("空态全达标(passedCount=6)",
               snap0["passedCount"] == 6,
               str(snap0["passedCount"]))
        record("信任增益 proxy 标注(0.5 中性)",
               m0["trust_gain_delta"]["value"] == 0.5,
               str(m0["trust_gain_delta"]["value"]))

        # 有数据场景: 2 成功(voice/passkey)+2 失败
        await seed_member(5700)
        await seed_bio(5700, "BIOeval001")
        await svc.orchestrate(
            5700, "passkey",
            credential={"credentialId": "BIOeval001"},
            hour=12)
        r_voice = await svc.voice_wake_login(
            5700, "小竹，我回来了", hour=12)
        for _ in range(2):
            try:
                await svc.orchestrate(
                    5700, "face",
                    credential={"liveness": 0.6})
            except ValueError:
                pass
        # 驻留领取(留存信号)
        await svc.retention_claim(5700,
                                  greeting="小竹你好")

        r1 = await svc.compute_metrics()
        snap1 = r1["snapshot"]
        m1 = snap1["metrics"]
        record("成功率口径(2/4=0.5)",
               m1["login_success_rate"]["value"]
               == 0.5,
               str(m1["login_success_rate"]["value"]))
        record("语音占比(voice 事件/总)",
               m1["voice_login_share"]["value"] > 0,
               str(m1["voice_login_share"]["value"]))
        record("耗时口径(秒——非毫秒)",
               0.0 <= m1["avg_login_duration"]
               ["value"] < 10.0,
               str(m1["avg_login_duration"]["value"]))
        record("留存口径(成功后有领取)",
               m1["retention_5min_rate"]["value"]
               > 0,
               str(m1["retention_5min_rate"]["value"]))
        record("事件计数留痕",
               snap1["eventCount"] >= 4
               and snap1["successCount"] >= 2,
               str((snap1["eventCount"],
                    snap1["successCount"])))

        # 快照留痕递增
        r2 = await svc.compute_metrics()
        s1 = snap1["snapId"]
        s2 = r2["snapshot"]["snapId"]
        record("快照留痕递增",
               isinstance(s1, int)
               and isinstance(s2, int) and s2 > s1,
               f"{s1} → {s2}")
        os.environ["LOGIN53_MODE"] = "off"


class TestDashboard:
    """02 监控看板"""

    async def run(self):
        print("[02 监控看板]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        # 空态看板(观测面——off 可访问)
        d0 = await svc.dashboard()
        record("看板空态",
               d0["latestSnapshot"] is None
               and d0["eventTotal"] == 0
               and d0["byChannel"] == {},
               str(d0["eventTotal"]))
        record("看板四态分布(全 0)",
               d0["byPortalState"] == {
                   "new": 0, "active": 0,
                   "dormant": 0, "high_risk": 0},
               str(d0["byPortalState"]))

        # 有数据看板
        os.environ["LOGIN53_MODE"] = "on"
        await seed_member(5710)
        await seed_bio(5710, "BIOeval010")
        await svc.orchestrate(
            5710, "passkey",
            credential={"credentialId": "BIOeval010"},
            hour=12)
        await svc.voice_wake_login(
            5710, "小竹，我回来了", hour=12)
        await svc.compute_metrics()

        d1 = await svc.dashboard()
        record("看板最新快照绑定",
               (d1["latestSnapshot"] or {})
               .get("snapId") is not None,
               str((d1["latestSnapshot"] or {})
                   .get("snapId")))
        record("看板指标段(metrics)",
               d1["metrics"] is not None
               and len(d1["metrics"]) == 6,
               str(len(d1.get("metrics") or {})))
        record("看板通道占比(passkey+voice)",
               d1["byChannel"].get("passkey", 0) >= 1
               and d1["byChannel"].get("voice", 0) >= 1,
               str(d1["byChannel"]))
        record("看板风险分布(silent)",
               d1["byDecision"].get("silent", 0) >= 1,
               str(d1["byDecision"]))
        record("看板四态分布(有档案)",
               sum(d1["byPortalState"].values()) >= 1,
               str(d1["byPortalState"]))
        record("看板驻留统计",
               (d1["retention"] or {})
               .get("milestones") == [3, 7, 30],
               str((d1["retention"] or {})
                   .get("milestones")))
        record("看板开关态回显",
               d1["mode"] == "on",
               str(d1["mode"]))
        os.environ["LOGIN53_MODE"] = "off"


class TestRedteam:
    """03 红队收官(53号零侵入断言)"""

    async def run(self):
        print("[03 红队收官]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()
        os.environ["LOGIN53_MODE"] = "on"

        await seed_member(5720)
        await seed_member(5721)
        await seed_bio(5720, "BIOredteam01")

        # RT-01: 伪造 confirmToken
        try:
            await svc.orchestrate(
                5720, "passkey",
                credential={
                    "credentialId": "BIOredteam01"},
                confirm_token="CTforged0000000")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "无效或已使用" in str(e), \
                str(e)[:30]
        record("RT-01 伪造确认令牌拒绝", ok, err)

        # RT-02: 跨会员凭证盗用
        try:
            await svc.orchestrate(
                5721, "passkey",
                credential={
                    "credentialId": "BIOredteam01"})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "归属不匹配" in str(e), \
                str(e)[:30]
        record("RT-02 跨会员凭证盗用拒绝", ok, err)

        # RT-03: 通道伪造(未注册通道)
        try:
            await svc.orchestrate(
                5720, "palm")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "通道非法" in str(e), ""
        record("RT-03 通道伪造拒绝", ok, err)

        # RT-04: 预算绕过(face 高成本+耗尽预算)
        from core.helpers import ts
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        await Xiaozhu48Repository(
        ).save_privacy_budget({
            "memberId": 5720,
            "dayKey": ts()[:10],
            "preference": 1.0, "usedToday": 1.0,
            "budget": 1.0, "ts": ts(),
        })
        try:
            await svc.orchestrate(
                5720, "face",
                credential={"liveness": 0.92})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "预算不足" in str(e), ""
        record("RT-04 预算绕过拒绝", ok, err)

        # RT-05: 安全挑战动作伪造(错误应答)
        r = await svc.orchestrate(
            5720, "face",
            credential={"liveness": 0.3})
        try:
            await svc.orchestrate(
                5720, "face",
                credential={"liveness": 0.3},
                challenge_response="伪造动作")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "动作不匹配" in str(e), ""
        record("RT-05 安全挑战伪造拒绝", ok, err)

        # RT-06: 令牌重放(合法令牌用后重放)
        reset_all()
        await seed_member(5722)
        await seed_bio(5722, "BIOredteam02")
        await svc.register_baseline_fingerprint(
            5722, "fp-redteam")
        # 构造 one_tap 场景(新设备+夜间+2 失败)
        for _ in range(2):
            await svc._bump_fail_count(5722, "voice")
        r2 = await svc.orchestrate(
            5722, "passkey",
            credential={
                "credentialId": "BIOredteam02"},
            fingerprint="different-fp", hour=2)
        if r2.get("status") == "one_tap_pending":
            token = r2["confirmToken"]
            await svc.orchestrate(
                5722, "passkey",
                credential={
                    "credentialId": "BIOredteam02"},
                fingerprint="different-fp",
                confirm_token=token)
            try:
                await svc.orchestrate(
                    5722, "passkey",
                    credential={
                        "credentialId":
                            "BIOredteam02"},
                    confirm_token=token)
                ok, err = False, "未拒绝"
            except ValueError:
                ok, err = True, ""
            record("RT-06 确认令牌重放拒绝", ok, err)
        else:
            record("RT-06 确认令牌重放拒绝",
                   False,
                   f"档位={r2.get('tier')}")
        os.environ["LOGIN53_MODE"] = "off"


class TestEndpoints:
    """04 端点+鉴权+零影响"""

    async def run(self):
        print("[04 端点+零影响]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 态 compute 409
        resp = client.post(
            "/api/login53/metrics/compute",
            headers=admin)
        record("HTTP compute off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 观测面端点(off 可访问)
        for path in (
                "/api/login53/metrics/latest",
                "/api/login53/metrics/snapshots",
                "/api/login53/events",
                "/api/login53/dashboard"):
            resp = client.get(path, headers=admin)
            record(f"观测面 {path.split('/')[-1]}"
                   f" off 可访问",
                   resp.status_code == 200,
                   str(resp.status_code))

        # on 态端到端
        os.environ["LOGIN53_MODE"] = "on"
        resp = client.post(
            "/api/login53/metrics/compute",
            headers=admin)
        record("HTTP compute 200(六指标)",
               resp.status_code == 200
               and len(((resp.json() or {})
                        .get("snapshot") or {})
                       .get("metrics") or {}) == 6,
               str(resp.status_code))

        resp = client.get("/api/login53/dashboard",
                          headers=admin)
        record("HTTP dashboard 200",
               resp.status_code == 200
               and (resp.json() or {}).get(
                   "byPortalState") is not None,
               str(resp.status_code))

        # 鉴权
        resp = client.post(
            "/api/login53/metrics/compute")
        record("compute 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.get("/api/login53/dashboard")
        record("dashboard 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 零影响: 宪法断言(全量)
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
        from services.xiaozhu_voice50_voiceprint import (
            voiceprint_mode,
        )
        record("50号声纹 proxy 默认态零改动",
               voiceprint_mode() == "proxy")
        os.environ["LOGIN53_MODE"] = "off"


async def run_all():
    await TestMetrics().run()
    await TestDashboard().run()
    await TestRedteam().run()
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
