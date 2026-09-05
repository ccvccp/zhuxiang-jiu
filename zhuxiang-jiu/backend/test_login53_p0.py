"""53号·小竹智能登录引擎 P0 专项测试
(注册表+态势感知层)

运行方式:
    python test_login53_p0.py

覆盖(53号计划 §九 P0):
    - 话术注册表: 三组 17 场景+TTS 四态参数+
      占位符渲染+黑名单扫描+异常组出路铁律
    - 多模态矩阵: 五通道+底座锚点+话术键绑定+
      风险阈值单调
    - 六指标注册表: 基线/方向+达标判定
    - 角色四态: new/active/dormant/high_risk
      判定优先级+状态迁移留痕
    - 态势感知: 基线匹配三档(静默/一键/常规)+
      意图预判+预算预检(49号只读探针)
    - 价值钩子: 话术渲染+fail-soft 聚合
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
                      nickname: str = "测试用户",
                      created_days_ago: int = 0):
    """种子: 会员档案(账龄控制——save 指定 memberId
    绕过 create 自增覆盖)"""
    from datetime import datetime, timedelta
    from repositories.member_repository import (
        MemberRepository,
    )
    repo = MemberRepository()
    created = (datetime.now()
               - timedelta(days=created_days_ago)
               ).isoformat()
    await repo.save(member_id, {
        "id": member_id, "phone":
            f"139{member_id:08d}",
        "nickname": nickname, "role": "member",
        "created_at": created, "points": 100,
        "status": 1,
    })
    return member_id


class TestScripts:
    """01 话术注册表"""

    async def run(self):
        print("[01 话术注册表]")
        reset_all()
        from services.login53_scripts import (
            ALL_SCRIPTS, POSITIVE_SCRIPTS,
            ERROR_SCRIPTS, EXIT_SCRIPTS,
            TTS_PROFILES, render_script,
        )
        record("三组 17 场景(7+7+3)",
               len(POSITIVE_SCRIPTS) == 7
               and len(ERROR_SCRIPTS) == 7
               and len(EXIT_SCRIPTS) == 3
               and len(ALL_SCRIPTS) == 17,
               str((len(POSITIVE_SCRIPTS),
                    len(ERROR_SCRIPTS),
                    len(EXIT_SCRIPTS))))
        record("TTS 四态参数组",
               set(TTS_PROFILES) == {
                   "success", "security_warn",
                   "elderly_mode", "error_recovery"},
               str(list(TTS_PROFILES)))

        # 占位符渲染
        r = render_script("wake_login", {
            "nickname": "竹友", "score": "782"})
        record("占位符渲染(昵称+信值分)",
               "竹友" in r["text"],
               r["text"][:40])
        r2 = render_script("passkey_silent", {})
        record("缺省降级渲染(不报错)",
               "信值" in r2["text"],
               r2["text"][:40])

        # 黑名单扫描(去污名化铁律)
        from services.login53_scripts import (
            FORBIDDEN_PHRASES,
        )
        clean = all(
            p not in s["text"]
            for s in ALL_SCRIPTS.values()
            for p in FORBIDDEN_PHRASES)
        record("话术黑名单零命中",
               clean, "存在责备性用语")

        # 异常组出路铁律
        record("异常组全含 fallbackAction",
               all(s.get("fallbackAction")
                   for s in ERROR_SCRIPTS.values()))
        record("异常组禁用 success 参数组",
               all(s["ttsProfile"] != "success"
                   for s in ERROR_SCRIPTS.values()))

        # 去污名化关键话术抽查
        acc = render_script("account_protected", {})
        record("账号保护话术去污名化",
               "这不是您的错" in acc["text"],
               acc["text"][:40])

        # 未注册话术拒绝
        try:
            render_script("not_exist")
            ok, err = False, "未拒绝"
        except KeyError as e:
            ok, err = "not_exist" in str(e), ""
        record("未注册话术 KeyError", ok, err)


class TestRegistry:
    """02 多模态矩阵+六指标注册表"""

    async def run(self):
        print("[02 注册表]")
        from services.login53_registry import (
            AUTH_CHANNELS, PORTAL_STATES,
            METRICS_REGISTRY, RISK_TIERS,
            evaluate_metric, current_mode,
        )
        record("五通道矩阵",
               set(AUTH_CHANNELS) == {
                   "passkey", "face", "voice",
                   "qr", "fingerprint"},
               str(list(AUTH_CHANNELS)))
        record("通道底座锚点齐备",
               all("base" in c and "scriptKey" in c
                   for c in AUTH_CHANNELS.values()))
        record("通道话术键全绑定",
               all(c["scriptKey"]
                   in __import__(
                       "services.login53_scripts",
                       fromlist=["ALL_SCRIPTS"]
                   ).ALL_SCRIPTS
                   for c in AUTH_CHANNELS.values()))

        # 风险阈值单调
        tiers = [v["maxRisk"] for v in
                 RISK_TIERS.values()]
        record("风险四档阈值单调"
               "(25<50<70<100)",
               tiers == sorted(tiers)
               and len(set(tiers)) == 4,
               str(tiers))

        # 角色四态
        record("角色四态注册表",
               set(PORTAL_STATES) == {
                   "new", "active", "dormant",
                   "high_risk"},
               str(list(PORTAL_STATES)))

        # 六指标
        record("六指标注册表",
               len(METRICS_REGISTRY) == 6
               and set(METRICS_REGISTRY) == {
                   "login_success_rate",
                   "avg_login_duration",
                   "retention_5min_rate",
                   "voice_login_share",
                   "complaint_rate",
                   "trust_gain_delta"},
               str(list(METRICS_REGISTRY)))

        # 达标判定
        record("指标判定(higher/lower 双向)",
               evaluate_metric(
                   "login_success_rate", 0.995) == "pass"
               and evaluate_metric(
                   "login_success_rate", 0.98) == "fail"
               and evaluate_metric(
                   "complaint_rate", 0.0005) == "pass"
               and evaluate_metric(
                   "complaint_rate", 0.002) == "fail")

        # 默认 off
        record("开关默认 off",
               current_mode() == "off")

        # 未注册指标
        try:
            evaluate_metric("nope", 1.0)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("未注册指标 KeyError", ok, err)


class TestPortalStates:
    """03 角色四态判定"""

    async def run(self):
        print("[03 角色四态判定]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        # 新用户(账龄 2 天+无登录史)
        await seed_member(5401, created_days_ago=2)
        r = await svc.resolve_portal_state(5401)
        record("新用户判定(账龄<7天)",
               r["portalState"] == "new",
               str(r["portalState"]))

        # 活跃用户(账龄 30 天+昨日登录)
        reset_all()
        await seed_member(5402, created_days_ago=30)
        from core.helpers import ts
        from datetime import datetime, timedelta
        yesterday = (datetime.now()
                     - timedelta(days=1)).isoformat()
        profile = {"memberId": 5402,
                  "lastLoginAt": yesterday}
        await svc.repo.save_profile(profile)
        r2 = await svc.resolve_portal_state(5402)
        record("活跃用户判定(7日内登录)",
               r2["portalState"] == "active",
               str(r2["portalState"]))

        # 沉睡用户(>30 天未登录)
        reset_all()
        await seed_member(5403, created_days_ago=365)
        old_day = (datetime.now()
                   - timedelta(days=45)).isoformat()
        await svc.repo.save_profile({
            "memberId": 5403,
            "lastLoginAt": old_day})
        r3 = await svc.resolve_portal_state(5403)
        record("沉睡用户判定(>30天)",
               r3["portalState"] == "dormant",
               str(r3["portalState"]))
        record("沉睡态含错过钩子",
               "错过" in r3["hook"]
               and "恢复" in r3["portal"],
               f"hook={r3['hook'][:20]}")

        # 高危用户(风控标记——优先级最高)
        reset_all()
        await seed_member(5404, created_days_ago=2)
        yesterday2 = (datetime.now()
                      - timedelta(days=1)).isoformat()
        await svc.repo.save_profile({
            "memberId": 5404,
            "lastLoginAt": yesterday2,
            "riskFlagged": 1})
        r4 = await svc.resolve_portal_state(5404)
        record("高危用户判定(风控优先)",
               r4["portalState"] == "high_risk",
               str(r4["portalState"]))
        record("高危态去污名化标注",
               "透明保护" in r4["stateLabel"],
               r4["stateLabel"])

        # 状态迁移留痕
        got = await svc.repo.get_profile(5404)
        record("状态迁移留痕(stateChangedAt)",
               bool(got.get("stateChangedAt")),
               str(got.get("stateChangedAt"))[:20])


class TestPreloginSense:
    """04 态势感知"""

    async def run(self):
        print("[04 态势感知]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        # off 态拒绝
        try:
            await svc.prelogin_sense(5401)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态感知拒绝", ok, err)

        os.environ["LOGIN53_MODE"] = "on"
        await seed_member(5405, created_days_ago=30)

        # 基线未登记(首次设备)→常规
        r0 = await svc.prelogin_sense(
            5405, fingerprint="abc123def456")
        record("无基线→常规档",
               r0["authLevel"] == "regular"
               and r0["baselineMatch"] == 0.0,
               str((r0["authLevel"],
                    r0["baselineMatch"])))

        # 登记基线→完全一致→静默
        await svc.register_baseline_fingerprint(
            5405, "abc123def456")
        r1 = await svc.prelogin_sense(
            5405, fingerprint="abc123def456")
        record("基线完全一致→静默档",
               r1["authLevel"] == "silent"
               and r1["baselineMatch"] == 1.0,
               str((r1["authLevel"],
                    r1["baselineMatch"])))

        # 部分一致→一键档(mock 相似度)
        r2 = await svc.prelogin_sense(
            5405, fingerprint="abc123XXX456")
        if 0.7 <= r2["baselineMatch"] < 0.95:
            expect = "one_tap"
        else:
            expect = "regular"
        record("部分一致→档位正确",
               r2["authLevel"] == expect,
               str((r2["baselineMatch"],
                    r2["authLevel"])))

        # 意图预判(来源标签优先)
        r3 = await svc.prelogin_sense(
            5405, visit_source="shopping")
        record("意图预判(来源标签优先)",
               r3["intent"] == "shopping"
               and r3["intentPage"] == "商品列表页",
               str(r3["intent"]))

        # 预算预检(无账户=满额)
        record("预算预检(无账户=1.0 满额)",
               r3["budget"].get("remaining") == 1.0
               and r3["budget"].get("firstUse")
               is True,
               str(r3["budget"]))

        # 预算预检(已耗尽→零成本通道推荐)
        from core.helpers import ts
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        await Xiaozhu48Repository( \
        ).save_privacy_budget({
            "memberId": 5405,
            "dayKey": ts()[:10],
            "preference": 1.0, "usedToday": 1.0,
            "budget": 1.0, "ts": ts(),
        })
        r4 = await svc.prelogin_sense(5405)
        record("预算耗尽→零成本通道推荐",
               r4["budget"].get("remaining") == 0.0
               and r4["recommendedChannels"]
               == ["passkey", "qr"],
               str(r4["recommendedChannels"]))
        os.environ["LOGIN53_MODE"] = "off"


class TestHook:
    """05 价值钩子"""

    async def run(self):
        print("[05 价值钩子]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        try:
            await svc.generate_hook(5401)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态钩子拒绝", ok, err)

        os.environ["LOGIN53_MODE"] = "on"
        await seed_member(5406, nickname="竹香老友")
        r = await svc.generate_hook(5406)
        record("钩子默认话术(唤醒场景)",
               r["script"]["key"] == "wake_login"
               and "竹香老友" in r["script"]["text"],
               r["script"]["text"][:40])
        record("钩子含 TTS 参数",
               "tts" in r["script"]
               and "pitch" in r["script"]["tts"],
               str(r["script"].get("tts"))[:40])
        record("钩子 valueHook 标注",
               r["script"].get("valueHook") is True)

        # 指定话术键渲染
        r2 = await svc.generate_hook(
            5406, script_key="passkey_silent",
            params={"score": "900",
                    "delta": "上升15"})
        record("指定话术键渲染",
               r2["script"]["key"]
               == "passkey_silent"
               and "900" in r2["script"]["text"],
               r2["script"]["text"][:40])

        # fail-soft(会员不存在→昵称降级)
        r3 = await svc.generate_hook(99999)
        record("聚合 fail-soft(缺省昵称)",
               r3["hookData"].get("nickname")
               == "用户",
               str(r3["hookData"].get("nickname")))
        os.environ["LOGIN53_MODE"] = "off"


class TestEndpoints:
    """06 端点+鉴权+零影响"""

    async def run(self):
        print("[06 端点+鉴权+零影响]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        member = {"X-Member-Id": "5401"}

        # registry(观测面——off 可访问)
        resp = client.get("/api/login53/registry",
                           headers=admin)
        body = resp.json() or {}
        record("HTTP registry 200(off 可访问)",
               resp.status_code == 200
               and len(body.get("channels")
                       or {}) == 5,
               str(resp.status_code))

        # off 态编排面 409
        resp = client.post("/api/login53/prelogin/sense",
                           json={}, headers=member)
        record("off 态 sense 409",
               resp.status_code == 409,
               str(resp.status_code))

        # on 态端点
        os.environ["LOGIN53_MODE"] = "on"
        await seed_member(5401, created_days_ago=2)
        resp = client.post(
            "/api/login53/prelogin/sense",
            json={"fingerprint": "fp-test-01"},
            headers=member)
        record("HTTP sense 200(意图+预算)",
               resp.status_code == 200
               and "intent" in (
                   (resp.json() or {})
                   .get("sense") or {}),
               str(resp.status_code))

        # portal 四态
        resp = client.get("/api/login53/portal",
                          headers=member)
        record("HTTP portal 200(new 态)",
               resp.status_code == 200
               and (((resp.json() or {})
                     .get("portal") or {})
                    .get("portalState")) == "new",
               str(resp.status_code))

        # 钩子
        resp = client.post(
            "/api/login53/hook/generate",
            json={}, headers=member)
        record("HTTP hook 200",
               resp.status_code == 200
               and "script" in (
                   (resp.json() or {})
                   .get("hook") or {}),
               str(resp.status_code))

        # 基线登记
        resp = client.post(
            "/api/login53/baseline",
            json={"fingerprint": "fp-test-01"},
            headers=member)
        record("HTTP baseline 200",
               resp.status_code == 200,
               str(resp.status_code))

        # my/profile
        resp = client.get("/api/login53/my/profile",
                          headers=member)
        record("HTTP my/profile 200",
               resp.status_code == 200,
               str(resp.status_code))

        # 鉴权
        resp = client.get("/api/login53/registry")
        record("registry 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(
            "/api/login53/prelogin/sense", json={})
        record("sense 无 Member 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 零影响: 宪法断言
        from routes.entry_routes import (
            router as entry_router,
        )
        entry_count = sum(
            1 for r in entry_router.routes)
        record("39号 entry 路由零改动(24 端点)",
               entry_count == 24, str(entry_count))
        from services.xiaozhu_fc_registry import (
            TOOL_REGISTRY,
        )
        record("49号17工具零改动",
               len(TOOL_REGISTRY) == 17)
        os.environ["LOGIN53_MODE"] = "off"


async def run_all():
    await TestScripts().run()
    await TestRegistry().run()
    await TestPortalStates().run()
    await TestPreloginSense().run()
    await TestHook().run()
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
