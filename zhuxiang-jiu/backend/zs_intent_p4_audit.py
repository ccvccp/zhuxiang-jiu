"""智搜意图判定·第四轮审计(语义边界)

重点: 否定语义 / 多意图混合 / 全角字符归一化
运行: $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; python zs_intent_p4_audit.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE = asyncio".split(" ")[0]] = "asyncio"
os.environ.pop("ZS_MODE", None)
os.environ.pop("ZS_LLM_ASSIST", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ISSUES = []


def issue(name, detail=""):
    ISSUES.append(name)
    print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def ok(name, detail=""):
    print(f"  ✓ {name}" + (f" {detail}" if detail else ""))


async def main():
    from services.zs_search_service import (
        classify_intent, extract_slots, ZsSearchService,
    )
    from services import llm_client as lm
    orig = lm.provider_client.chat
    lm.provider_client.chat = lambda *a, **k: None   # LLM 关轨纯规则

    print("== ① 否定语义 ==")
    # 场景否定: "不要送礼的" → 场景不应为送礼
    s = extract_slots("不要送礼的，自己喝的酒推荐", "product")
    if s.get("scene") == "送礼":
        issue("场景否定误提(不要送礼→仍提送礼)", f"slots={s}")
    else:
        ok("场景否定不误提", str(s))
    # 商品词否定: "不想要竹香酒，有别的吗"
    s = extract_slots("不想要竹香酒，有别的推荐吗", "product")
    if "竹香" in s.get("productWords", []):
        issue("商品词否定误提(不想要竹香→仍提竹香)", f"slots={s}")
    else:
        ok("商品词否定不误提", str(s))
    # 对照: 正向场景仍要提
    s = extract_slots("推荐送礼的竹香酒", "product")
    (ok if s.get("scene") == "送礼" else issue)(
        "正向场景对照(送礼仍提)", f"slots={s}")

    print("== ② 多意图混合(行为可预测性) ==")
    # 一句话两问: 商品+订单 —— 得分制确定性(商品词密度高者胜);
    # 混合意图仅答主意图为已知产品边界(P3 可做意图拆分), 判定
    # 本身必须行为确定
    c1 = classify_intent("竹奕酒多少钱，顺便看看我的订单发货了吗")
    c2 = classify_intent("竹奕酒多少钱，顺便看看我的订单发货了吗")
    if c1["intent"] == c2["intent"] == "product":
        ok("混合意图行为确定性(product 词密度稳定胜出)",
           f"candidates={c1['candidates']}")
    else:
        issue("混合意图行为不确定或非预期",
              f"{c1['intent']}/{c2['intent']}")

    print("== ③ 全角字符(NFKC 归一化) ==")
    svc = ZsSearchService()
    # 全角数字等级: "我是Ｌ３会员"(全角Ｌ３)
    r = await svc.query("我是Ｌ３会员有什么权益", role="guest")
    if r.get("slots", {}).get("level") == 3:
        ok("全角Ｌ３等级槽位")
    else:
        issue("全角Ｌ３槽位漏提", f"slots={r.get('slots')}")
    # 全角订单号: "订单ＲＴ１２３..."
    r = await svc.query("订单ＲＴ17580000000001发货了吗", role="guest")
    if r.get("slots", {}).get("orderNo") == "RT17580000000001":
        ok("全角ＲＴ订单号归一化")
    else:
        issue("全角ＲＴ订单号漏提", f"slots={r.get('slots')}")
    # 全角价格: "１２８元的酒"
    r = await svc.query("１２８元的竹香酒推荐", role="guest")
    if r.get("slots", {}).get("priceAround") == 128:
        ok("全角数字价格归一化")
    else:
        issue("全角价格漏提", f"slots={r.get('slots')}")

    print("== ④ 新锚点误伤回归(三轮新增词) ==")
    # "我的政策"新锚点不应把品牌政策类问题误抢
    for text, expect in [
        ("瑞麒品牌政策是什么", "brand"),      # 品牌词 2 vs 政策 1
        ("退换货政策是什么", "order"),         # 退货2+政策1=3
        ("我的积分怎么兑换", "equity"),
    ]:
        c = classify_intent(text)
        (ok if c["intent"] == expect else issue)(
            f"[{text}] 期望 {expect}", f"got={c['intent']} "
            f"cands={c['candidates']}")

    lm.provider_client.chat = orig
    print("-" * 50)
    print(f"问题数: {len(ISSUES)}")
    return 0 if not ISSUES else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
