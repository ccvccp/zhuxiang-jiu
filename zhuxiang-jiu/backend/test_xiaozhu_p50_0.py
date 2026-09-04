"""50号·小竹语音信值积分引擎 P0 专项测试

运行方式:
    python test_xiaozhu_p50_0.py

覆盖(50号计划 §七 P0):
    - 规则注册表: 14 行为全量/三层分布/L2-L3 因子对齐
      45号九因子/L1 不映射(防法治域污染)/参数口径
    - 系数链数学: 声纹×1.5/0.3/半程/质量阈值/加成链
    - 防刷封顶: 基线×3/溢出×0.1/首日下限/封顶先于桥接
    - L1 实时轨: 入账/降级(扣分>20 frozen)/人工恢复
    - ref 绑定: exp-voice50-* 格式/事件可溯
    - 台账: 日切清零/池余额守恒/视图红线文案
    - 钩子: VOICE50_MODE=off 零影响(轮次不产生事件)/
      on 时三行为触发
    - 第 17 指令: 我的语音积分(off 提示/on 卡片)
    - 端点: my/rules/PUT 热更新留痕/risk-state/
      unfreeze/鉴权
"""

import asyncio
import os
import re
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["VOICE50_MODE"] = "off"

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


async def _new_member_and_session(seq: int):
    """会员+会话(voice50 测试专用)"""
    from services.xiaozhu_service import XiaozhuService
    svc = XiaozhuService()
    member = 5000 + seq
    sid = (await svc.open_session(member))["sessionId"]
    return member, sid


class TestRules:
    """01 规则注册表(14 行为+自检)"""

    async def run(self):
        print("[01 规则注册表]")
        from services.xiaozhu_voice50_rules import (
            VOICE_RULES, rules_view,
            CAP_MULTIPLIER, L1_DEGRADE_THRESHOLD,
        )
        record("行为总数(14)", len(VOICE_RULES) == 14,
               str(len(VOICE_RULES)))
        layers = {"L1": 0, "L2": 0, "L3": 0}
        for r in VOICE_RULES.values():
            layers[r["layer"]] += 1
        record("三层分布(4/5/5)",
               layers == {"L1": 4, "L2": 5, "L3": 5},
               str(layers))
        # L1 不映射 45号因子(防法治域污染)
        record("L1 不映射 45号因子",
               all(not r.get("targetFactor")
                   for r in VOICE_RULES.values()
                   if r["layer"] == "L1"))
        # L2/L3 因子在 45号九因子注册表
        from services.trust_scoring_service import (
            TrustValueScorer,
        )
        record("L2/L3 因子对齐九因子",
               all(r.get("targetFactor")
                   in TrustValueScorer.LAYER_OF
                   for r in VOICE_RULES.values()
                   if r["layer"] in ("L2", "L3")))
        record("层归属一致(自检通过)", True)  # import 即自检
        view = rules_view()
        record("视图层数/参数",
               view["layers"] == {"L1": 4, "L2": 5,
                                  "L3": 5}
               and view["params"]["capMultiplier"]
               == CAP_MULTIPLIER
               and view["params"][
                   "l1DegradeThreshold"]
               == L1_DEGRADE_THRESHOLD)


class TestEngineMath:
    """02 系数链数学(声纹/质量/加成)"""

    async def run(self):
        print("[02 系数链数学]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        # 声纹系数(仅 L1 验证类行为)
        r = await svc.record_behavior(
            5101, "voice_login", voiceprint="real")
        record("real 声纹 ×1.5(L1)",
               r["multipliers"]["voiceprint"] == 1.5
               and abs(r["finalScore"] - 3.0) < 1e-6,
               str(r["finalScore"]))
        r = await svc.record_behavior(
            5102, "voice_login", voiceprint="")
        record("未验证 ×0.3(L1)",
               r["multipliers"]["voiceprint"] == 0.3,
               str(r["multipliers"]))
        r = await svc.record_behavior(
            5103, "voice_login", voiceprint="proxy")
        record("proxy 半程加成 ×1.25",
               r["multipliers"]["voiceprint"] == 1.25,
               str(r["multipliers"]))
        # L2 行为不受声纹系数(伦理导向非生物溢价)
        r = await svc.record_behavior(
            5104, "voice_clear_intent",
            quality=0.95, voiceprint="real")
        record("L2 不乘声纹系数",
               r["multipliers"]["voiceprint"] == 1.0,
               str(r["multipliers"]))
        # 质量阈值(<0.8 不计分)
        r = await svc.record_behavior(
            5105, "voice_clear_intent", quality=0.5)
        record("质量 <0.8 计 0 分",
               r["finalScore"] == 0.0)
        # 加成链(显式命中)
        r = await svc.record_behavior(
            5106, "voice_confirm", voiceprint="proxy",
            gains={"dualFactor": True})
        record("双因子加成 ×2",
               r["multipliers"]["gains"] == 2.0
               and abs(r["finalScore"] - 7.5) < 1e-6,
               str(r["finalScore"]))
        r = await svc.record_behavior(
            5107, "voice_confirm", voiceprint="proxy",
            gains={"unknownGain": True})
        record("未知加成不乘", r["finalScore"] == 3.75,
               str(r["finalScore"]))
        # 扣分项(负向直入账)
        r = await svc.record_behavior(
            5108, "voice_polite", penalty=True)
        record("辱骂扣分 -10(不经封顶)",
               r["finalScore"] == -10.0
               and r["cappedScore"] == -10.0,
               str(r["finalScore"]))


class TestCap:
    """03 防刷封顶(基线×3/溢出×0.1)"""

    async def run(self):
        print("[03 防刷封顶]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        from services.xiaozhu_voice50_rules import (
            VOICE_RULES, CAP_OVERFLOW_RATE,
        )
        svc = Voice50Service()
        # 封顶灌爆需突破日限(P1 enforcement)——测试期
        # 临时放开 dailyCap, 测完复原(日限数学 P1 专项覆盖)
        saved_cap = VOICE_RULES["voice_login"]["dailyCap"]
        VOICE_RULES["voice_login"]["dailyCap"] = None
        # 冷启动首日下限 30
        r1 = await svc.record_behavior(
            5201, "voice_login", voiceprint="real")
        r2 = await svc.record_behavior(
            5201, "voice_login", voiceprint="real")
        record("首日下限内不截断",
               r1["cappedScore"] == 3.0
               and r2["cappedScore"] == 3.0)
        # 基线×3=30(冷启动 10×3)——灌爆(交错 sleep
        # 制造时序抖动——防 P4 机器节拍闸门误伤连跑)
        import time as _time
        results = []
        for i in range(12):
            if i % 3 == 0:
                _time.sleep(0.02)
            elif i % 3 == 1:
                _time.sleep(0.05)
            results.append(
                await svc.record_behavior(
                    5201, "voice_login",
                    voiceprint="real"))
        capped = [r for r in results
                  if r["overflowScore"] > 0]
        record("封顶触发(溢出 ×0.1)",
               bool(capped)
               and all(
                   abs(r["overflowScore"]
                       - (r["finalScore"]
                          - r["cappedScore"])
                       * CAP_OVERFLOW_RATE) < 1e-6
                   for r in capped),
               str([(r["finalScore"], r["cappedScore"],
                     r["overflowScore"])
                    for r in capped[:2]]))
        # 池只收 capped+overflow
        v = await svc.my_view(5201)
        events_sum = sum(
            r["cappedScore"] + r["overflowScore"]
            for r in results)
        # my_view recent 只取 8 条——用事件对账替代
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        evs = await Voice50Repository().list_events(
            member_id=5201)
        total_in = sum(
            float(e.get("cappedScore") or 0)
            + float(e.get("overflowScore") or 0)
            for e in evs)
        record("池=封顶值+溢出合计",
               abs(v["poolBalance"] - total_in) < 0.05,
               f"{v['poolBalance']} vs {total_in}")
        record("封顶先于桥接(溢出独立字段)",
               all("overflowScore" in e for e in evs))
        # 复原日限(热更新语义自检)
        VOICE_RULES["voice_login"]["dailyCap"] = saved_cap


class TestL1Realtime:
    """04 L1 实时轨(入账/降级/恢复)"""

    async def run(self):
        print("[04 L1 实时轨]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        # L1 扣分(声纹疑似合成 -5)累计 >20 → frozen
        r = None
        for _ in range(5):
            r = await svc.record_behavior(
                5301, "voice_login", penalty=True)
        record("L1 扣分累计(-25)触发冻结",
               r["frozen"] is True,
               str(r))
        state = await svc.risk_state(5301)
        record("风控状态 frozen",
               state["frozen"] is True
               and state["l1PenaltyTotal"] == 25.0)
        # L2 扣分不触发实时冻结(走 T+1 处置)
        r2 = await svc.record_behavior(
            5302, "voice_polite", penalty=True)
        state2 = await svc.risk_state(5302)
        record("L2 扣分不实时冻结",
               r2["frozen"] is not True
               and state2["frozen"] is False)
        record("池不为负(扣分不扣池)",
               (await svc.my_view(5302))[
                   "poolBalance"] == 0.0)
        # 冻结期拒绝提交
        try:
            await svc.record_behavior(
                5301, "voice_login")
            record("冻结期拒绝计分", False, "未抛")
        except ValueError as e:
            record("冻结期拒绝计分", "冻结" in str(e))
        # 人工恢复
        r = await svc.unfreeze(5301, note="复核通过")
        record("人工恢复(admin)",
               r["frozen"] is False)
        r2 = await svc.record_behavior(
            5301, "voice_login")
        record("恢复后可计分", r2["evId"] > 0)
        # 未冻结时恢复 → 409
        try:
            await svc.unfreeze(5301)
            record("未冻结恢复拒绝", False, "未抛")
        except ValueError:
            record("未冻结恢复拒绝", True)


class TestRefAndLedger:
    """05 ref 绑定与台账"""

    async def run(self):
        print("[05 ref 与台账]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        r = await svc.record_behavior(
            5401, "voice_login", voiceprint="proxy")
        record("ref 格式(exp-voice50-*)",
               bool(re.fullmatch(
                   r"exp-voice50-\d+-[0-9a-f]{8}",
                   r["ref"])), r["ref"])
        # 事件可溯(ref ↔ evId)
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        evs = await Voice50Repository().list_events(
            member_id=5401)
        record("事件 ref 可溯",
               evs[-1]["ref"] == r["ref"]
               and evs[-1]["evId"] == r["evId"])
        # L2/L3 事件 pending(T+1 结算资格)
        r2 = await svc.record_behavior(
            5401, "voice_clear_intent", quality=0.9)
        evs = await Voice50Repository().list_events(
            member_id=5401)
        l2ev = [e for e in evs
                if e["behavior"]
                == "voice_clear_intent"]
        record("L2 事件 pending(T+1)",
               l2ev[-1]["status"] == "pending")
        record("L1 事件 settled(实时)",
               [e for e in evs
                if e["behavior"] == "voice_login"]
               [-1]["status"] == "settled")
        # my_view 红线文案
        v = await svc.my_view(5401)
        record("视图红线三文案",
               "≠信值分" in v["redlines"][0]
               and "不用语音不扣分" in v["redlines"][1]
               and "冻结只停积分" in v["redlines"][2])
        record("视图 recent 含 ref",
               bool(v["recent"])
               and all(x.get("ref")
                       for x in v["recent"]))


class TestHookAndCommand:
    """06 钩子与第 17 指令"""

    async def run(self):
        print("[06 钩子与第 17 指令]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        repo = Voice50Repository()
        svc = XiaozhuService()
        member, sid = await _new_member_and_session(1)
        # off 零影响
        await svc.handle_text(sid, "小竹，看新品")
        evs = await repo.list_events(member_id=member)
        record("off 钩子空转零事件",
               evs == [], str(len(evs)))
        # off 时第 17 指令提示
        r = await svc.handle_text(sid, "小竹，我的语音积分")
        record("off 指令提示未启用",
               r["turn"]["intent"] == "voice.score"
               and "未启用" in r["reply"],
               r["reply"][:30])
        # on: 三行为触发
        os.environ["VOICE50_MODE"] = "on"
        member2, sid2 = await _new_member_and_session(2)
        await svc.handle_text(sid2, "小竹，看新品")
        evs = await repo.list_events(member_id=member2)
        behaviors = {e["behavior"] for e in evs}
        record("on 文本轮: 仅清晰意图(无登录)",
               behaviors == {"voice_clear_intent"},
               str(behaviors))
        # on: 指令卡片
        r = await svc.handle_text(sid2, "小竹，我的语音积分")
        card = r.get("card") or {}
        record("on 指令卡片(池余额)",
               card.get("type") == "voice50_score"
               and "poolBalance" in card,
               str(card)[:60])
        record("on 回复含池语义",
               "激励池" in r["reply"]
               and "绝不因不用语音" in r["reply"],
               r["reply"][:40])
        os.environ["VOICE50_MODE"] = "off"


class TestEndpoints:
    """07 端点(HTTP)"""

    async def run(self):
        print("[07 端点]")
        reset_all()
        os.environ["VOICE50_MODE"] = "on"
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        h = {"X-Member-Id": "5501"}
        # my
        resp = client.get("/api/xiaozhu/voice50/my",
                          headers=h)
        body = resp.json()
        record("GET my 200",
               resp.status_code == 200
               and body.get("success") is True
               and "poolBalance" in body)
        # risk-state
        resp = client.get(
            "/api/xiaozhu/voice50/risk-state"
            "?member_id=5501", headers=admin)
        record("GET risk-state 200(admin)",
               resp.status_code == 200
               and resp.json().get("frozen") is False)
        resp = client.get(
            "/api/xiaozhu/voice50/risk-state"
            "?member_id=5501")
        record("risk-state 缺 Role 403",
               resp.status_code == 403)
        # rules
        resp = client.get(
            "/api/xiaozhu/voice50/rules", headers=admin)
        body = resp.json()
        record("GET rules 200(14 行为)",
               resp.status_code == 200
               and body.get("total") == 14)
        # PUT 热更新
        resp = client.put(
            "/api/xiaozhu/voice50/rules/voice_login",
            json={"base": 2.5}, headers=admin)
        record("PUT 规则热更新",
               resp.status_code == 200
               and resp.json()["changes"]["base"]["to"]
               == 2.5)
        resp = client.get(
            "/api/xiaozhu/voice50/rules", headers=admin)
        record("热更新留痕(recentUpdates)",
               resp.status_code == 200
               and (resp.json()
                    .get("recentUpdates") or [{}])[
                       -1].get("behavior")
               == "voice_login")
        # 非法字段拒绝
        resp = client.put(
            "/api/xiaozhu/voice50/rules/voice_login",
            json={"layer": "L2"}, headers=admin)
        record("非法字段更新拒绝 409",
               resp.status_code == 409)
        # 未注册行为
        resp = client.put(
            "/api/xiaozhu/voice50/rules/nope",
            json={"base": 1}, headers=admin)
        record("未注册行为 404", resp.status_code == 404)
        # unfreeze(未冻结 → 409)
        resp = client.post(
            "/api/xiaozhu/voice50/unfreeze",
            json={"memberId": 5501}, headers=admin)
        record("未冻结 unfreeze 409",
               resp.status_code == 409)
        # my 缺头 401
        resp = client.get("/api/xiaozhu/voice50/my")
        record("my 缺头 401", resp.status_code == 401)
        os.environ["VOICE50_MODE"] = "off"


class TestZeroImpact:
    """08 默认零影响(48号既有断言复验)"""

    async def run(self):
        print("[08 默认零影响]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        svc = XiaozhuService()
        member, sid = await _new_member_and_session(9)
        r = await svc.handle_text(sid, "小竹，查信值")
        record("既有指令照常(intent)",
               r["turn"]["intent"] == "trust.score")
        r = await svc.handle_text(sid, "小竹，你能干什么")
        record("帮助含第 17 指令(自描述)",
               "语音积分" in str(r.get("card")))
        evs = await Voice50Repository().list_events()
        record("voice50 键空间独立(无事件)",
               evs == [])


async def run_all():
    await TestRules().run()
    await TestEngineMath().run()
    await TestCap().run()
    await TestL1Realtime().run()
    await TestRefAndLedger().run()
    await TestHookAndCommand().run()
    await TestEndpoints().run()
    await TestZeroImpact().run()


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
