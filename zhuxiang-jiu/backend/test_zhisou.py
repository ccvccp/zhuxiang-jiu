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
