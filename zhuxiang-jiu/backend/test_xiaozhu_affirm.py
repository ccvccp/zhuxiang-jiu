"""48号·肯定应答拦截 真机失败说法全覆盖测试"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"

PASS = 0
FAIL = 0


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


async def main():
    from services.xiaozhu_service import XiaozhuService
    svc = XiaozhuService()

    print("[01 真机失败说法重放(推荐语境→肯定应答→加购)]")
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    # 建立商品语境(推荐)——文本渠道首轮须唤醒
    r = await svc.handle_text(sid, "小竹，看新品")
    assert (r.get("card") or {}).get("type") == "product_list", \
        f"新品卡未出: {r.get('reply')}"
    prod_name = (r.get("card") or {}).get("subject")

    cases = [
        ("需要", 1),          # 96:5 / 98:6
        ("需要加两件", 2),     # 96:4
        ("要加两件", 2),
        ("买两件", 2),        # 98:3
        ("加两件", 2),
        ("来两件", 2),
        ("要", 1),
        ("加个购物车", 1),     # 96:3
        ("加入购物车", 1),
        ("需要加购", 1),
    ]
    for text, want_qty in cases:
        r = await svc.handle_text(sid, text)
        card = r.get("card") or {}
        record(f"'{text}'→加购{want_qty}件(当前款)",
               card.get("type") == "cart_added"
               and card.get("quantity") == want_qty
               and card.get("subject") == prod_name,
               f"type={card.get('type')} qty={card.get('quantity')}"
               f" sub={card.get('subject')}")
    await svc.delete_session(sid)

    print("[02 无商品语境不误加]")
    sid2 = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid2, "需要")
    t = r.get("turn") or {}
    record("无语境'需要'→引导(非 cart_added)",
           (r.get("card") or {}).get("type") != "cart_added",
           f"intent={t.get('intent')}")
    await svc.delete_session(sid2)

    print("[03 ASR 误听修正(留痕 99:3)]")
    from services.xiaozhu_service import fix_asr_mishear
    record("'加入国五车'→'加入购物车'",
           fix_asr_mishear("蒋新平加入国五车") == "蒋新平加入购物车")

    print("[04 既有剧本回归: 显式加购/否定/换一款]")
    sid3 = (await svc.open_session(1, "voice"))["sessionId"]
    await svc.handle_text(sid3, "小竹，看新品")
    r = await svc.handle_text(sid3, "来一件")
    record("显式'来一件'仍加购",
           (r.get("card") or {}).get("type") == "cart_added")
    r = await svc.handle_text(sid3, "不要这款")
    t = r.get("turn") or {}
    record("否定→换一款",
           t.get("intent") == "product.new")
    await svc.delete_session(sid3)

    print("=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
