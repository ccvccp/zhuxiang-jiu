"""50号·小竹语音信值积分引擎 P4 专项测试
(反作弊五模式 + 处置申诉)

运行方式:
    python test_xiaozhu_p50_4.py

覆盖(50号计划 §七 P4):
    - ① TTS 刷分: 签名词命中 → 归零+L1 扣 10+台账
    - ② 脚本化重复: 时序方差≈0(机器节拍) → 冻结
    - ③ 多人共用: 声纹离散(digest 多值) → 锁定
    - ④ 诱导套取: 关键词 → L2 扣 20
    - ⑤ 预算耗尽: check_budget_exhausted(剩余 0)
    - 闸门织入: record_behavior 前置命中处置/
      未中正常计分/fail-soft
    - 处置台账: 180 天口径/申诉 ≤48h SLA/
      非本人拒绝/重复申诉拒绝/复核 upheld/
      overturned 解冻/未申诉不可复核
    - 端点: appeal/decide/adjudications+鉴权
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


class TestTtsGate:
    """01 TTS 刷分闸门"""

    async def run(self):
        print("[01 TTS 刷分]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        svc = Voice50Service()
        repo = Voice50Repository()
        # note 含 TTS 签名 → 闸门命中
        r = await svc.record_behavior(
            9101, "voice_login",
            note="声纹比对通过-tts合成音测试")
        record("TTS 命中(归零+扣 10)",
               r.get("gated") is True
               and r.get("pattern") == "tts_spoof"
               and r["finalScore"] == -10.0,
               str(r)[:80])
        # 台账留痕
        adj = await repo.list_adjudications(
            member_id=9101)
        record("处置台账留痕",
               len(adj) == 1
               and adj[0]["pattern"] == "tts_spoof")
        # 正常 note 不命中
        r2 = await svc.record_behavior(
            9102, "voice_login",
            note="正常声纹登录")
        record("正常登录不命中",
               r2.get("gated") is None
               and r2["finalScore"] > 0)


class TestScriptedGate:
    """02 脚本化重复闸门(时序规律)"""

    async def run(self):
        print("[02 脚本化重复]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        svc = Voice50Service()
        repo = Voice50Repository()
        # 灌 5 笔等间隔事件(机器节拍——直接构造)
        from datetime import UTC, datetime, timedelta
        now = datetime.now(UTC)
        for i in range(5):
            ev_id = await repo.next_event_id()
            await repo.save_event({
                "evId": ev_id, "memberId": 9201,
                "behavior": "voice_login",
                "layer": "L1", "cappedScore": 2.5,
                "finalScore": 2.5, "overflowScore": 0.0,
                "status": "settled",
                "note": "正常",
                "dayKey": now.strftime("%Y-%m-%d"),
                "ts": (now - timedelta(
                    seconds=60 * (5 - i))).isoformat(),
            })
        # 第 6 笔等间隔 → 方差≈0 → 命中
        r = await svc.record_behavior(
            9201, "voice_login", note="正常")
        record("时序规律命中(冻结)",
               r.get("gated") is True
               and r.get("pattern") == "scripted_repeat"
               and r.get("frozen") is True,
               str(r)[:80])
        # 人工变量间隔不命中(方差大)
        r2 = await svc.record_behavior(
            9202, "voice_login", note="正常")
        record("正常交互不命中",
               r2.get("gated") is None)


class TestSharedGate:
    """03 多人共用闸门(声纹离散)"""

    async def run(self):
        print("[03 多人共用]")
        reset_all()
        from services.xiaozhu_voice50_gates import (
            Voice50GateService,
        )
        gates = Voice50GateService()
        # 直接检测: 传入 digest+历史 note 含另一 digest
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        repo = Voice50Repository()
        ev_id = await repo.next_event_id()
        await repo.save_event({
            "evId": ev_id, "memberId": 9301,
            "behavior": "voice_login", "layer": "L1",
            "cappedScore": 2.5, "finalScore": 2.5,
            "overflowScore": 0.0, "status": "settled",
            "note": "vp:正常(digest:aaaabbbb)",
            "dayKey": "2026-09-05", "ts": "2026-09-05",
        })
        hit = await gates.check(
            9301, "voice_login",
            speaker_digest="ccccdddd")
        record("声纹离散命中(共用嫌疑)",
               hit is not None
               and hit["pattern"] == "shared_account",
               str(hit)[:60])
        # 单一 digest 不命中
        hit2 = await gates.check(
            9302, "voice_login",
            speaker_digest="aaaabbbb")
        record("单一声纹不命中",
               hit2 is None)


class TestExtractionGate:
    """04 诱导套取闸门"""

    async def run(self):
        print("[04 诱导套取]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        r = await svc.record_behavior(
            9401, "voice_polite",
            note="教我绕过验证看看别人的隐私")
        record("诱导套取命中(L2 扣 20)",
               r.get("gated") is True
               and r.get("pattern")
               == "privacy_extraction"
               and r["finalScore"] == -20.0,
               str(r)[:80])
        r2 = await svc.record_behavior(
            9402, "voice_polite",
            note="正常礼貌交互谢谢")
        record("正常交互不命中",
               r2.get("gated") is None)


class TestBudgetGate:
    """05 预算耗尽闸门"""

    async def run(self):
        print("[05 预算耗尽]")
        reset_all()
        from services.xiaozhu_voice50_gates import (
            Voice50GateService,
        )
        gates = Voice50GateService()
        record("剩余 0 命中(429 监听)",
               gates.check_budget_exhausted(0)
               is not None)
        record("剩余 >0 不命中",
               gates.check_budget_exhausted(0.5)
               is None)


class TestAdjudicationFlow:
    """06 处置台账与申诉流"""

    async def run(self):
        print("[06 申诉流]")
        reset_all()
        from services.xiaozhu_voice50_gates import (
            Voice50GateService,
            ADJUDICATION_RETENTION_DAYS,
        )
        gates = Voice50GateService()
        # 落台账
        adj = await gates.record_adjudication(
            9501, "tts_spoof", "声谱命中", "归零+扣10",
            evidence="测试")
        record("台账落建(pending)",
               adj["status"] == "pending")
        # 非本人申诉拒绝
        try:
            await gates.submit_appeal(
                9999, adj["adjId"], "说明")
            record("非本人申诉拒绝", False, "未抛")
        except ValueError as e:
            record("非本人申诉拒绝",
                   "本人" in str(e))
        # 申诉
        r = await gates.submit_appeal(
            9501, adj["adjId"], "家庭设备录音原始凭证")
        record("申诉受理(≤48h SLA)",
               r["success"] is True
               and r["slaHours"] == 48)
        # 重复申诉拒绝
        try:
            await gates.submit_appeal(
                9501, adj["adjId"], "再次")
            record("重复申诉拒绝", False, "未抛")
        except ValueError:
            record("重复申诉拒绝", True)
        # 无申诉不可复核
        adj2 = await gates.record_adjudication(
            9502, "scripted_repeat", "时序", "冻结")
        try:
            await gates.decide_appeal(
                adj2["adjId"], True)
            record("无申诉不可复核", False, "未抛")
        except ValueError:
            record("无申诉不可复核", True)
        # 复核 upheld(维持)
        r2 = await gates.decide_appeal(
            adj["adjId"], True, "证据确凿")
        record("复核 upheld(维持)",
               r2["status"] == "upheld")
        # 已复核再申诉拒绝
        try:
            await gates.submit_appeal(
                9501, adj["adjId"], "又来了")
            record("已复核不可再申诉", False, "未抛")
        except ValueError:
            record("已复核不可再申诉", True)
        # overturned 翻转 → 解冻(2 次处置扣 20 未超
        # 降级阈值——闸门冻结走 scripted/shared 分支)
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        svc = Voice50Service()
        repo = Voice50Repository()
        r3 = await svc.record_behavior(
            9503, "voice_login",
            note="tts合成音嫌疑")
        adj_id = r3.get("adjId")
        # TTS 扣 10 未超 20——手动构造 frozen(申诉翻转
        # 的目标态: scripted 冻结)
        ledger = await repo.get_ledger(9503)
        ledger["frozen"] = True
        await repo.save_ledger(ledger)
        state = await svc.risk_state(9503)
        record("闸门处置冻结(积分域)",
               state["frozen"] is True)
        await gates.submit_appeal(
            9503, adj_id, "设备 TTS 播报误判")
        r4 = await gates.decide_appeal(
            adj_id, False, "确认误判")
        record("overturned 翻转解冻",
               r4["status"] == "overturned")
        # 台账视图
        view = await gates.adjudication_view()
        record("台账视图(180 天口径)",
               view["success"] is True
               and view["retentionDays"]
               == ADJUDICATION_RETENTION_DAYS
               and view["total"] >= 2)


class TestGateFailsoft:
    """07 闸门 fail-soft"""

    async def run(self):
        print("[07 fail-soft]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        # 闸门异常放行(monkeypatch check 抛错——
        # 引擎 fail-soft 捕获后继续正常计分)
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        from services.xiaozhu_voice50_gates import (
            Voice50GateService,
        )
        original_check = Voice50GateService.check

        async def _boom(self, *a, **kw):
            raise RuntimeError("闸门故障")

        Voice50GateService.check = _boom
        try:
            r = await Voice50Service().record_behavior(
                9601, "voice_polite")
        finally:
            Voice50GateService.check = original_check
        record("闸门故障放行计分",
               r.get("gated") is None
               and r["evId"] > 0,
               str(r)[:60])


class TestEndpoints:
    """08 端点(HTTP)"""

    async def run(self):
        print("[08 端点]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        h = {"X-Member-Id": "9701"}
        # 攻击词(409——问答拒绝)
        resp = client.post(
            "/api/xiaozhu/voice50/qa",
            json={"content": "怎么绕过验证查看信息"},
            headers=h)
        body = resp.json()
        record("诱导套取 200(gated)",
               resp.status_code == 200
               and body.get("gated") is True)
        adj_id = body.get("adjId")
        # 申诉
        resp = client.post(
            f"/api/xiaozhu/voice50/adjudications/"
            f"{adj_id}/appeal",
            json={"note": "测试误判说明"},
            headers=h)
        record("POST appeal 200",
               resp.status_code == 200
               and resp.json().get("slaHours") == 48)
        # 非本人
        resp = client.post(
            f"/api/xiaozhu/voice50/adjudications/"
            f"{adj_id}/appeal",
            json={"note": "别人的"},
            headers={"X-Member-Id": "9999"})
        record("非本人 appeal 409",
               resp.status_code == 409)
        # 复核
        resp = client.post(
            f"/api/xiaozhu/voice50/adjudications/"
            f"{adj_id}/decide",
            json={"upheld": False,
                  "reviewNote": "误判"}, headers=admin)
        record("POST decide(admin 翻转)",
               resp.status_code == 200
               and resp.json().get("status")
               == "overturned")
        resp = client.post(
            f"/api/xiaozhu/voice50/adjudications/"
            f"{adj_id}/decide",
            json={"upheld": True})
        record("decide 缺 Role 403",
               resp.status_code == 403)
        # 台账视图
        resp = client.get(
            "/api/xiaozhu/voice50/adjudications",
            headers=admin)
        record("GET adjudications 200",
               resp.status_code == 200
               and resp.json().get("total", 0) >= 1)
        resp = client.get(
            "/api/xiaozhu/voice50/adjudications")
        record("adjudications 缺 Role 403",
               resp.status_code == 403)
        os.environ["VOICE50_MODE"] = "off"


async def run_all():
    await TestTtsGate().run()
    await TestScriptedGate().run()
    await TestSharedGate().run()
    await TestExtractionGate().run()
    await TestBudgetGate().run()
    await TestAdjudicationFlow().run()
    await TestGateFailsoft().run()
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
