"""48号·小竹智能语音中枢 P4 专项测试
(语音中枢看板与收官)

运行方式:
    python test_xiaozhu_p4.py

覆盖(计划 §八):
    - 看板六区块: 聚合结构/fail-soft 分区(单区块
      异常不阻断)/使用总览数学(直达率/语音占比)/
      指令命中排行/积分账本汇总/共创队列呈现
    - 高敏台账: executor 计数(发放/核销/码错/
      过期/通过率)
    - 治理桥接: member_level 维度直达率上报 46号
      (voice_L* 分组/46号侧采样入库/46号 29 档案
      断言零改动红线/台账 upsert 幂等自愈)
    - HTTP 层: 2 端点/鉴权
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


async def _new_trust() -> int:
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    import uuid
    suffix = uuid.uuid4().hex[:10]
    r = await TrustProfileService().create_role(
        "person", f"p4-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


async def _session(member_id: int) -> int:
    from services.xiaozhu_service import XiaozhuService
    return (await XiaozhuService().open_session(
        member_id))["sessionId"]


async def _text(sid: int, text: str) -> dict:
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().handle_text(sid, text)


async def _bind(member_id: int, trust_id: int):
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().bind_trust(
        member_id, trust_id, note="p4")


def _get_code(token: str) -> str:
    """测试钩子: 从沙箱单例取令牌真码(生产不可外泄)"""
    from services.xiaozhu_executor import get_executor
    entry = get_executor()._tokens.get(token)
    return entry["code"] if entry else ""


async def _seed_turns(member_id: int, hits: int,
                      generals: int) -> None:
    """灌轮次(hits 次指令命中 + generals 次兜底)"""
    sid = await _session(member_id)
    for _ in range(hits):
        await _text(sid, "小竹，看新品")
    for _ in range(generals):
        await _text(sid, "小竹，说说玄学")
        # 二次进入免唤醒窗, 兜底归 general


class TestDashboardZones:
    async def run(self):
        print("[01 看板六区块聚合]")
        reset_all()
        from services.xiaozhu_dashboard_service import (
            XiaozhuDashboardService,
        )
        svc = XiaozhuDashboardService()

        # 空库: 分区齐备 + zoneErrors 空
        # (49号P4 新增 fc/50号P5 新增 voice50 分区)
        b = await svc.build()
        record("区块齐备(48号六区块+fc+voice50)",
               set(b["zones"].keys()) == {
                   "usage", "commands", "confirm",
                   "points", "cocreate", "fairness",
                   "fc", "voice50"}
               and b["zoneErrors"] == [],
               str(list(b["zones"].keys())))
        record("红线常驻",
               len(b.get("redlines") or []) >= 5)

        # 灌数据: 会员1(level 1) 2 命中 + 1 兜底
        await _seed_turns(1, hits=2, generals=1)
        b = await svc.build()
        u = b["zones"]["usage"]
        record("使用总览会话数",
               u["sessions"] == 1, str(u["sessions"]))
        record("直达率数学(2/3)",
               u["directRate"] == 66.7,
               str(u["directRate"]))
        record("语音占比(全文本轮)",
               u["voiceShare"] == 0.0,
               str(u["voiceShare"]))
        c = b["zones"]["commands"]
        record("指令排行命中",
               c["ranking"][0]["action"] == "product.new"
               and c["ranking"][0]["hits"] == 2,
               str(c["ranking"]))
        record("兜底率(1/3)",
               c["fallbackRate"] == 33.3,
               str(c["fallbackRate"]))

        # fail-soft: 单区块异常不阻断
        async def _boom():
            raise RuntimeError("区块模拟故障")
        svc._zone_points = _boom
        b = await svc.build()
        record("fail-soft 分区",
               "points" in b["zoneErrors"]
               and b["zones"]["points"].get("error")
               and b["zones"]["usage"]["sessions"] == 1,
               str(b["zoneErrors"]))


class TestConfirmStats:
    async def run(self):
        print("[02 高敏台账计数]")
        reset_all()
        from services.xiaozhu_executor import get_executor
        ex = get_executor()
        s = ex.stats()
        record("台账初始零",
               s["issued"] == 0 and s["confirmed"] == 0
               and s["passRate"] is None)

        # 走完整 confirm 流(错码一次 + 核销一次)
        sid = await _session(40)
        await _bind(40, await _new_trust())
        r = await _text(sid, "小竹，把100信用分换成信值")
        token = r.get("confirmToken")
        s = ex.stats()
        record("发放计数+1", s["issued"] == 1,
               str(s["issued"]))
        from services.xiaozhu_service import XiaozhuService
        try:
            await XiaozhuService().confirm_action(
                token, "0000")
        except ValueError:
            pass
        s = ex.stats()
        record("码错计数+1", s["wrongCode"] == 1,
               str(s["wrongCode"]))
        await XiaozhuService().confirm_action(
            token, _get_code(token))
        s = ex.stats()
        record("核销计数+1", s["confirmed"] == 1,
               str(s["confirmed"]))
        record("通过率(1/1)",
               s["passRate"] == 100.0, str(s["passRate"]))
        # 过期计数
        r = await _text(sid, "小竹，把80信用分换成信值")
        token2 = r.get("confirmToken")
        ex._tokens[token2]["expiresAt"] = 0.0
        try:
            await XiaozhuService().confirm_action(
                token2, _get_code(token2))
        except KeyError:
            pass
        s = ex.stats()
        record("过期计数+1", s["expired"] == 1,
               str(s["expired"]))


class TestPointsZone:
    async def run(self):
        print("[03 积分账本区块]")
        reset_all()
        from services.xiaozhu_dashboard_service import (
            XiaozhuDashboardService,
        )
        # 会员1: 2 次指令 → +4
        await _seed_turns(1, hits=2, generals=0)
        b = await XiaozhuDashboardService().build()
        p = b["zones"]["points"]
        record("发放汇总(+4)",
               p["awarded"] == 4.0, str(p["awarded"]))
        record("余额总量=持分",
               p["balanceTotal"] == 4.0
               and p["holders"] == 1,
               str(p["balanceTotal"]))
        record("kind 分解",
               p["byKind"].get("command_done") == 4.0,
               str(p["byKind"]))


class TestFairnessBridge:
    async def run(self):
        print("[04 治理桥接]")
        reset_all()
        from services.xiaozhu_dashboard_service import (
            XiaozhuDashboardService,
        )
        svc = XiaozhuDashboardService()

        # 空数据 → 无有效分组
        r = await svc.bridge_fairness()
        record("空数据不上报",
               r["bridged"] == 0, str(r))

        # 灌数据: 会员1(L1) 6轮全命中; 会员2(L3) 5轮
        # 3命中2兜底(store 内置 level 1/3)
        await _seed_turns(1, hits=6, generals=0)
        await _seed_turns(2, hits=3, generals=2)
        r = await svc.bridge_fairness()
        record("双等级分组上报",
               r["bridged"] == 2
               and set(r["groups"]) == {
                   "voice_L1", "voice_L3"},
               str(r["groups"]))

        # 46号侧采样入库(无个人标识字段)
        from repositories.ai_governance_repository \
            import AiGovernance46Repository
        samples = await AiGovernance46Repository(
        ).list_samples("xiaozhu_voice")
        record("46号侧采样入库",
               len(samples) == 2, str(len(samples)))
        by_group = {s["group"]: s for s in samples}
        record("L1 直达率(100%)",
               by_group["voice_L1"]["score"] == 100.0,
               str(by_group.get("voice_L1")))
        record("L3 直达率(60%)",
               by_group["voice_L3"]["score"] == 60.0,
               str(by_group.get("voice_L3")))
        record("样本无个人标识",
               all(not ({"memberId", "trustId", "phone",
                         "email", "name", "userId", "id"}
                        & set(s)) for s in samples),
               str(samples[:1]))

        # 46号 29 档案断言零改动红线(桥接后 sync 不受扰)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        r2 = await AiGovernanceService().sync_registry()
        record("46号 sync 仍 31 档案",
               r2["discovered"] == 32,
               str(r2["discovered"]))
        # sync 会把 side-door 档案标 retired → 桥接自愈
        gov = await AiGovernance46Repository().get_gov(
            "xiaozhu_voice")
        record("sync 后标 retired(预期)",
               gov.get("status") == "retired",
               str(gov.get("status")))
        r3 = await svc.bridge_fairness()
        gov = await AiGovernance46Repository().get_gov(
            "xiaozhu_voice")
        record("桥接自愈回 active",
               gov.get("status") == "active"
               and r3["bridged"] == 2,
               str(gov.get("status")))

        # 样本不足等级不上报(<5 轮)
        await _seed_turns(1, hits=1, generals=0)
        # 会员1 现有 6+1=7 轮仍够; 用新低量等级验证:
        # 会员40 建会话 1 轮(不达 5)
        sid40 = await _session(40)
        await _text(sid40, "小竹，查优惠")
        r4 = await svc.bridge_fairness()
        record("低量等级不上报(L40 缺位)",
               "voice_L40" not in (r4["groups"] or []),
               str(r4["groups"]))

        # 看板⑥ 预览分组
        b = await svc.build()
        f = b["zones"]["fairness"]
        record("看板⑥等级分组预览",
               any(g["group"] == "voice_L1"
                   for g in f["groups"]),
               str(f["groups"]))


class TestHttp:
    async def run(self):
        print("[05 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.xiaozhu_routes import (
            register_xiaozhu_routes,
        )
        app = FastAPI()
        register_xiaozhu_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        await _seed_turns(1, hits=4, generals=1)

        # dashboard
        resp = client.get("/api/xiaozhu/dashboard",
                         headers=admin)
        body = resp.json()
        record("GET dashboard 200",
               resp.status_code == 200
               and set(body.get("zones") or {}) == {
                   "usage", "commands", "confirm",
                   "points", "cocreate", "fairness",
                   "fc", "voice50"},
               str(resp.status_code))
        record("看板 fail-soft 字段齐备",
               "zoneErrors" in body
               and "redlines" in body
               and "intervention" in body)
        resp = client.get("/api/xiaozhu/dashboard")
        record("dashboard 缺Role 403",
               resp.status_code == 403)

        # fairness-bridge
        resp = client.post(
            "/api/xiaozhu/dashboard/fairness-bridge",
            headers=admin)
        body = resp.json()
        record("POST bridge 200",
               resp.status_code == 200
               and body.get("bridged", 0) >= 1,
               str(body.get("groups")))
        resp = client.post(
            "/api/xiaozhu/dashboard/fairness-bridge")
        record("bridge 缺Role 403",
               resp.status_code == 403)

        # 46号审计含语音分组(跨模块 E2E——同 app 注册)
        from routes.ai_governance_routes import (
            register_ai_governance_routes,
        )
        register_ai_governance_routes(app)
        resp = client.post("/api/ai-gov/fairness/audit",
                           json={"scorerId":
                                 "xiaozhu_voice"},
                           headers=admin)
        groups = {g.get("group")
                  for g in (resp.json()
                            .get("groups") or [])}
        record("46号审计含语音分组",
               resp.status_code == 200
               and any(str(g).startswith("voice_")
                       for g in groups),
               str(groups))


async def run_all():
    await TestDashboardZones().run()
    await TestConfirmStats().run()
    await TestPointsZone().run()
    await TestFairnessBridge().run()
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
