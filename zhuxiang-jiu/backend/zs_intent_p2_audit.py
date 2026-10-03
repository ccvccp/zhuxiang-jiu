"""智搜意图判定·P2 边界审计(LLM 兜底链路)

重点: 规则→LLM 判定的交接面 + 进化闭环的防滥用面
运行: $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; python zs_intent_p2_audit.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("ZS_MODE", None)
os.environ.pop("ZS_LLM_ASSIST", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repositories.store import reset_store as _reset_store  # noqa: E402

ISSUES = []


def issue(name, detail=""):
    ISSUES.append(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
    print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def ok(name, detail=""):
    print(f"  ✓ {name}")


async def main():
    from services import llm_client as lm
    from services.zs_search_service import ZsSearchService
    svc = ZsSearchService()
    _orig = lm.provider_client.chat

    calls = {"n": 0}

    def mk_chat(reply):
        def _chat(system, user, temperature=0.3, model=""):
            calls["n"] += 1
            return reply
        return _chat

    print("== ① LLM 返回值边界 ==")
    # 1. LLM 判 chat(闲聊) → 应回 None 走规则轨(不采纳"闲聊"兜底)
    lm.provider_client.chat = mk_chat('{"intent":"chat","query":"闲聊"}')
    r = await svc.query("帮我挑个送领导的口粮酒", role="guest")
    (ok if r["intent"] == "chat" else issue)(
        "LLM判chat回规则轨", f"intent={r['intent']}")
    d = next(x for x in await svc.decisions(limit=200)
             if x.get("query") == "帮我挑个送领导的口粮酒")
    if d.get("llmAssist") is None:
        ok("LLM判chat不留痕(视同失败)")
    else:
        issue("LLM判chat仍留痕", str(d.get("llmAssist"))[:80])

    # 2. LLM 非法意图 → None
    lm.provider_client.chat = mk_chat('{"intent":"weather","query":"x"}')
    r = await svc.query("今晚吃什么好呢随便聊聊", role="guest")
    (ok if r["intent"] == "chat" else issue)(
        "LLM非法意图回规则轨", f"intent={r['intent']}")

    # 3. LLM 非 JSON 纯文本 → None
    lm.provider_client.chat = mk_chat("我觉得您想买酒")
    r = await svc.query("随便说点什么测试", role="guest")
    (ok if r["intent"] == "chat" else issue)(
        "LLM非JSON回规则轨", f"intent={r['intent']}")

    # 4. LLM 空 query → 回退原文检索
    # (查询句需规则零锚点才走 LLM——"品牌"是 2 分锚点会短路)
    lm.provider_client.chat = mk_chat('{"intent":"brand","query":""}')
    r = await svc.query("说点文化方面的讲究", role="guest")
    d2 = next(x for x in await svc.decisions(limit=200)
              if x.get("query") == "说点文化方面的讲究")
    la = d2.get("llmAssist") or {}
    if la.get("query") == "说点文化方面的讲究":
        ok("LLM空query回退原文")
    else:
        issue("LLM空query未回退", str(la))

    print("== ② 合规前置 vs LLM 触发顺序 ==")
    # 5. 合规文本必须先拦, LLM 零调用
    calls["n"] = 0
    lm.provider_client.chat = mk_chat('{"intent":"product","query":"x"}')
    r = await svc.query("小孩能喝酒吗", role="guest")
    if r["intent"] == "blocked" and calls["n"] == 0:
        ok("合规拦截优先于LLM(零调用)")
    else:
        issue("合规文本触发LLM", f"intent={r['intent']} calls={calls['n']}")

    print("== ③ LLM 触发范围 ==")
    # 6. 规则高分意图不触发 LLM
    calls["n"] = 0
    await svc.query("竹奕酒多少钱一瓶", role="guest")
    if calls["n"] == 0:
        ok("规则高置信零LLM调用")
    else:
        issue("规则高置信仍调LLM", f"calls={calls['n']}")

    # 7. LLM 采纳后 intentCandidates 是否保留规则候选(审计完整性)
    lm.provider_client.chat = mk_chat(
        '{"intent":"product","query":"送礼 竹香酒"}')
    r = await svc.query("帮我挑个送领导的口粮酒", role="guest")
    d3 = [x for x in await svc.decisions(limit=200)
          if x.get("query") == "帮我挑个送领导的口粮酒"
          and x.get("llmAssist")]
    if d3 and d3[-1].get("intentCandidates") == []:
        issue("LLM采纳后规则候选丢失(审计断档)",
              "intentCandidates=[] 看不到规则层原判定")
    else:
        ok("LLM采纳后仍保留规则候选")

    # 8. LLM 兜底意图 order(未登录) → 结构化路登录引导
    lm.provider_client.chat = mk_chat(
        '{"intent":"order","query":"我的订单到哪了"}')
    r = await svc.query("我买的东西到哪了呀", member_id=0, role="guest")
    if r["intent"] == "order" and "登录" in r.get("answer", ""):
        ok("LLM兜底order意图结构化路正常")
    else:
        issue("LLM兜底order意图异常",
              f"intent={r['intent']} answer={r.get('answer', '')[:40]}")

    print("== ④ 进化闭环防滥用 ==")
    # 9. 同一 decision 重复反馈可无限刷 routeBoost?
    lm.provider_client.chat = _orig
    r = await svc.query("竹香酒怎么样", role="guest")
    did = r["decisionId"]
    before = (await svc.evolution_params()).get("product", 1.0)
    for _ in range(5):
        await svc.submit_feedback(did, "useful")
    after5 = (await svc.evolution_params()).get("product", 1.0)
    if after5 - before > 0.05 + 1e-9:
        issue("同决策重复反馈可刷分",
              f"{before}→{after5}(5次+0.25, 无防重)")
    else:
        ok("同决策反馈有防重")

    # 10. intentWeight 精确边界 0.6
    await svc.store.set_param("intent_weight", "value", 0.6)
    lm.provider_client.chat = mk_chat(
        '{"intent":"product","query":"送礼 酒"}')
    r = await svc.query("帮我挑个送领导的口粮酒", role="guest")
    if r["intent"] == "product":
        ok("intentWeight=0.6 精确边界采纳")
    else:
        issue("intentWeight=0.6 边界未采纳", str(r.get("intent")))
    await svc.store.set_param("intent_weight", "value", 0.599)
    r = await svc.query("帮我挑个送领导的口粮酒", role="guest")
    if r["intent"] == "chat":
        ok("intentWeight<0.6 不采纳(留痕审计)")
    else:
        issue("intentWeight<0.6 仍采纳", str(r.get("intent")))
    await svc.store.set_param("intent_weight", "value", 0.6)

    print("== ⑤ LLM 超时/异常 fail-soft ==")
    # 11. LLM 抛异常 → 不影响主链
    def boom(*a, **k):
        raise RuntimeError("llm down")
    lm.provider_client.chat = boom
    try:
        r = await svc.query("随便聊点什么天气", role="guest")
        (ok if r["intent"] == "chat" else issue)(
            "LLM异常fail-soft", str(r.get("intent")))
    except Exception as e:
        issue("LLM异常穿透主链", str(e))
    lm.provider_client.chat = _orig

    print("-" * 50)
    print(f"问题数: {len(ISSUES)}")
    return 0 if not ISSUES else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
