"""50号·小竹语音信值积分引擎 P3 专项测试
(L3 五行为 + 公平天花板)

运行方式:
    python test_xiaozhu_p50_3.py

覆盖(50号计划 §七 P3):
    - L3 动态天花板: 基线×2(比全局紧)/新用户首月
      上浮 50%/溢出 ×0.1
    - 佐证 per-claim 验真(双源采信 ×2/孤证基础分)
    - 语料捐赠流(提交基础 10/采纳 +20/驳回)
    - 社区问答(点赞 ×1.5/攻击词拒绝)
    - 伴侣月度(日均 <3 拒绝/月限 1/多样性 ×1.3)
    - FL 预留接口(预算前置拒绝)
    - 公平桥(L3 分布三组/样本 <5 不上报/46号 side-door)
    - 端点鉴权
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


class TestL3Cap:
    """01 L3 动态天花板"""

    async def run(self):
        print("[01 L3 动态天花板]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        from services.xiaozhu_voice50_rules import (
            VOICE_RULES,
        )
        svc = Voice50Service()
        # 临时放开日限(灌爆 L3 封顶)
        saved = VOICE_RULES["voice_community_qa"][
            "dailyCap"]
        VOICE_RULES["voice_community_qa"][
            "dailyCap"] = None
        # 新用户 L3 cap = max(基线 10×2×1.5, 30)=30
        results = []
        for _ in range(12):
            results.append(
                await svc.record_behavior(
                    8101, "voice_community_qa"))
        capped = [r for r in results
                  if r["overflowScore"] > 0]
        record("L3 新用户封顶(30)触发",
               bool(capped), str(len(capped)))
        # cap=30: 前 3-4 笔满(8+16+24+30)
        record("新用户 L3 上浮(基线×2×1.5=30)",
               results[3]["cappedScore"] == 2.0
               or results[3]["overflowScore"] > 0,
               f"{results[3]}")
        # 老用户(事件 30 天前) L3 cap = 基线×2=20
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        repo = Voice50Repository()
        # 灌老用户历史事件(ts=40 天前)
        from datetime import UTC, datetime, timedelta
        old_ts = (datetime.now(UTC)
                  - timedelta(days=40)).isoformat()
        ev_id = await repo.next_event_id()
        await repo.save_event({
            "evId": ev_id, "memberId": 8102,
            "behavior": "voice_polite", "layer": "L2",
            "cappedScore": 10.0, "finalScore": 10.0,
            "status": "settled", "dayKey": "2000-01-01",
            "ts": old_ts,
        })
        results2 = []
        for _ in range(6):
            results2.append(
                await svc.record_behavior(
                    8102, "voice_community_qa"))
        record("老用户 L3 封顶更紧(基线×2)",
               any(r["overflowScore"] > 0
                   for r in results2),
               str([r["cappedScore"]
                    for r in results2]))
        VOICE_RULES["voice_community_qa"][
            "dailyCap"] = saved
        # 新用户判定 helper
        record("新用户判定(首事件 30 天内)",
               await svc._is_newcomer(8103) is True)
        record("老用户判定(40 天前事件)",
               await svc._is_newcomer(8102) is False)


class TestEvidence:
    """02 佐证 per-claim 验真"""

    async def run(self):
        print("[02 佐证验真]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        # 双源+可核验要素(数字) → 采信 ×2(base 12→24)
        r = await svc.record_evidence(
            8201, "社区志愿服务20260901现场录音佐证8小时",
            sources=["gov_penalty", "media"])
        record("双源采信 ×2(=24)",
               abs(r["finalScore"] - 24.0) < 1e-6
               and r["verify"]["verified"] is True,
               str(r["finalScore"]))
        # 权威源单源 → 采信
        r2 = await svc.record_evidence(
            8201, "行政处罚公示2026第88号平台截图佐证",
            sources=["court"])
        record("权威源采信 ×2",
               r2["verify"]["verified"] is True
               and abs(r2["finalScore"] - 24.0) < 1e-6)
        # 孤证 → 未采信(基础 12)
        r3 = await svc.record_evidence(
            8201, "个人口头描述20260901志愿服务经历",
            sources=["self"])
        record("孤证不采信(基础 12)",
               r3["verify"]["verified"] is False
               and abs(r3["finalScore"] - 12.0) < 1e-6,
               str(r3["finalScore"]))
        # 证据过短 → 验真 0
        r4 = await svc.record_evidence(
            8201, "短",
            sources=["gov_penalty", "media"])
        record("短证据验真拒绝",
               r4["verify"]["confidence"] == 0.0)
        # 缺可核验要素(无数字) → 0.5 不采信
        # (8201 已提交 4 次——第 5 次语义复用会被 P4
        #  闸门拦截[正确语义]; 换新会员 8202 测验真分支)
        r5 = await svc.record_evidence(
            8202, "社区志愿服务现场录音佐证八小时",
            sources=["gov_penalty", "media"])
        record("缺可核验要素不采信",
               r5["verify"]["confidence"] == 0.5
               and abs(r5["finalScore"] - 12.0) < 1e-6)


class TestCorpus:
    """03 语料捐赠流"""

    async def run(self):
        print("[03 语料捐赠]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        # 提交(基础 10)
        r = await svc.submit_corpus(
            8301, "我想用语音查询竹香酒的窖藏年份场景")
        record("提交得基础 10",
               r["success"] is True
               and abs(r["baseScore"] - 10.0) < 1e-6,
               str(r["baseScore"]))
        # 场景过短拒绝
        try:
            await svc.submit_corpus(8301, "短")
            record("短场景拒绝", False, "未抛")
        except ValueError:
            record("短场景拒绝", True)
        # 采纳 +20
        r2 = await svc.review_corpus(
            r["corpusId"], adopted=True, note="纳入")
        record("采纳 +20(总 30)",
               r2["status"] == "adopted")
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        evs = await Voice50Repository().list_events(
            member_id=8301)
        corpus = [e for e in evs
                  if e["behavior"]
                  == "voice_corpus_donate"]
        record("语料两事件(10+30)",
               len(corpus) == 2
               and abs(corpus[-1]["finalScore"]
                       - 30.0) < 1e-6,
               str([e["finalScore"] for e in corpus]))
        # 重复审核拒绝
        try:
            await svc.review_corpus(
                r["corpusId"], adopted=True)
            record("重复审核拒绝", False, "未抛")
        except ValueError:
            record("重复审核拒绝", True)
        # 驳回(基础分保留, 无 bonus)
        r3 = await svc.submit_corpus(
            8302, "方言点酒场景描述完整版")
        r4 = await svc.review_corpus(
            r3["corpusId"], adopted=False)
        record("驳回状态留痕",
               r4["status"] == "rejected")


class TestQa:
    """04 社区问答"""

    async def run(self):
        print("[04 社区问答]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        r = await svc.record_qa(8401, "信值修复窗口说明")
        record("问答基础 8",
               abs(r["finalScore"] - 8.0) < 1e-6)
        r2 = await svc.record_qa(
            8401, "信值兑换汇率问题解答", liked=True)
        record("被点赞 ×1.5(=12)",
               abs(r2["finalScore"] - 12.0) < 1e-6,
               str(r2["finalScore"]))
        # 攻击词拒绝
        try:
            await svc.record_qa(8401, "你这个废物")
            record("攻击内容拒绝", False, "未抛")
        except ValueError as e:
            record("攻击内容拒绝",
                   "攻击性语言" in str(e))


class TestCompanion:
    """05 伴侣月度核算"""

    async def run(self):
        print("[05 伴侣月度]")
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
        # 无交互 → 拒绝
        r = await svc.check_companion(8501)
        record("无交互拒绝",
               r["eligible"] is False)

        async def _seed(member: int, n: int,
                        behaviors: list) -> None:
            """灌历史事件(ts 分布近 30 天——绕过日封顶)"""
            now = datetime.now(UTC)
            for i in range(n):
                ev_id = await repo.next_event_id()
                day = now - timedelta(
                    days=i % 29)
                await repo.save_event({
                    "evId": ev_id, "memberId": member,
                    "behavior": behaviors[i % len(
                        behaviors)],
                    "layer": "L3",
                    "cappedScore": 0.5,
                    "finalScore": 0.5,
                    "overflowScore": 0.0,
                    "status": "settled",
                    "dayKey": day.strftime("%Y-%m-%d"),
                    "ts": day.isoformat(),
                })

        # 日均 ≥3(95 事件/30 天)+单一行为(多样性低)
        await _seed(8502, 95, ["voice_polite"])
        r2 = await svc.check_companion(8502)
        record("日均 ≥3 合格(发放)",
               r2["eligible"] is True
               and r2["dailyAvg"] >= 3.0,
               str(r2.get("dailyAvg")))
        record("多样性不足无 ×1.3(=100)",
               abs(r2["award"]["finalScore"]
                   - 100.0) < 1e-6,
               str(r2["award"]["finalScore"]))
        # 月限 1
        r3 = await svc.check_companion(8502)
        record("月限 1(二次拒绝)",
               r3["eligible"] is False
               and "月限" in r3["reason"])
        # 日均不足
        await _seed(8503, 50, ["voice_polite"])
        r4 = await svc.check_companion(8503)
        record("日均 <3 拒绝",
               r4["eligible"] is False
               and "日均" in r4["reason"])
        # 多样性 >0.6 → ×1.3(9+ 种行为)
        behaviors9 = ["voice_polite", "voice_login",
                      "voice_confirm", "voice_clear_intent",
                      "voice_privacy_grant", "voice_feedback",
                      "voice_inclusive", "voice_community_qa",
                      "voice_corpus_donate"]
        await _seed(8504, 95, behaviors9)
        r5 = await svc.check_companion(8504)
        record("多样性 >0.6 ×1.3(=130)",
               r5["eligible"] is True
               and abs(r5["award"]["finalScore"]
                       - 130.0) < 1e-6,
               str(r5["award"]["finalScore"]))


class TestFlGradient:
    """06 FL 预留接口"""

    async def run(self):
        print("[06 FL 预留接口]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        # 预算充足(默认 1.0)
        r = await svc.record_fl_gradient(
            8601, quality=0.8)
        record("质量 >0.7 ×1.5(=22.5)",
               abs(r["finalScore"] - 22.5) < 1e-6,
               str(r["finalScore"]))
        r2 = await svc.record_fl_gradient(
            8601, quality=0.5)
        record("质量低无加成(=15)",
               abs(r2["finalScore"] - 15.0) < 1e-6)
        # 预算耗尽 → 拒绝
        from services.xiaozhu_privacy_service import (
            _today_key,
        )
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        await Xiaozhu48Repository().save_privacy_budget({
            "memberId": 8602, "dailyBudget": 1.0,
            "preference": 1.0, "usedToday": 0.95,
            "dayKey": _today_key(), "history": [],
            "ts": ""})
        try:
            await svc.record_fl_gradient(8602)
            record("预算不足拒绝", False, "未抛")
        except ValueError as e:
            record("预算不足拒绝",
                   "隐私预算不足" in str(e))


class TestFairnessBridge:
    """07 公平桥(L3 分布)"""

    async def run(self):
        print("[07 公平桥]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        # 空 → 无需上报
        r = await svc.bridge_fairness()
        record("无 L3 事件不上报",
               r["bridged"] == 0)
        # 灌 6 个会员低分组(每个 <10)
        for m in range(8701, 8707):
            await svc.record_behavior(
                m, "voice_community_qa")
        r2 = await svc.bridge_fairness()
        record("低分组上报(≥5 样本)",
               r2["bridged"] == 1
               and r2["groups"] == ["l3_low"],
               str(r2))
        # 46号 side-door 档案核验(28 档案断言零改动)
        from repositories.ai_governance_repository \
            import AiGovernance46Repository
        gov = await AiGovernance46Repository().get_gov(
            "voice50_l3_credits")
        record("side-door 档案入册",
               gov is not None
               and gov.get("status") == "active")
        # 样本不足组不上报(1 个 high)
        await svc.record_behavior(
            8801, "voice_corpus_donate",
            gains={"adopted": True})
        r3 = await svc.bridge_fairness()
        record("单 high 样本不上报",
               "l3_high" not in r3["groups"])


class TestEndpoints:
    """08 端点(HTTP)"""

    async def run(self):
        print("[08 端点]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        h = {"X-Member-Id": "8901"}
        # evidence
        resp = client.post(
            "/api/xiaozhu/voice50/evidence",
            json={"evidence": "社区服务20260901录音佐证8小时",
                  "sources": ["gov_penalty", "media"]},
            headers=h)
        body = resp.json()
        record("POST evidence(采信)",
               resp.status_code == 200
               and body.get("verify", {}).get(
                   "verified") is True,
               str(resp.status_code))
        # corpus
        resp = client.post(
            "/api/xiaozhu/voice50/corpus",
            json={"scenario": "语音查询窖藏年份场景"},
            headers=h)
        body = resp.json()
        record("POST corpus 200",
               resp.status_code == 200
               and body.get("status") == "pending")
        # review
        resp = client.post(
            f"/api/xiaozhu/voice50/corpus/"
            f"{body.get('corpusId')}/review",
            json={"adopted": True}, headers=admin)
        record("POST review(admin)",
               resp.status_code == 200
               and resp.json().get("status")
               == "adopted")
        resp = client.post(
            "/api/xiaozhu/voice50/corpus/1/review",
            json={"adopted": True})
        record("review 缺 Role 403",
               resp.status_code == 403)
        # qa
        resp = client.post(
            "/api/xiaozhu/voice50/qa",
            json={"content": "信值问题解答",
                  "liked": True}, headers=h)
        record("POST qa 200(点赞)",
               resp.status_code == 200
               and abs(resp.json()["finalScore"]
                       - 12.0) < 1e-6)
        resp = client.post(
            "/api/xiaozhu/voice50/qa",
            json={"content": "废物"}, headers=h)
        record("攻击内容 409",
               resp.status_code == 409)
        # companion
        resp = client.post(
            "/api/xiaozhu/voice50/companion/check",
            headers=h)
        record("POST companion(未达标)",
               resp.status_code == 200
               and resp.json().get("eligible")
               is False)
        # fairness-bridge
        resp = client.post(
            "/api/xiaozhu/voice50/fairness-bridge",
            headers=admin)
        record("POST fairness-bridge 200",
               resp.status_code == 200
               and resp.json().get("success") is True)
        resp = client.post(
            "/api/xiaozhu/voice50/fairness-bridge")
        record("fairness 缺 Role 403",
               resp.status_code == 403)
        os.environ["VOICE50_MODE"] = "off"


async def run_all():
    await TestL3Cap().run()
    await TestEvidence().run()
    await TestCorpus().run()
    await TestQa().run()
    await TestCompanion().run()
    await TestFlGradient().run()
    await TestFairnessBridge().run()
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
