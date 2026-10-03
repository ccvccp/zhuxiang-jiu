"""智搜意图判定·第三轮审计(未覆盖面深挖)

重点: 规划验收场景回归 / 槽位完整性 / LLM 调用经济性
运行: $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; python zs_intent_p3_audit.py
"""
import asyncio
import os
import re
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("ZS_MODE", None)
os.environ.pop("ZS_LLM_ASSIST", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ISSUES = []


def issue(name, detail=""):
    ISSUES.append(name)
    print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def ok(name, detail=""):
    print(f"  ✓ {name}")


async def main():
    from services.zs_search_service import (
        classify_intent, extract_slots, ZsSearchService,
    )
    from services import llm_client as lm

    print("== ① 规划验收场景(角色×意图二维路由的意图面) ==")
    # 规划第一节验收表: "我的政策" → 已认证代理 → 个性化返回
    for text, expect in [
        ("我的政策", "agent"),          # 规划验收场景
        ("我的等级", "equity"),
        ("我的积分", "equity"),
        ("我的权益", "equity"),
        ("查我的会员等级", "equity"),
    ]:
        c = classify_intent(text)
        (ok if c["intent"] == expect else issue)(
            f"[{text}] 规则直判 {expect}",
            f"got={c['intent']} score={c.get('candidates')}")

    print("== ② 槽位完整性(规划第三节承诺 9 类) ==")
    # 规划槽位表: orderNo/waybillNo/level 等未实现?
    cases = [
        # 订单号用站内真实格式 RT+毫秒+序号(_gen_order_id 口径)
        ("订单RT17580000000001发货了吗", "order", "orderNo"),
        ("运单SF1234567890到哪了", "order", "waybillNo"),
        ("我是L3会员有什么权益", "equity", "level"),
        ("做山东的代理怎么申请", "agent", "province"),
    ]
    for text, intent, slot in cases:
        c = classify_intent(text)
        s = extract_slots(text, c["intent"])
        if c["intent"] != intent:
            issue(f"[{text}] 意图 {intent}", f"got={c['intent']}")
        elif slot not in s:
            issue(f"[{text}] 槽位 {slot} 未提取", f"slots={s}")
        else:
            ok(f"[{text}] {slot}={s[slot]}")

    print("== ③ LLM 调用经济性(无效输入不烧 LLM) ==")
    calls = {"n": 0}

    def counting_chat(*a, **k):
        calls["n"] += 1
        return '{"intent":"chat","query":"x"}'

    orig = lm.provider_client.chat
    lm.provider_client.chat = counting_chat
    svc = ZsSearchService()
    for text in ["？？？。。。", "！！", "@#￥%", "。。。"]:
        await svc.query(text, role="guest")
    if calls["n"] > 0:
        issue("纯符号查询浪费 LLM 调用", f"calls={calls['n']}")
    else:
        ok("纯符号查询零 LLM 调用")
    lm.provider_client.chat = orig

    print("== ④ 编码/格式边界 ==")
    c = classify_intent("竹奕酒多少钱")     # 正常对照
    (ok if c["intent"] == "product" else issue)("半角对照", str(c))
    c = classify_intent("  竹奕酒多少钱  ")  # 首尾空白(query 会 strip)
    (ok if c["intent"] == "product" else issue)(
        "首尾空白(主链 strip 后)", str(c["intent"]))
    c = classify_intent("hello world")
    (ok if c["intent"] == "chat" else issue)("纯英文兜底chat",
                                             str(c["intent"]))
    c = classify_intent("酒")
    (ok if c["intent"] == "chat" else issue)("单字低置信兜底chat",
                                             str(c["intent"]))

    print("== ⑤ R4 精确单查询(槽位联动价值) ==")
    # 有 orderNo 槽位时订单路是否精确查该单(而非仅列最近2单)
    s = extract_slots("订单RT17580000000001发货了吗", "order")
    if "orderNo" not in s:
        ok("(槽位未实现, 见②——联动检查随②修复后补)", "")
    else:
        ok("orderNo 槽位已实现", str(s))

    print("-" * 50)
    print(f"问题数: {len(ISSUES)}")
    return 0 if not ISSUES else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
