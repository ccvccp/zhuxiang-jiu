"""50号·小竹语音信值积分引擎 P1 专项测试
(L1 四行为信号源+声纹双态+日限 enforcement)

运行方式:
    python test_xiaozhu_p50_1.py

覆盖(50号计划 §七 P1):
    - 声纹验证器: 双态(proxy/real)/绑定检查/
      文本未验证/liveness 确定性/代理摘要一致性
    - 日限 enforcement: dailyCap 满后 skip/
      penalty 豁免/次日重置语义(dayKey)
    - extra_mult 场景折扣(多次失败后成功 ×0.5)
    - record_confirm_undo(-1 扣分)
    - record_antifraud_coop: 非问询拒绝/47号
      watched tier 计分 ×1.3/回避扣分 -3
    - 钩子 E2E: 绑定检查(login 只在语音通道)/
      47号联动查询轮/事件 voiceprintMode 审计标注
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
os.environ["VOICE50_MODE"] = "off"
os.environ["VOICE50_VOICEPRINT_MODE"] = "proxy"

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
    """建档案+绑定(返回 trustId)"""
    import uuid
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    from services.xiaozhu_service import XiaozhuService
    suffix = uuid.uuid4().hex[:10]
    tid = (await TrustProfileService().create_role(
        "person", f"p501-{suffix[:6]}",
        f"110101{suffix}4321"))["trustId"]
    await XiaozhuService().bind_trust(
        member_id, tid, note="p50-1")
    return tid


class TestVoiceprint:
    """01 声纹验证器(双态)"""

    async def run(self):
        print("[01 声纹验证器]")
        reset_all()
        from services.xiaozhu_voice50_voiceprint import (
            verify as vp_verify, voiceprint_mode,
            liveness_score, speaker_proxy_digest,
            VP_MODE_PROXY, VP_MODE_REAL,
        )
        record("默认模式 proxy",
               voiceprint_mode() == VP_MODE_PROXY)
        session = {"sessionId": 1}
        # 未绑定 → 未验证
        vp = await vp_verify(6101, session, "voice")
        record("语音+未绑定 → 未验证 ×0.3",
               vp["verified"] is False
               and vp["multiplier"] == 0.3,
               str(vp["multiplier"]))
        # 绑定 → proxy verified
        await _bind(6101)
        vp = await vp_verify(6101, session, "voice")
        record("语音+绑定 → proxy verified ×1.25",
               vp["verified"] is True
               and vp["mode"] == VP_MODE_PROXY
               and vp["multiplier"] == 1.25,
               str(vp["multiplier"]))
        record("proxy 注明不作凭证",
               "不作凭证" in vp["note"])
        # 文本通道 → 未验证
        vp = await vp_verify(6101, session, "text")
        record("文本通道 → 未验证 ×0.3",
               vp["verified"] is False
               and "文本通道" in vp["note"])
        # real 态
        os.environ["VOICE50_VOICEPRINT_MODE"] = "real"
        vp = await vp_verify(6101, session, "voice")
        record("real 态 → 全额 ×1.5+活体",
               vp["verified"] is True
               and vp["mode"] == VP_MODE_REAL
               and vp["multiplier"] == 1.5
               and 0.85 <= vp["liveness"] <= 0.95,
               str(vp))
        # liveness 确定性(同会员+会话稳定)
        l1 = liveness_score(6101, 1)
        l2 = liveness_score(6101, 1)
        record("liveness 确定性", l1 == l2)
        # 代理摘要(49号同款口径)
        d1 = speaker_proxy_digest(6101)
        record("代理摘要确定性+区分",
               d1 == speaker_proxy_digest(6101)
               and d1 != speaker_proxy_digest(6102)
               and len(d1) == 32)
        os.environ["VOICE50_VOICEPRINT_MODE"] = "proxy"


class TestDailyCap:
    """02 日限 enforcement"""

    async def run(self):
        print("[02 日限 enforcement]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        # voice_login dailyCap=6——灌 6 次后 skip
        for _ in range(6):
            r = await svc.record_behavior(
                6201, "voice_login")
        record("日限内正常计分",
               r.get("skipped") is None)
        r7 = await svc.record_behavior(
            6201, "voice_login")
        record("日限满 → skip(不记事件)",
               r7.get("skipped") == "dailyCapReached"
               and r7.get("finalScore") == 0.0,
               str(r7)[:60])
        # penalty 豁免日限(扣分始终可记)
        rp = await svc.record_behavior(
            6201, "voice_login", penalty=True)
        record("扣分豁免日限",
               rp.get("skipped") is None
               and rp["finalScore"] == -5.0)
        # 不限日限行为(voice_polite None)
        for _ in range(35):
            rp2 = await svc.record_behavior(
                6201, "voice_polite", quality=None)
        record("不限日限行为(voice_polite)",
               rp2.get("skipped") is None)
        # skip 不产生事件(事件计数=6)
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        evs = await Voice50Repository().list_events(
            member_id=6201)
        logins = [e for e in evs
                  if e["behavior"] == "voice_login"
                  and float(e.get("finalScore")
                           or 0) > 0]
        record("skip 未产生正向事件",
               len(logins) == 6, str(len(logins)))


class TestExtraMult:
    """03 extra_mult 场景折扣"""

    async def run(self):
        print("[03 extra_mult 场景折扣]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        # voice_env_verify: 多次失败后成功 ×0.5
        # (P1 作用域修正: env_verify 不乘声纹系数——
        #  加成走 gains/extra_mult)
        r = await svc.record_behavior(
            6301, "voice_env_verify",
            voiceprint="proxy", extra_mult=0.5)
        record("多次失败后成功 ×0.5",
               abs(r["finalScore"] - 5 * 0.5) < 1e-6,
               str(r["finalScore"]))
        # 一次通过 ×1.5(gains firstPass)
        r2 = await svc.record_behavior(
            6302, "voice_env_verify",
            voiceprint="proxy",
            gains={"firstPass": True})
        record("一次通过 ×1.5",
               abs(r2["finalScore"] - 5 * 1.5) < 1e-6,
               str(r2["finalScore"]))


class TestConfirmUndo:
    """04 确认后撤销"""

    async def run(self):
        print("[04 确认后撤销]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        r = await svc.record_confirm_undo(6401)
        record("撤销扣分 -1",
               r["finalScore"] == -1.0
               and r["behavior"] == "voice_confirm")
        # 池不动(负向只记事件)
        v = await svc.my_view(6401)
        record("撤销不扣池", v["poolBalance"] == 0.0)


class TestAntifraudCoop:
    """05 反欺诈配合(47号联动)"""

    async def run(self):
        print("[05 反欺诈配合(47号联动)]")
        reset_all()
        from services.xiaozhu_voice50_service import (
            Voice50Service,
        )
        svc = Voice50Service()
        # 无绑定 → 拒绝(非问询场景)
        try:
            await svc.record_antifraud_coop(6501)
            record("非问询场景拒绝(无绑定)",
                   False, "未抛")
        except ValueError as e:
            record("非问询场景拒绝(无绑定)",
                   "未被风控问询" in str(e))
        # 绑定+无画像 → 拒绝
        tid = await _bind(6502)
        try:
            await svc.record_antifraud_coop(6502)
            record("非问询场景拒绝(无画像)",
                   False, "未抛")
        except ValueError:
            record("非问询场景拒绝(无画像)", True)
        # 灌 47号风险事件 4 次 → watched tier
        from services.trust_risk_profile_service import (
            TrustRiskProfileService, tier_of,
            trust_level_of,
        )
        for _ in range(4):
            await TrustRiskProfileService(
            ).record_risk_event(
                tid, source="p50-test",
                signals=["semantic_reuse"])
        profile = await TrustRiskProfileService(
        ).get_profile(tid)
        tier = tier_of(trust_level_of(
            float(profile.get("riskEMA") or 0)))
        record("47号画像已降级(watched+)",
               tier in ("watched", "restricted",
                        "flagged"), tier)
        # 计分: 一致性通过 ×1.3
        r = await svc.record_antifraud_coop(
            6502, consistency_passed=True)
        record("配合响应计分 ×1.3",
               abs(r["finalScore"] - 4 * 1.3) < 1e-6,
               str(r["finalScore"]))
        # 事件 voiceprintMode 审计标注(未验证——文本)
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        evs = await Voice50Repository().list_events(
            member_id=6502)
        coop = [e for e in evs
                if e["behavior"]
                == "voice_antifraud_coop"]
        record("coop 事件留痕(note 含 tier)",
               bool(coop)
               and "risk47" in (coop[-1].get("note")
                               or ""))
        # 回避/矛盾 → -3
        r2 = await svc.record_antifraud_coop(
            6502, consistency_passed=False)
        record("回避应答扣分 -3",
               r2["finalScore"] == -3.0)


class TestHookE2E:
    """06 钩子 E2E(绑定检查+47号联动+审计标注)"""

    async def run(self):
        print("[06 钩子 E2E]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        from repositories.voice50_repository import (
            Voice50Repository,
        )
        repo = Voice50Repository()
        svc = XiaozhuService()
        os.environ["VOICE50_MODE"] = "on"
        # 绑定会员——文本轮(不产生 voice_login)
        await _bind(6601)
        sid = (await svc.open_session(
            6601, channel="text"))["sessionId"]
        await svc.handle_text(sid, "小竹，看新品")
        evs = await repo.list_events(member_id=6601)
        record("文本轮无 voice_login(通道检查)",
               all(e["behavior"] != "voice_login"
                   for e in evs),
               str({e["behavior"] for e in evs}))
        # 语音轮(单元级直调钩子——ASR mock 不可控)
        session = await svc._require_open(sid)
        cmd = next(c for c in
                   __import__(
                       "services.xiaozhu_service",
                       fromlist=["COMMANDS"]).COMMANDS
                   if c["action"] == "product.new")
        await svc._voice50_turn_hook(
            session, "voice", "小竹，看新品",
            cmd, {"reply": "ok"})
        evs = await repo.list_events(member_id=6601)
        login = [e for e in evs
                 if e["behavior"] == "voice_login"]
        record("语音轮 voice_login(proxy verified)",
               bool(login)
               and login[-1]["voiceprintMode"]
               == "proxy",
               str(login[-1]["voiceprintMode"]
                   ) if login else "无事件")
        # 47号 flagged 会员查询轮 → coop
        tid = await _bind(6602)
        from services.trust_risk_profile_service \
            import TrustRiskProfileService
        for _ in range(4):
            await TrustRiskProfileService(
            ).record_risk_event(
                tid, source="p50-test",
                signals=["semantic_reuse"])
        cmd_score = next(
            c for c in
            __import__(
                "services.xiaozhu_service",
                fromlist=["COMMANDS"]).COMMANDS
            if c["action"] == "trust.score")
        session2 = {"sessionId": 2, "memberId": 6602}
        await svc._voice50_turn_hook(
            session2, "text", "小竹，查信值",
            cmd_score, {"reply": "ok"})
        evs = await repo.list_events(member_id=6602)
        coop = [e for e in evs
                if e["behavior"]
                == "voice_antifraud_coop"]
        record("47号问询轮触发 coop(钩子)",
               bool(coop)
               and "risk47" in (coop[-1].get("note")
                               or ""),
               str(len(coop)))
        # voice 通道未绑定 → 未验证审计标注
        session3 = {"sessionId": 3, "memberId": 6603}
        await svc._voice50_turn_hook(
            session3, "voice", "小竹，看新品",
            cmd, {"reply": "ok"})
        evs = await repo.list_events(member_id=6603)
        login3 = [e for e in evs
                  if e["behavior"] == "voice_login"]
        record("未绑定语音轮 → 未验证标注",
               bool(login3)
               and login3[-1]["voiceprintMode"] == "",
               str(login3[-1]["voiceprintMode"])
               if login3 else "无事件")
        os.environ["VOICE50_MODE"] = "off"


async def run_all():
    await TestVoiceprint().run()
    await TestDailyCap().run()
    await TestExtraMult().run()
    await TestConfirmUndo().run()
    await TestAntifraudCoop().run()
    await TestHookE2E().run()


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
