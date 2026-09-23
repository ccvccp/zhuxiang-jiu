"""48号·小竹 P1 语音购物清单(cart.add)专项测试

运行方式:
    python test_xiaozhu_voice_p1.py

覆盖(P1 语音选品——"说一句话完成所有操作"购物闭环):
    - 指令注册: COMMANDS 16 项含 cart.add
    - 指代加购: "看新品"后"把这个加入购物车"→主推商品入清单
    - 纯指代: "就它了"→最近商品卡首件
    - 关键词加购: "来一件竹香"→搜索首件(优先于上轮卡)
    - 清单累计: cartCount 递增(1→2)
    - 澄清: 新会话无目标直说"加购"→clarify=product
    - 多件结算: 清单聚合→cart.submit 2 件下单(order_done)
    - P0 兼容: 无加购轮会话"结算"→最近商品卡单件
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

PASS = 0
FAIL = 0


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  \u2713 {name}")
    else:
        FAIL += 1
        print(f"  \u2717 {name} \u2014 {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def main():
    reset_all()
    from services.xiaozhu_service import (
        COMMANDS, XiaozhuService,
    )

    print("[01 指令注册]")
    actions = [c["action"] for c in COMMANDS]
    record("COMMANDS 25 项含 cart.add/order.query/order.pay"
           "+酒问话四问",
           len(COMMANDS) == 25
           and "cart.add" in actions
           and "order.query" in actions
           and "order.pay" in actions
           and all(a in actions for a in (
               "wine.verify", "wine.craft",
               "wine.recommend", "wine.reviews")),
           f"{len(COMMANDS)}|{actions[-3:]}")

    svc = XiaozhuService()

    async def _open(member_id: int = 1) -> int:
        return (await svc.open_session(
            member_id))["sessionId"]

    print("[02 指代加购(看新品→把这个加入购物车)]")
    sid = await _open(1)
    r = await svc.handle_text(sid, "小竹，看新品")
    first_subject = (r.get("card") or {}).get("subject")
    record("看新品卡片就绪", bool(first_subject),
           str(first_subject))
    r = await svc.handle_text(sid, "小竹，把这个加入购物车")
    card = r.get("card") or {}
    record("指代加购→cart_added 卡",
           r.get("success") is True
           and (r.get("turn") or {}).get("intent")
           == "cart.add"
           and card.get("type") == "cart_added"
           and card.get("cartCount") == 1
           and card.get("subject") == first_subject,
           f"{str(card)[:90]}")
    record("加购 executed 留痕",
           r.get("executed") is True,
           str(r.get("executed")))

    print("[03 纯指代(就它了→最近商品卡)]")
    sid2 = await _open(1)
    await svc.handle_text(sid2, "小竹，看新品")
    r = await svc.handle_text(sid2, "小竹，就它了")
    card = r.get("card") or {}
    record("纯指代加购成功",
           (r.get("turn") or {}).get("intent") == "cart.add"
           and card.get("type") == "cart_added"
           and card.get("cartCount") == 1,
           f"{str(card)[:80]}")

    print("[04 关键词加购优先于上轮卡]")
    sid3 = await _open(1)
    await svc.handle_text(sid3, "小竹，看新品")
    r = await svc.handle_text(sid3, "小竹，来一件竹香")
    card = r.get("card") or {}
    record("关键词搜索首件入清单",
           (r.get("turn") or {}).get("intent")
           == "cart.add"
           and card.get("type") == "cart_added"
           and "竹" in str(card.get("subject") or ""),
           f"{str(card)[:80]}")

    print("[05 清单累计+多件结算]")
    sid4 = await _open(1)
    await svc.handle_text(sid4, "小竹，看新品")
    r1 = await svc.handle_text(
        sid4, "小竹，把这个加入购物车")
    r2 = await svc.handle_text(
        sid4, "小竹，来一件竹香")
    record("cartCount 递增 1→2",
           (r1.get("card") or {}).get("cartCount") == 1
           and (r2.get("card") or {}).get("cartCount")
           == 2,
           f"{(r1.get('card') or {}).get('cartCount')}"
           f"→{(r2.get('card') or {}).get('cartCount')}")
    # 结算: 聚合 2 件 → 高敏确认流(cart.submit 升级 SENSITIVE)
    r = await svc.handle_text(sid4, "小竹，结算")
    turn = r.get("turn") or {}
    card = r.get("card") or {}
    record("多件结算→高敏确认流",
           turn.get("intent") == "cart.submit"
           and r.get("confirmRequired") is True
           and card.get("type") == "confirm"
           and "件" in str(card.get("subject") or ""),
           f"{turn.get('intent')}|"
           f"{str(card.get('subject'))[:60]}")
    # 核销 4 位码 → 订单真实创建(后续查订单用例依赖)
    from services.xiaozhu_executor import get_executor
    tok = r.get("confirmToken")
    real_code = get_executor()._tokens[tok]["code"]
    r2 = await svc.confirm_action(tok, real_code)
    record("确认核销→订单创建+金额回填",
           (r2.get("executed") is True
            or "订单已提交" in str(r2.get("reply")))
           and "金额 -" not in str(r2.get("reply")),
           str(r2.get("reply"))[:60])
    await svc.delete_session(sid4)

    print("[06 澄清(无目标直说加购)]")
    sid5 = await _open(1)
    r = await svc.handle_text(sid5, "小竹，加购")
    record("无目标→clarify=product",
           r.get("clarify") == "product"
           and "想加购" in str(r.get("reply")),
           f"{r.get('clarify')}|{str(r.get('reply'))[:50]}")
    await svc.delete_session(sid5)

    print("[07 P0 兼容(无加购轮结算单件)]")
    sid6 = await _open(1)
    await svc.handle_text(sid6, "小竹，看新品")
    r = await svc.handle_text(sid6, "小竹，结算这个")
    turn = r.get("turn") or {}
    record("回退最近商品卡单件结算",
           turn.get("intent") == "cart.submit"
           and ((r.get("card") or {}).get("type")
                == "order_done"
                or "订单" in str(r.get("reply"))),
           f"{turn.get('intent')}|{str(r.get('reply'))[:60]}")
    await svc.delete_session(sid6)

    print("[08 订单查询(P2 order.query)]")
    # 25 项指令
    record("COMMANDS 25 项含 order.query",
           len(COMMANDS) == 25
           and "order.query" in [c["action"] for c in COMMANDS],
           str(len(COMMANDS)))
    # 下单后查单: 成单→最近订单卡
    sid7 = await _open(1)
    await svc.handle_text(sid7, "小竹，看新品")
    await svc.handle_text(sid7, "小竹，把这个加入购物车")
    # 结算升高敏: 发确认令牌后核销 4 位码成单
    r0 = await svc.handle_text(sid7, "小竹，结算")
    from services.xiaozhu_executor import get_executor as _ge
    _tok = r0.get("confirmToken")
    if _tok:
        await svc.confirm_action(
            _tok, _ge()._tokens[_tok]["code"])
    r = await svc.handle_text(sid7, "小竹，查我的订单")
    turn = r.get("turn") or {}
    card = r.get("card") or {}
    record("查订单→order_list 卡(最近单含物流栏)",
           turn.get("intent") == "order.query"
           and card.get("type") == "order_list"
           and len(card.get("items") or []) >= 1
           and "waybill" in (card.get("items") or [{}])[0]
           and r.get("jump") == "/#/pages/orders/index",
           f"{turn.get('intent')}|{str(card)[:80]}")
    # 物流说法同义
    r = await svc.handle_text(sid7, "小竹，我的订单到哪了")
    record("物流说法同义命中",
           (r.get("turn") or {}).get("intent")
           == "order.query",
           str((r.get("turn") or {}).get("intent")))
    await svc.delete_session(sid7)
    # 游客引导
    sid8 = await _open(0)
    r = await svc.handle_text(sid8, "小竹，查我的订单")
    record("游客查单→登录引导",
           "登录" in str(r.get("reply")),
           str(r.get("reply"))[:50])
    await svc.delete_session(sid8)

    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
