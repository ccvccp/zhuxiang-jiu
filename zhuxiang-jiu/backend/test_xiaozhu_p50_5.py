"""50号·小竹语音信值积分引擎 P5 专项测试
(群体三场景+衰减对冲+看板分区)

运行方式:
    python test_xiaozhu_p50_5.py

覆盖(50号计划 §七 P5):
    - 群体系数: minor L2×1.5/L3×0.8/elder 同/
      disabled ×1.2/org_proxy L2L3×0.5/
      none 标准/非法群体拒绝/画像持久化
    - 激励池衰减: 90 天无交互月 5%/保底 30%/
      活跃不衰减/空池跳过/衰减史留痕
    - 修复对冲: ≤50%/超限拒绝/池划扣/
      45号 submit_repair 通道返回 repairId
    - 看板第 8 区块: off 空态/on 全量指标/
      fail-soft
    - 端点: group-profile/decay/offset+鉴权
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
        "person", f"p505-{suffix[:6]}",
        f"110101{suffix}4321"))["trustId"]
    await XiaozhuService().bind_trust(
        member_id, tid, note="p50-5")
    return tid


class TestGroupProfile:
    """01 群体三场景"""

    async def run(self):
        print("[01 群体三场景]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        # minor: L2 ×1.5(礼貌 0.5→0.75)
        await svc.set_group_profile(
            10101, "minor", guardian_id=999)
        r = await svc.record_behavior(
            10101, "voice_polite")
        record("minor L2 ×1.5(0.75)",
               abs(r["finalScore"] - 0.75) < 1e-6,
               str(r["finalScore"]))
        # minor: L3 ×0.8(问答 8→6.4)
        r2 = await svc.record_behavior(
            10101, "voice_community_qa",
            note="社区问答内容正常")
        record("minor L3 ×0.8(6.4)",
               abs(r2["finalScore"] - 6.4) < 1e-6,
               str(r2["finalScore"]))
        # elder 等价 minor 系数
        await svc.set_group_profile(10102, "elder")
        r3 = await svc.record_behavior(
            10102, "voice_polite")
        record("elder L2 ×1.5", r3["finalScore"] == 0.75)
        # disabled: ×1.2
        await svc.set_group_profile(
            10103, "disabled", verified=True)
        r4 = await svc.record_behavior(
            10103, "voice_polite")
        record("disabled ×1.2(0.6)",
               abs(r4["finalScore"] - 0.6) < 1e-6)
        # org_proxy: L2 ×0.5
        await svc.set_group_profile(10104, "org_proxy")
        r5 = await svc.record_behavior(
            10104, "voice_polite")
        record("org_proxy L2 ×0.5(0.25)",
               abs(r5["finalScore"] - 0.25) < 1e-6)
        # none 标准
        r6 = await svc.record_behavior(
            10105, "voice_polite")
        record("none 标准(0.5)",
               abs(r6["finalScore"] - 0.5) < 1e-6)
        # 非法群体
        try:
            await svc.set_group_profile(10106, "vip")
            record("非法群体拒绝", False, "未抛")
        except ValueError:
            record("非法群体拒绝", True)
        # 画像持久化(重读)
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        profile = await Voice50Repository(
        ).get_group_profile(10103)
        record("画像持久化(verified)",
               profile.get("group") == "disabled"
               and profile.get("verified") is True)


class TestDecay:
    """02 激励池月度衰减"""

    async def run(self):
        print("[02 激励池衰减]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        from datetime import UTC, datetime, timedelta
        svc = Voice50Service()
        repo = Voice50Repository()
        # 灌池(100 分)+90 天前 lastActive
        for _ in range(4):
            await svc.record_behavior(
                10201, "voice_corpus_donate")
        ledger = await repo.get_ledger(10201)
        old = (datetime.now(UTC)
               - timedelta(days=95)).isoformat()
        ledger["lastActiveAt"] = old
        ledger["poolBalance"] = 100.0
        ledger["earnedTotal"] = 100.0
        await repo.save_ledger(ledger)
        # 活跃会员(今天)
        for _ in range(2):
            await svc.record_behavior(
                10202, "voice_polite")
        r = await svc.run_decay()
        record("衰减执行(1 人)",
               r["decayed"] == 1
               and r["skipped"] >= 1,
               str(r)[:60])
        ledger2 = await repo.get_ledger(10201)
        record("池 100→95(月 5%)",
               abs(ledger2["poolBalance"] - 95.0)
               < 0.01,
               str(ledger2["poolBalance"]))
        record("衰减史留痕",
               len(ledger2.get("decayHistory") or [])
               == 1)
        # 保底 30%(池 28 < earned 30 → 不再衰减)
        ledger2["poolBalance"] = 28.0
        await repo.save_ledger(ledger2)
        r2 = await svc.run_decay()
        ledger3 = await repo.get_ledger(10201)
        record("保底 30%(28 不再衰减)",
               ledger3["poolBalance"] == 28.0
               and r2["decayed"] == 0,
               str(ledger3["poolBalance"]))
        # 活跃会员池不变
        active = await repo.get_ledger(10202)
        record("活跃会员不衰减",
               (active.get("decayHistory") or []) == [])


class TestOffset:
    """03 修复对冲"""

    async def run(self):
        print("[03 修复对冲]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        svc = Voice50Service()
        member = 10301
        tid = await _bind(member)
        # 灌违规事件(45号)
        await TrustProfileService().record_event(
            tid, "L2", "ethics_evidence", -30.0,
            source="admin", summary="违规测试")
        events = await TrustValue45Repository(
        ).list_events_by_trust(tid)
        vid = [e for e in events
               if (e.get("delta") or 0) < 0
               ][0]["eventId"]
        # 灌池(50 分)
        for _ in range(2):
            await svc.record_behavior(
                member, "voice_corpus_donate")
        ledger = await repo_get(member)
        ledger["poolBalance"] = 50.0
        ledger["earnedTotal"] = 50.0
        await repo_save(ledger)
        # 未绑定拒绝
        try:
            await svc.offset_violation(
                10399, vid)
            record("未绑定拒绝", False, "未抛")
        except ValueError:
            record("未绑定拒绝", True)
        # 对冲(默认 50%×50=25)
        r = await svc.offset_violation(member, vid)
        record("对冲划扣(25)+repairId",
               r["success"] is True
               and abs(r["offset"] - 25.0) < 0.01
               and r.get("repairId") is not None,
               str(r)[:80])
        record("池 50→25",
               abs(r["poolBalance"] - 25.0) < 0.01)
        # 超限拒绝
        try:
            await svc.offset_violation(
                member, vid, amount=20.0)
            record("超 50% 拒绝", False, "未抛")
        except ValueError as e:
            record("超 50% 拒绝",
                   "超上限" in str(e))
        # 池不足
        ledger = await repo_get(member)
        ledger["poolBalance"] = 0.0
        await repo_save(ledger)
        try:
            await svc.offset_violation(member, vid)
            record("池不足拒绝", False, "未抛")
        except ValueError as e:
            record("池不足拒绝", "不足" in str(e))


async def repo_get(member_id: int) -> dict:
    from repositories.voice50_repository import (
        Voice50Repository,
    )
    return await Voice50Repository().get_ledger(
        member_id)


async def repo_save(ledger: dict) -> None:
    from repositories.voice50_repository import (
        Voice50Repository,
    )
    await Voice50Repository().save_ledger(ledger)


class TestDashboardZone:
    """04 看板第 8 区块"""

    async def run(self):
        print("[04 看板 voice50 分区]")
        reset_all()
        from services.xiaozhu_dashboard_service import (
            XiaozhuDashboardService,
        )
        svc = XiaozhuDashboardService()
        # off 空态
        os.environ["VOICE50_MODE"] = "off"
        board = await svc.build()
        v50 = (board.get("zones") or {}).get("voice50")
        record("off 空态(enabled=False)",
               v50 == {"enabled": False,
                       "note": "语音积分引擎未启用"
                               "(VOICE50_MODE=off)——"
                               "零影响态"},
               str(v50)[:60])
        # on 全量
        os.environ["VOICE50_MODE"] = "on"
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        await Voice50Service().record_behavior(
            10401, "voice_polite")
        board = await svc.build()
        v50 = (board.get("zones") or {}).get("voice50")
        record("on 分区齐备",
               v50.get("enabled") is True
               and v50.get("events") == 1
               and "poolTotal" in v50
               and "settlements" in v50
               and "adjudications" in v50,
               str(v50)[:80])
        record("分区含红线文案",
               "永不阻断" in v50.get("note", ""))
        # fail-soft: voice50 数据源故障不阻断看板
        real_build = svc.build

        async def _wrap_build():
            board = await real_build()
            board["zones"]["voice50"] = {
                "error": "模拟故障"}
            board["zoneErrors"] = ["voice50"]
            return board

        # 直接验证 zone 函数异常路径
        async def _boom():
            raise RuntimeError("voice50 数据源故障")

        svc._zone_voice50 = _boom
        board2 = await svc.build()
        record("voice50 分区 fail-soft",
               "voice50" in (board2.get("zoneErrors")
                             or [])
               and len(board2.get("zones") or {}) == 8,
               str(board2.get("zoneErrors")))


class TestEndpoints:
    """05 端点(HTTP)"""

    async def run(self):
        print("[05 端点]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        h = {"X-Member-Id": "10501"}
        # group-profile
        resp = client.put(
            "/api/xiaozhu/voice50/group-profile",
            json={"memberId": 10501,
                  "group": "disabled",
                  "verified": True}, headers=admin)
        body = resp.json()
        record("PUT group-profile 200",
               resp.status_code == 200
               and body.get("group") == "disabled")
        resp = client.put(
            "/api/xiaozhu/voice50/group-profile",
            json={"memberId": 10501, "group": "vip"},
            headers=admin)
        record("非法群体 409", resp.status_code == 409)
        resp = client.put(
            "/api/xiaozhu/voice50/group-profile",
            json={"memberId": 10501,
                  "group": "minor"})
        record("group-profile 缺 Role 403",
               resp.status_code == 403)
        # decay
        resp = client.post(
            "/api/xiaozhu/voice50/decay", headers=admin)
        record("POST decay 200",
               resp.status_code == 200
               and resp.json().get("success") is True)
        resp = client.post(
            "/api/xiaozhu/voice50/decay")
        record("decay 缺 Role 403",
               resp.status_code == 403)
        # offset(未绑定 → 409)
        resp = client.post(
            "/api/xiaozhu/voice50/offset",
            json={"violationEventId": 1}, headers=h)
        record("offset 未绑定 409",
               resp.status_code == 409)
        resp = client.post(
            "/api/xiaozhu/voice50/offset",
            json={}, headers=h)
        record("offset 缺参 409",
               resp.status_code == 409)
        # 看板分区
        resp = client.get(
            "/api/xiaozhu/dashboard", headers=admin)
        zones = (resp.json() or {}).get("zones") or {}
        record("看板八区块(含 voice50)",
               resp.status_code == 200
               and "voice50" in zones,
               str(resp.status_code))
        os.environ["VOICE50_MODE"] = "off"


async def run_all():
    await TestGroupProfile().run()
    await TestDecay().run()
    await TestOffset().run()
    await TestDashboardZone().run()
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
