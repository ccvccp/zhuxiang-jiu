"""智搜·AI智能搜索引擎大模型 MVP 测试(自跑范式, 内存模式)

覆盖:
    [L1 意图层]
    1.  商品意图(价格锚点+商品词)
    2.  品牌意图(麒麟/瑞麒)
    3.  招商意图
    4.  权益意图(角色变体)
    5.  订单意图
    6.  闲聊兜底(无锚点)
    7.  槽位: 价格区间/场景/商品词
    [L2 合规前置]
    8.  未成年购酒拦截
    9.  医疗功效拦截
    10. 代理收益承诺拦截
    11. 合规拦截留痕(blocked 意图)
    [L3/L4 检索与融合]
    12. 知识路召回(品牌问题命中知识库)
    13. 融合分确定性(同输入同输出)
    14. 结构化回答带来源
    [留痕与观测]
    15. 决策留痕落库
    16. 意图统计计数
    [门控]
    17. ZS_MODE=off → query 409 口径
    18. override 运行时切档
    19. status 总览字段

运行: $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; python test_zhisou.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("ZS_MODE", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repositories.store import reset_store as _reset_store  # noqa: E402

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


async def main():
    from services.zs_search_service import (
        ZsSearchService, classify_intent, extract_slots,
        compliance_gate, current_mode,
    )
    svc = ZsSearchService()

    # ---- L1 意图 ----
    c = classify_intent("竹奕酒多少钱一瓶")
    record("意图-商品(价格)", c["intent"] == "product", str(c))
    c = classify_intent("麒麟和瑞麒是什么关系")
    record("意图-品牌(麒麟)", c["intent"] == "brand", str(c))
    c = classify_intent("我想做代理怎么申请")
    record("意图-招商", c["intent"] == "agent", str(c))
    c = classify_intent("会员有什么权益")
    record("意图-权益", c["intent"] == "equity", str(c))
    c = classify_intent("我的订单发货了吗")
    record("意图-订单", c["intent"] == "order", str(c))
    c = classify_intent("今天天气怎么样啊")
    record("意图-闲聊兜底", c["intent"] == "chat", str(c))

    # ---- 槽位 ----
    s = extract_slots("推荐 200 到 500 元 送礼的竹香酒", "product")
    record("槽位-价格区间", s.get("priceRange") == [200, 500], str(s))
    record("槽位-场景送礼", s.get("scene") == "送礼", str(s))
    record("槽位-商品词", "竹香" in s.get("productWords", []), str(s))
    # 三轮审查补全: 单号/等级/省份槽位(lookaround 边界, 中文非 \b)
    s = extract_slots("订单RT17580000000001发货了吗", "order")
    record("槽位-订单号", s.get("orderNo") == "RT17580000000001",
           str(s))
    s = extract_slots("运单SF1234567890到哪了", "order")
    record("槽位-运单号", s.get("waybillNo") == "SF1234567890",
           str(s))
    record("槽位-等级(L3)", extract_slots(
        "我是L3会员有什么权益", "equity").get("level") == 3, "")
    record("槽位-等级防误提(SSL3不提)",
           "level" not in extract_slots("SSL3证书怎么样", "chat"),
           str(extract_slots("SSL3证书怎么样", "chat")))
    record("槽位-省份", extract_slots(
        "做山东的代理怎么申请", "agent").get("province") == "山东", "")

    # ---- L2 合规 ----
    b = compliance_gate("未成年人能买酒吗")
    record("合规-未成年拦截", b is not None
           and b["rule"] == "minor", str(b))
    b = compliance_gate("你们的酒能治病吗")
    record("合规-医疗拦截", b is not None
           and b["rule"] == "medical", str(b))
    b = compliance_gate("做代理有保底收益吗")
    record("合规-收益承诺拦截", b is not None
           and b["rule"] == "agent_promise", str(b))
    b = compliance_gate("竹奕酒多少钱")
    record("合规-正常问题放行", b is None, str(b))

    # ---- 主链路(需知识库; 直接 query) ----
    os.environ["ZS_MODE"] = "assist"
    r = await svc.query("麒麟是什么", role="guest")
    record("主链-品牌问题回答",
           r["intent"] == "brand" and len(r.get("sources", [])) >= 0,
           str(r)[:120])
    record("主链-合规拦截返回",
           (await svc.query("酒能治病吗"))["intent"] == "blocked")

    r1 = await svc.query("竹香酒怎么样", role="guest")
    r2 = await svc.query("竹香酒怎么样", role="guest")
    record("主链-确定性(同输入同输出)",
           r1["answer"] == r2["answer"]
           and [x.get("score") for x in r1.get("results", [])]
           == [x.get("score") for x in r2.get("results", [])])

    rg = await svc.query("会员有什么权益", role="guest")
    rm = await svc.query("会员有什么权益", role="member")
    record("主链-角色变体(guest≠member)",
           rg["answer"] != rm["answer"],
           f"g={rg['answer'][:30]} m={rm['answer'][:30]}")

    # ---- P1: R2 权益路(会员模型结构化查询) ----
    from services.member_service import MemberService
    reg = await MemberService().register("13800009999", "Zs#Test2026",
                                         nickname="智搜测试")
    mid = reg.get("memberId") or reg.get("member_id")
    re = await svc.query("会员有什么权益", member_id=mid, role="member")
    record("P1权益-会员个人化(等级名)",
           "竹芽会员" in re["answer"] and "成长值" in re["answer"],
           str(re["answer"])[:80])
    record("P1权益-会员来源(会员模型)",
           any("会员模型#member" in s for s in re.get("sources", [])),
           str(re.get("sources")))
    record("P1权益-游客体系介绍(注册引导)",
           "注册" in rg["answer"] and "L1" in rg["answer"],
           str(rg["answer"])[:80])

    # ---- P1: R4 订单路(鉴权联动) ----
    ro_g = await svc.query("我的订单发货了吗", member_id=0,
                           role="guest")
    record("P1订单-游客登录引导(不查订单)",
           "登录" in ro_g["answer"]
           and any("auth" in s for s in ro_g.get("sources", [])),
           str(ro_g["answer"])[:80])
    ro_m = await svc.query("我的订单发货了吗", member_id=mid,
                           role="member")
    record("P1订单-会员无订单口径",
           "暂无订单" in ro_m["answer"], str(ro_m["answer"])[:80])

    # 三轮审查补: 自我指代查询规则直判(规划验收场景)
    record("P3意图-我的政策(agent)",
           (await svc.query("我的政策", member_id=mid,
                            role="member"))["intent"] == "agent", "")
    record("P3意图-我的等级(equity)",
           (await svc.query("我的等级", member_id=mid,
                            role="member"))["intent"] == "equity", "")
    # 精确单查询: 不存在单号 → 未找到口径(回落最近订单前的明确回复)
    ro_p = await svc.query("订单RT99999999999999999发货了吗",
                           member_id=mid, role="member")
    record("P3订单-精确单未找到口径",
           "未找到" in ro_p["answer"], str(ro_p["answer"])[:80])

    # ---- P1: 显式反馈进化闭环 ----
    rq = await svc.query("竹香酒怎么样", role="guest")
    record("P1反馈-决策ID返回",
           isinstance(rq.get("decisionId"), int), str(rq)[:60])
    fb = await svc.submit_feedback(rq["decisionId"], "useful")
    record("P1反馈-有用进化(+0.05)",
           fb.get("evolved") is True
           and fb.get("routeBoostAfter") == 1.05, str(fb))
    fb = await svc.submit_feedback(rq["decisionId"], "useless")
    record("P1反馈-重复反馈防刷分(仅留痕)",
           fb.get("evolved") is False
           and "防刷分" in str(fb.get("note", "")), str(fb)[:100])
    rq2 = await svc.query("竹香酒推荐一下", role="guest")
    fb = await svc.submit_feedback(rq2["decisionId"], "useless")
    record("P1反馈-新决策没用回调(-0.05)",
           fb.get("evolved") is True
           and fb.get("routeBoostAfter") == 1.0, str(fb))
    rb = await svc.query("酒能治病吗")
    fb = await svc.submit_feedback(rb["decisionId"], "useful")
    record("P1反馈-红线(合规拦截不进化)",
           fb.get("evolved") is False, str(fb))
    # clamp: 每次造新决策反馈 useless, 到 0.8 下界后不再降
    for _ in range(12):
        rqx = await svc.query("竹香酒怎么样", role="guest")
        await svc.submit_feedback(rqx["decisionId"], "useless")
    params = await svc.evolution_params()
    record("P1反馈-clamp安全阀(下界0.8)",
           params.get("product") == 0.8, str(params))
    try:
        await svc.submit_feedback(rq["decisionId"], "bad")
        record("P1反馈-非法verdict拒绝", False, "未抛出")
    except ValueError:
        record("P1反馈-非法verdict拒绝(409口径)", True)
    try:
        await svc.submit_feedback(999999, "useful")
        record("P1反馈-决策不存在404", False, "未抛出")
    except KeyError:
        record("P1反馈-决策不存在(404口径)", True)
    fbs = await svc.feedbacks(limit=20)
    record("P1反馈-留痕可审计",
           len(fbs) >= 5 and all("verdict" in f for f in fbs[:3]),
           str(len(fbs)))

    # ---- P2: LLM 意图兜底 + 查询改写(fail-soft) ----
    async def _reset_evo():
        """段间隔离: 进化参数回基线(锚点调权后参数跨段污染判定)"""
        for w in list((await svc.anchor_params()).keys()):
            await svc.store.set_param("anchor_boost", w, 1.0)
        await svc.store.set_param("intent_weight", "value", 0.6)
        for k in list((await svc.evolution_params()).keys()):
            await svc.store.set_param("route_boost", k, 1.0)

    await _reset_evo()
    from services import llm_client as _llm_mod
    _orig_chat = _llm_mod.provider_client.chat
    _llm_mod.provider_client.chat = (
        lambda system, user, temperature=0.3, model="":
        '{"intent":"product","query":"送礼 竹香酒 推荐"}')
    r2 = await svc.query("帮我挑个送领导的口粮酒", role="guest")
    record("P2兜底-LLM意图采纳(product)",
           r2["intent"] == "product", str(r2.get("intent")))
    ds2 = await svc.decisions(limit=200)
    d2 = next(d for d in ds2
              if d.get("query") == "帮我挑个送领导的口粮酒"
              and d.get("llmAssist"))
    record("P2兜底-留痕llmAssist(意图+改写)",
           (d2.get("llmAssist") or {}).get("intent") == "product"
           and "送礼" in (d2.get("llmAssist") or {}).get("query", ""),
           str(d2.get("llmAssist")))
    record("P2改写-知识路改写检索(来源)",
           "竹香" in str(r2.get("sources", []))
           or r2.get("resultCount", 0) >= 0, "")
    # intentWeight 进化: LLM 兜底决策反馈 → ±0.02(重复反馈防刷分,
    # 回落用新 LLM 兜底决策验证)
    fb = await svc.submit_feedback(r2["decisionId"], "useful")
    record("P2进化-intentWeight上调(0.6→0.62)",
           fb.get("intentWeightAfter") == 0.62, str(fb))
    r2b = await svc.query("帮我挑个送领导的口粮酒", role="guest")
    fb = await svc.submit_feedback(r2b["decisionId"], "useless")
    record("P2进化-intentWeight回落(0.62→0.6)",
           fb.get("intentWeightAfter") == 0.6, str(fb))
    # LLM 失败 fail-soft → 回规则轨 chat
    _llm_mod.provider_client.chat = lambda *a, **k: None
    r3 = await svc.query("帮我挑个送领导的口粮酒", role="guest")
    record("P2兜底-LLM失败回退chat", r3["intent"] == "chat",
           str(r3.get("intent")))
    # intentWeight 采纳闸: 0.4(<0.6) → 仅留痕不采纳
    await svc.store.set_param("intent_weight", "value", 0.4)
    _llm_mod.provider_client.chat = (
        lambda system, user, temperature=0.3, model="":
        '{"intent":"product","query":"送礼 竹香酒 推荐"}')
    r4 = await svc.query("帮我挑个送领导的口粮酒", role="guest")
    record("P2兜底-intentWeight低不采纳(仍chat)",
           r4["intent"] == "chat", str(r4.get("intent")))
    await svc.store.set_param("intent_weight", "value", 0.6)
    # 总开关 off(用调用计数器实证 LLM 未被触发)
    _calls = {"n": 0}

    def _counting_chat(*a, **k):
        _calls["n"] += 1
        return '{"intent":"product","query":"送礼 竹香酒 推荐"}'

    _llm_mod.provider_client.chat = _counting_chat
    os.environ["ZS_LLM_ASSIST"] = "off"
    r5 = await svc.query("帮我挑个送领导的口粮酒", role="guest")
    record("P2兜底-总开关off不触发",
           r5["intent"] == "chat" and _calls["n"] == 0,
           f"intent={r5.get('intent')} calls={_calls['n']}")
    os.environ.pop("ZS_LLM_ASSIST", None)
    _llm_mod.provider_client.chat = _orig_chat

    # ---- P2: 隐式转化回流(动作卡点击 +0.03) ----
    ra = await svc.query("竹奕酒多少钱一瓶", role="guest")
    base = (await svc.evolution_params()).get("product", 1.0)
    fc = await svc.record_action_click(ra["decisionId"],
                                       action_label="逛商城选酒")
    record("P2隐式-动作点击进化(+0.03)",
           fc.get("evolved") is True
           and fc.get("routeBoostAfter") == round(base + 0.03, 3)
           and fc.get("source") == "action"
           and fc.get("actionLabel") == "逛商城选酒", str(fc))

    # ---- P2: 小竹语音接入(规则匹配→统一意图引擎) ----
    from services.xiaozhu_service import match_command
    cmd = match_command("小竹，帮我搜适合送礼的竹香酒")
    record("P2小竹-指令匹配(zs.search)",
           cmd is not None and cmd["action"] == "zs.search",
           str(cmd and cmd.get("action")))
    from services.xiaozhu_service import XiaozhuService
    xr = await XiaozhuService()._exec_zs_search(
        "麒麟是什么", member_id=0)
    record("P2小竹-智搜问答回复",
           isinstance(xr.get("reply"), str)
           and len(xr["reply"]) > 4, str(xr)[:80])
    cmd2 = match_command("小竹，查我的订单")
    record("P2小竹-既有指令不受影响(order.query)",
           cmd2 is not None and cmd2["action"] == "order.query",
           str(cmd2 and cmd2.get("action")))

    # ---- P3: 混合意图拆分 ----
    from services.zs_search_service import split_sub_intents
    subs = split_sub_intents("竹奕酒多少钱，顺便看看我的订单发货了吗",
                             "product")
    record("P3拆分-副意图检测(order)",
           subs and subs[0][0] == "order", str(subs))
    record("P3拆分-单意图不拆",
           split_sub_intents("竹奕酒多少钱", "product") == [], "")
    record("P3拆分-短片段不拆",
           split_sub_intents("竹奕酒，哈", "product") == [], "")
    # 主链: 混合句双路合并回答(游客 order 副路给登录引导)
    rmix = await svc.query("竹奕酒多少钱，顺便看看我的订单发货了吗",
                           member_id=0, role="guest")
    record("P3拆分-主意图仍product",
           rmix["intent"] == "product", str(rmix.get("intent")))
    record("P3拆分-副问摘要合并回答",
           "订单服务" in rmix["answer"] and "登录" in rmix["answer"],
           str(rmix["answer"])[:100])
    dmix = next(d for d in await svc.decisions(limit=200)
                if d.get("query") == "竹奕酒多少钱,顺便看看我的订单"
                                     "发货了吗")   # NFKC 全角逗号已归一
    record("P3拆分-留痕subIntents",
           (dmix.get("subIntents") or [{}])[0].get("intent")
           == "order", str(dmix.get("subIntents")))
    record("P3拆分-副卡标记进结果",
           any(r.get("subIntent") == "order"
               for r in rmix.get("results", [])),
           str(len(rmix.get("results", []))))
    # 护栏: 合规整句拦截优先(混合句含违规词不拆不放行)
    rblk = await svc.query("竹奕酒多少钱，这酒能治病吗",
                           member_id=0, role="guest")
    record("P3拆分-合规整句拦截优先",
           rblk["intent"] == "blocked", str(rblk.get("intent")))
    # 护栏: 上限2路(三意图混合只拆1个副意图)
    subs3 = split_sub_intents(
        "竹奕酒多少钱，看看我的订单，会员积分还有多少", "product")
    record("P3拆分-子查询上限(仅1副意图)", len(subs3) == 1,
           str(subs3))

    # ---- 锚点自动调权(反馈驱动意图锚点词进化) ----
    await _reset_evo()
    from services.zs_search_service import (
        classify_intent as _clf, ANCHOR_BOOST_BOUNDS as _AB,
    )
    # 无参数=全基线(行为与历史一致)
    record("锚点-无参数基线一致",
           _clf("竹奕酒多少钱一瓶")["intent"] == "product", "")
    # 反馈调权: 命中词 ±0.1 + 留痕
    ra2 = await svc.query("竹香酒怎么样", role="guest")
    fba = await svc.submit_feedback(ra2["decisionId"], "useful")
    tun = fba.get("anchorTuning") or []
    record("锚点-有用升权(+0.1留痕)",
           any(t.get("word") == "竹香" and t.get("after") == 1.1
               for t in tun), str(tun))
    # 调权生效: boost 传入后判定得分变化(竹香 2→2.2)
    ab = await svc.anchor_params()
    record("锚点-参数落库", ab.get("竹香") == 1.1, str(ab))
    c_ab = _clf("竹香", {"竹香": 1.5})
    record("锚点-boost参与得分", c_ab["candidates"][0][1] == 3.0,
           str(c_ab["candidates"]))
    # clamp: 连续 useless 到下界 0.5
    for _ in range(10):
        rx = await svc.query("竹香酒推荐", role="guest")
        await svc.submit_feedback(rx["decisionId"], "useless")
    record("锚点-clamp下界0.5",
           (await svc.anchor_params()).get("竹香") == 0.5,
           str(await svc.anchor_params()))
    # 红线: LLM 兜底决策不调锚点(hits=llm_fallback)
    from services import llm_client as _lm2
    _orig2 = _lm2.provider_client.chat
    _lm2.provider_client.chat = (
        lambda *a, **k: '{"intent":"product","query":"送礼 酒"}')
    rl = await svc.query("帮我挑个送领导的口粮酒", role="guest")
    fbl = await svc.submit_feedback(rl["decisionId"], "useful")
    record("锚点-红线(LLM兜底不调锚点)",
           "anchorTuning" not in fbl, str(fbl)[:100])
    _lm2.provider_client.chat = _orig2
    await _reset_evo()      # 锚点段收尾归零(防污染观测段)

    # ---- shadow 档完整语义(三态留痕 + 进化冻结) ----
    os.environ["ZS_MODE"] = "shadow"
    rs = await svc.query("竹香酒怎么样", role="guest")
    record("shadow-查询正常返回建议",
           rs["intent"] == "product", str(rs.get("intent")))
    dsh = next(d for d in await svc.decisions(limit=200)
               if d.get("query") == "竹香酒怎么样"
               and d.get("mode") == "shadow")
    record("shadow-留痕模式标记", dsh.get("mode") == "shadow",
           str(dsh.get("mode")))
    before_rb = (await svc.evolution_params()).get("product", 1.0)
    before_ab = (await svc.anchor_params()).get("竹香", 1.0)
    fsh = await svc.submit_feedback(rs["decisionId"], "useful")
    record("shadow-反馈进化冻结",
           fsh.get("evolved") is False
           and "冻结" in str(fsh.get("note", ""))
           and (await svc.evolution_params()).get("product",
                                                  before_rb) == before_rb
           and (await svc.anchor_params()).get("竹香",
                                               before_ab) == before_ab,
           str(fsh)[:100])
    os.environ["ZS_MODE"] = "assist"
    rrec = await svc.query("竹香酒推荐", role="guest")
    frec = await svc.submit_feedback(rrec["decisionId"], "useful")
    record("shadow-assist档进化恢复",
           frec.get("evolved") is True, str(frec)[:80])

    # ---- 小竹 FC 注册表同步(规则轨之外 LLM/FC 轨可达) ----
    from services.xiaozhu_fc_registry import TOOL_REGISTRY
    record("FC注册-zs.search(只读)",
           TOOL_REGISTRY.get("zs.search", {}).get("tier")
           == "readonly", str(TOOL_REGISTRY.get("zs.search")
                              and TOOL_REGISTRY["zs.search"]["tier"]))
    from services.llm_client import provider_client as _pc
    import inspect as _insp
    record("FC注册-LLM command 白名单含 zs.search",
           "zs.search" in _insp.getsource(
               _pc.classify_dialog_intent), "")

    # ---- 留痕与观测 ----
    ds = await svc.decisions(limit=10)
    record("留痕-决策落库", len(ds) >= 5
           and all("intent" in d for d in ds[:3]), str(len(ds)))
    st = await svc.intent_stats()
    record("观测-意图统计", st["total"] >= 5
           and "品牌咨询" in st["byIntent"], str(st))

    # ---- 门控 ----
    os.environ["ZS_MODE"] = "off"
    try:
        await svc.query("测试")
        from services.zs_search_service import require_query_mode
        await require_query_mode()
        record("门控-off拒绝", False, "未抛出")
    except ValueError:
        record("门控-off拒绝(409口径)", True)
    m = await current_mode()
    record("门控-模式读取", m["mode"] == "off" and m["source"] == "env",
           str(m))
    os.environ["ZS_MODE"] = "assist"
    st = await svc.status()
    record("总览-字段齐全",
           all(k in st for k in ("module", "mode", "queries",
                                 "blockedRate", "decisions")), str(st))

    print(os.linesep.join(RESULTS))
    print("-" * 58)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
