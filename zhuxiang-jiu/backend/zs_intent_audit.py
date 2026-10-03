"""智搜意图判定逻辑·边界用例集审查

用例覆盖: 意图冲突/子串误命中/低置信/多意图混合/槽位边界。
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.zs_search_service import (  # noqa: E402
    classify_intent, extract_slots, compliance_gate,
)

CASES = [
    # (输入, 期望意图 or None=仅观察)
    ("竹奕酒多少钱一瓶", "product"),
    ("帮我推荐送礼的酒", "product"),
    ("会员有什么优惠", "equity"),
    ("有什么优惠活动", "attract"),
    ("现在有秒杀吗", "attract"),
    ("怎么注册账号", "help"),
    ("怎么下单购买", "help"),
    ("竹香酒怎么下单", "help"),          # 冲突: product(竹香2+酒1) vs help(下单2)
    ("退货怎么办理", "order"),
    ("退款多久到账", "order"),
    ("开发票吗", "order"),
    ("竹文化有什么讲究", "brand"),
    ("徂徕山富硒水是什么", "brand"),
    ("我想加盟开店", "agent"),
    ("开网店要什么条件", "agent"),
    ("竹奕酒和竹香酒哪个好", "product"),
    ("喝酒有害健康吗", None),            # 观察: 低置信应 chat
    ("你好", "chat"),
    ("3Q", "chat"),
    ("支持货到付款吗", None),            # 观察: 支付子串?"支付"≠"支持" 应 help? 无锚点→chat
    ("订单号ZY123发货了吗", "order"),
    ("积分怎么兑换", "equity"),
    ("代理政策里保证金多少", "agent"),   # 冲突: agent(代理2+保证金2+政策1) 
    ("价格200到400的礼盒", "product"),
    ("麒麟", "brand"),
]


async def t():
    print("== 意图分类边界用例 ==")
    issues = 0
    for text, expect in CASES:
        c = classify_intent(text)
        flag = ""
        if expect and c["intent"] != expect:
            flag = f"  ✗ 期望{expect}"
            issues += 1
        elif c["intent"] == "chat" and expect is None:
            pass
        print(f"  [{c['intent']:8s}|{c['confidence']:.2f}] {text[:22]}"
              f" | 命中{c['hits'][:4]}{flag}")

    print("\n== 槽位边界 ==")
    for text in ("200到500元的酒", "大约300元", "商务宴请用酒",
                 "收藏陈酿老酒", "42度的竹奕"):
        print(f"  {text[:16]} → {extract_slots(text, 'product')}")

    print("\n== 合规边界 ==")
    for text in ("解酒吗", "能养生治百病吗", "代理稳赚吗",
                 "小孩能喝酒吗", "竹奕酒好喝吗"):
        b = compliance_gate(text)
        print(f"  {text[:14]} → {b['rule'] if b else '放行'}")

    print(f"\n问题数: {issues}")


asyncio.run(t())
