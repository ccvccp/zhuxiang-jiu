# -*- coding: utf-8 -*-
"""满减最优测算 coupon.best_combo 专项测试(确定性引擎 + 三层对齐)

覆盖:
    [A] 口径对齐: best_combo 与 order_service._calc_price 同参数
        逐字段一致(等级/券/积分全开)
    [B] 边界: 券门槛 999/1000 / 积分 30% 上限取整百
    [C] 免邮陷阱: 用满积分跌破 99 反多付运费 → 枚举保守方案胜
        (no_points 与 keep_free_ship 两类陷阱场景)
    [D] 凑单 hint: 券缺口 / 免邮缺口 / 无缺口空串
    [E] 口述金额解析: 中文数字 / 阿拉伯数字+单位 / 裸数字
    [F] 三层对齐: COMMANDS / TOOL_REGISTRY / SAFE_READONLY
        + handle_text 全链 + 路由不误伤
    [G] fail-soft: zero 金额合法归零 / 非法输入 ValueError
        (主链 _execute 兜底不崩)

运行:
    python test_coupon_best_combo_service.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"
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
        print(f"  ✗ {name} — {detail}")


async def main():
    from repositories.store import reset_store
    reset_store()
    from services.coupon_best_combo_service import (
        best_combo, parse_amount_cn,
    )
    from services.order_service import OrderService
    from services.xiaozhu_service import (
        COMMANDS, XiaozhuService, match_command,
    )
    from services.xiaozhu_fc_registry import TOOL_REGISTRY
    from services.xiaozhu_executor import SAFE_READONLY

    print("[A 口径对齐(_calc_price 同参数)]")
    # 等级 L3 + 满 1000 券 + 积分 20000(¥200 < 券后 30% 上限)
    # 全开: 1200 → 折后 1080 → 券后 1030 → 积分抵 200 → 830 免邮
    items = [{"productId": "ZX-TEST", "quantity": 2,
              "unitPrice": 600.0}]
    combo = best_combo(1200, 3, 20000)
    ref = OrderService()._calc_price(items, 3,
                                     combo["pointsUsed"])
    record("选中用满积分方案",
           combo["planCode"] == "max_points"
           and combo["pointsUsed"] == 20000)
    record("goodsTotal/memberDiscount/couponDiscount 一致",
           combo["goodsTotal"] == ref["goodsTotal"]
           and combo["memberDiscount"] == ref["memberDiscount"]
           and combo["couponDiscount"] == ref["couponDiscount"],
           f"{combo}|{ref}")
    record("pointsDiscount/shipping/finalPay 一致",
           combo["pointsDiscount"] == ref["pointsDiscount"]
           and combo["shipping"] == ref["shippingFee"]
           and combo["finalPay"] == ref["actualAmount"],
           f"{combo}|{ref}")
    record("finalPay=830(手算口径链)",
           combo["finalPay"] == 830.0)

    print("[B 券门槛与积分上限]")
    r999 = best_combo(999, 1, 0)
    r1000 = best_combo(1000, 1, 0)
    record("999 未达券门槛",
           r999["couponDiscount"] == 0
           and r999["finalPay"] == 999)
    record("1000 券生效(边界含)",
           r1000["couponDiscount"] == -50
           and r1000["finalPay"] == 950)
    # 1000 券后 950, 30% 上限=285 → 用 100000 竹叶(¥1000)
    # 只能抵 285(取整百 28500), finalPay=665
    rcap = best_combo(1000, 1, 100000)
    record("积分 30% 上限封顶(28500 竹叶)",
           rcap["pointsUsed"] == 28500
           and rcap["pointsDiscount"] == -285.0
           and rcap["finalPay"] == 665.0,
           str(rcap))

    print("[C 免邮陷阱枚举]")
    # 陷阱①(用满反多付): 99.5 元 + 500 竹叶(¥5)——用满后
    # 94.5 < 99 破免邮线, 94.5+10=104.5 > 不用积分的 99.5;
    # 保线方案需抵扣≤0.5 元(不可行) → 最优=不用积分
    trap1 = best_combo(99.5, 1, 500)
    record("用满反多付 → 选不用积分",
           trap1["planCode"] == "no_points"
           and trap1["pointsUsed"] == 0
           and trap1["shipping"] == 0
           and trap1["finalPay"] == 99.5,
           str(trap1))
    # 手工对照: 同参数强用满 500 竹叶 → 104.5 更贵
    forced = OrderService()._calc_price(
        [{"productId": "X", "quantity": 1,
          "unitPrice": 99.5}], 1, 500)
    record("对照: 强用满确实更贵(104.5)",
           forced["actualAmount"] == 104.5
           and trap1["finalPay"] < forced["actualAmount"])
    # 陷阱②(保线最优): 128 元 + 3000 竹叶(¥30)——用满后
    # 98 < 99 破线(98+10=108); 保线用 2900 竹叶 → 恰 99 免邮
    trap2 = best_combo(128, 1, 3000)
    record("保免邮线方案胜(2900 竹叶→99)",
           trap2["planCode"] == "keep_free_ship"
           and trap2["pointsUsed"] == 2900
           and trap2["pointsDiscount"] == -29.0
           and trap2["shipping"] == 0
           and trap2["finalPay"] == 99.0,
           str(trap2))

    print("[D 凑单 hint]")
    record("999 → 提示差 1 元享满减",
           "再买 ¥1" in best_combo(999, 1, 0)["hint"]
           and "1000" in best_combo(999, 1, 0)["hint"])
    record("50 → 提示免邮缺口",
           "免邮" in best_combo(50, 1, 0)["hint"])
    record("达标无缺口 → hint 空串",
           best_combo(1200, 1, 0)["hint"] == "")

    print("[E 口述金额解析]")
    record("八百块→800", parse_amount_cn("八百块") == 800)
    record("一千二百元→1200",
           parse_amount_cn("一千二百元") == 1200)
    record("两千五块→2500(尾数补位)",
           parse_amount_cn("两千五块") == 2500)
    record("1200元→1200", parse_amount_cn("1200元") == 1200)
    record("裸数字 1200→1200",
           parse_amount_cn("算算1200怎么买") == 1200)
    record("无金额→None",
           parse_amount_cn("怎么买最划算") is None)

    print("[F 三层对齐 + 全链]")
    acts = tuple(c["action"] for c in COMMANDS)
    record("COMMANDS 含 action 且共 30 条",
           "coupon.best_combo" in acts
           and len(COMMANDS) == 30, str(len(COMMANDS)))
    record("TOOL_REGISTRY 含条目(readonly/零成本)",
           "coupon.best_combo" in TOOL_REGISTRY
           and TOOL_REGISTRY["coupon.best_combo"]["tier"]
           == "readonly"
           and TOOL_REGISTRY["coupon.best_combo"]
           ["privacyCost"] == 0.0)
    record("SAFE_READONLY 沙箱白名单含",
           "coupon.best_combo" in SAFE_READONLY)
    record("路由命中(口令组)",
           all((match_command(t) or {}).get("action")
               == "coupon.best_combo" for t in (
                   "小竹，满减最优测算",
                   "小竹，1200元怎么买最划算",
                   "小竹，帮我凑单到1000",
                   "小竹，优惠组合怎么搭")))
    record("路由不误伤(优惠/折扣仍归 promo)",
           (match_command("小竹，今天有什么优惠")
            or {}).get("action") == "promo.query"
           and (match_command("小竹，竹韵佳酿多少钱")
                or {}).get("action") == "product.price")
    # 全链: 新会话口述金额(会员 88002 无积分账户 → 0 竹叶;
    # L1 无折扣: 1200 券后 1150 免邮)
    svc = XiaozhuService()
    sid = (await svc.open_session(88002))["sessionId"]
    r = await svc.handle_text(sid, "小竹，1200元怎么买最划算")
    record("handle_text 全链(实付 1150)",
           "实付 ¥1150" in str(r.get("reply"))
           and (r.get("card") or {}).get("type")
           == "coupon_combo",
           str(r.get("reply"))[:60])
    # 无金额语境 → 引导话术(fail-soft 不崩)
    sid2 = (await svc.open_session(88002))["sessionId"]
    r2 = await svc.handle_text(sid2, "小竹，满减最优测算")
    record("无金额 → 引导话术",
           "1200" in str(r2.get("reply"))
           or "清单" in str(r2.get("reply")),
           str(r2.get("reply"))[:60])

    print("[G fail-soft]")
    zero = best_combo(0, 1, 0)
    # 0 元按口径链仍含基础运费(与 _calc_price 同口径对照)
    zero_ref = OrderService()._calc_price(
        [{"productId": "Z", "quantity": 1,
          "unitPrice": 0}], 1, 0)
    record("zero 金额合法不崩(口径对照)",
           zero["finalPay"] == zero_ref["actualAmount"]
           and zero["finalPay"] == 10.0,
           f"{zero}|{zero_ref}")
    try:
        best_combo(-5, 1, 0)
        record("负数金额 → ValueError", False, "未抛")
    except ValueError:
        record("负数金额 → ValueError", True)
    try:
        best_combo("abc", 1, 0)
        record("非数字金额 → ValueError", False, "未抛")
    except ValueError:
        record("非数字金额 → ValueError", True)
    # 引擎异常在主链 fail-soft(_execute 兜底话术, 不崩)
    sid3 = (await svc.open_session(88002))["sessionId"]
    try:
        r3 = await svc.handle_text(
            sid3, "小竹，999999999元怎么买最划算")
        record("超大金额走主链不崩",
               isinstance(r3, dict) and "reply" in r3,
               str(r3)[:60])
    except Exception as exc:  # noqa: BLE001
        record("超大金额走主链不崩", False, str(exc))

    print()
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
