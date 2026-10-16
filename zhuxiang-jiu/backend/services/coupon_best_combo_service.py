# -*- coding: utf-8 -*-
"""满减最优测算·确定性引擎(小竹指令 coupon.best_combo 专用)

服务于小竹语音助手「满减最优测算」指令(coupon.best_combo——
"怎么买最划算/满减/优惠组合/凑单"口语直达), 对应蓝图对照
分析近期项①。

口径与 order_service._calc_price 严格对齐(六步链, 常量全部
从 order_service import——单一数据源, 禁止复制数字):
    商品总价 → 等级折扣 LEVEL_DISCOUNTS → Mock 券(满
    COUPON_THRESHOLD 减 COUPON_DISCOUNT, 判定基于
    goods_total) → 积分(POINTS_PER_YUAN 竹叶=¥1, 抵扣
    上限=券后金额×POINTS_DISCOUNT_CAP) → 运费
    (after_points>=SHIPPING_FREE_THRESHOLD 免邮) →
    round_half_up 商业舍入

积分免邮陷阱(枚举三方案取实付最小):
    积分抵扣发生在免邮线判定之前——"用满积分"可能把
    after_points 拉破免邮线, 反而多付 SHIPPING_FEE 运费;
    "不用积分"则白白放弃抵扣。故枚举
    {不用积分, 用满可用上限, 用到恰好保住免邮线(若可行)}
    三方案, 取 finalPay 最小者(并列时少用积分——保余额)。

铁律: 零 LLM——所有金额数字由本确定性引擎产出, LLM 只
负责选择指令(见 xiaozhu_fc_registry 描述即护栏)。
"""

import re

from core.helpers import round_half_up
from services.order_service import (
    COUPON_DISCOUNT, COUPON_THRESHOLD,
    LEVEL_DISCOUNTS, POINTS_DISCOUNT_CAP,
    POINTS_PER_YUAN, SHIPPING_FEE,
    SHIPPING_FREE_THRESHOLD,
)

# 积分使用须为 100 的整数倍(下单侧 deduct_points 校验口径)
_POINTS_STEP = POINTS_PER_YUAN

# 中文数字字符表(金额口语解析用)
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3,
             "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
             "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}

# 口述金额正则(按优先级): 阿拉伯数字+单位 → 中文数字+单位 →
# 裸 3-6 位数字("1200 怎么买最划算"; 上限防误抓流水号)
_AMT_ARABIC_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:元|块)")
_AMT_CN_RE = re.compile(
    r"([零一二两三四五六七八九十百千万]+)\s*(?:块钱|元|块)")
_AMT_BARE_RE = re.compile(r"(?<![\d.])(\d{3,6})(?![\d.])")

# 口述金额上限(防误抓订单号/手机号片段)
_AMOUNT_MAX = 999999.0


def _cn_to_int(s: str) -> int:
    """中文数字串转整数("八百"→800, "一千二"→1200,
    "两千五百"→2500, "一万二"→12000, "一百零五"→105)"""
    total, section, num = 0, 0, 0
    zero_before_tail = False
    for ch in s:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
            zero_before_tail = (ch == "零")
        elif ch == "万":
            section = (section + num) * 10000
            total += section
            section, num = 0, 0
            zero_before_tail = False
        elif ch in _CN_UNIT:
            section += (num or 1) * _CN_UNIT[ch]
            num = 0
            zero_before_tail = False
    # 收尾: 尾数无单位("一千二"的"二"=200——按 section
    # 下一级单位补位; "一百零五"的"五"=5——零后尾数按个位)
    if num > 0:
        if zero_before_tail:
            mag = 1
        elif section > 0:
            # 下一级单位: 千级(1000)尾数补百位, 百级(200)
            # 尾数补十位, 十级(20)尾数补个位
            mag = 10 ** max(len(str(section)) - 2, 0)
        elif total > 0:
            mag = 1000
        else:
            mag = 1
        return total + section + num * mag
    return total + section


def parse_amount_cn(text: str) -> float | None:
    """口述金额解析("八百块"→800, "1200元"→1200,
    "一千二百元"→1200, "1200 怎么买最划算"→1200)

    未命中/超上限返回 None(调用方降级引导)。
    """
    t = str(text or "")
    m = _AMT_ARABIC_RE.search(t)
    if m:
        v = float(m.group(1))
        return v if v <= _AMOUNT_MAX else None
    m = _AMT_CN_RE.search(t)
    if m:
        v = float(_cn_to_int(m.group(1)))
        return v if 0 < v <= _AMOUNT_MAX else None
    m = _AMT_BARE_RE.search(t)
    if m:
        v = float(m.group(1))
        return v if 0 < v <= _AMOUNT_MAX else None
    return None


def _floor_points(value: float) -> int:
    """金额(元)折算可用积分并向下取整到 100 倍数"""
    n = int(value * POINTS_PER_YUAN
            // _POINTS_STEP * _POINTS_STEP)
    return max(n, 0)


def _price_chain(goods_total: float, member_level: int,
                 use_points: int) -> dict:
    """单方案价格链(与 _calc_price 逐步同口径; 纯函数)"""
    # 2. 会员折扣(对商品总价打折)
    discount_rate = LEVEL_DISCOUNTS.get(member_level, 1.0)
    member_discount = -(goods_total * (1 - discount_rate))
    after_member = goods_total + member_discount
    # 3. 优惠券(满 1000 减 50, 判定基于 goods_total)
    coupon_discount = (-COUPON_DISCOUNT
                       if goods_total >= COUPON_THRESHOLD
                       else 0)
    after_coupon = after_member + coupon_discount
    # 4. 积分抵扣(100 竹叶=¥1, 上限=券后×30%)
    points_discount = 0
    if use_points > 0:
        cap_amount = after_coupon * POINTS_DISCOUNT_CAP
        points_value = use_points / POINTS_PER_YUAN
        points_discount = -min(points_value, cap_amount)
    after_points = after_coupon + points_discount
    # 5. 运费(满 99 免邮——判定在积分抵扣之后)
    shipping_fee = (0 if after_points
                    >= SHIPPING_FREE_THRESHOLD
                    else SHIPPING_FEE)
    # 6. 实付(round_half_up 商业舍入)
    final_pay = round_half_up(after_points + shipping_fee)
    return {
        "pointsUsed": use_points,
        "pointsDiscount": round_half_up(points_discount),
        "shipping": shipping_fee,
        "finalPay": final_pay,
        "afterCoupon": after_coupon,
    }


def _build_hint(goods_total: float, base_chain: dict) -> str:
    """凑单提示(基于不用积分的基准链; 无缺口返回空串)"""
    hints = []
    # 券门槛缺口(判定口径与 _calc_price 一致: 基于 goods_total)
    if goods_total < COUPON_THRESHOLD:
        gap = round_half_up(
            COUPON_THRESHOLD - goods_total)
        hints.append(f"再买 ¥{gap:g} 可享满 "
                     f"{COUPON_THRESHOLD} 减 "
                     f"{COUPON_DISCOUNT}")
    # 免邮缺口(基准方案仍需付运费时, 按券后金额算差价)
    after_coupon = base_chain["afterCoupon"]
    if base_chain["shipping"] and after_coupon \
            < SHIPPING_FREE_THRESHOLD:
        gap = round_half_up(
            SHIPPING_FREE_THRESHOLD - after_coupon)
        hints.append(f"加购 ¥{gap:g} 可免邮")
    return "；".join(hints)


def best_combo(goods_total: float, member_level: int = 1,
               points_available: int = 0) -> dict:
    """满减最优测算(确定性纯函数, 零 LLM)

    Args:
        goods_total: 商品总价(元)——由调用方从会话购物清单
            聚合或口述金额解析, 须为非负数
        member_level: 会员等级 1-5(缺省 1)
        points_available: 可用竹叶余额(缺省 0)

    Returns:
        {plan, planCode, goodsTotal, memberLevel,
         memberDiscount, couponDiscount, pointsUsed,
         pointsDiscount, shipping, finalPay, savings, hint}
        savings=相对无任何优惠口径(原价+原价运费)的节省额

    Raises:
        ValueError: 金额/等级/积分为非法输入(负数/非数字)
    """
    try:
        goods_total = float(goods_total)
    except (TypeError, ValueError):
        raise ValueError("测算金额须为数字") from None
    if goods_total < 0:
        raise ValueError("测算金额须为非负数")
    try:
        member_level = int(member_level)
    except (TypeError, ValueError):
        raise ValueError("会员等级须为整数") from None
    if member_level not in LEVEL_DISCOUNTS:
        member_level = 1
    try:
        points_available = max(int(points_available), 0)
    except (TypeError, ValueError):
        points_available = 0

    # 1. 商品总价 + 2/3. 等级折扣与满减券(与积分无关,
    #    三方案共用同一段前置链)
    discount_rate = LEVEL_DISCOUNTS.get(member_level, 1.0)
    member_discount = -(goods_total * (1 - discount_rate))
    coupon_discount = (-COUPON_DISCOUNT
                       if goods_total >= COUPON_THRESHOLD
                       else 0)
    after_coupon = (goods_total + member_discount
                    + coupon_discount)

    # 积分可用上限: min(余额, 券后×30%), 且取整到 100 倍数
    max_usable = _floor_points(
        min(points_available / POINTS_PER_YUAN,
            after_coupon * POINTS_DISCOUNT_CAP))

    # 方案枚举: A 不用积分 / B 用满上限 / C 恰好保住免邮线
    candidates = [
        ("no_points", "不使用积分", 0),
        ("max_points", "用满积分抵扣", max_usable),
    ]
    # 保线最大抵扣额(元) = 券后金额 - 免邮线, 折竹叶取整百
    keep_free = _floor_points(
        after_coupon - SHIPPING_FREE_THRESHOLD)
    keep_free = min(keep_free, max_usable)
    if keep_free > 0:
        candidates.append(
            ("keep_free_ship", "用积分保免邮线", keep_free))

    chains = []
    for code, label, pts in candidates:
        if pts <= 0 and code != "no_points":
            continue
        chain = _price_chain(goods_total, member_level, pts)
        chains.append((code, label, pts, chain))
    # 取 finalPay 最小; 并列时少用积分(保余额优先)
    code, label, pts, chain = min(
        chains, key=lambda c: (c[3]["finalPay"], c[2]))

    # 基准链(不用积分)供 hint 口径
    base = _price_chain(goods_total, member_level, 0)
    # 原价口径应付 = 原价 + 原价运费(无任何优惠时)
    orig_shipping = (0 if goods_total
                     >= SHIPPING_FREE_THRESHOLD
                     else SHIPPING_FEE)
    orig_pay = round_half_up(goods_total + orig_shipping)

    return {
        "plan": label,
        "planCode": code,
        "goodsTotal": round_half_up(goods_total),
        "memberLevel": member_level,
        "memberDiscount": round_half_up(member_discount),
        "couponDiscount": round_half_up(coupon_discount),
        "pointsUsed": pts,
        "pointsDiscount": chain["pointsDiscount"],
        "shipping": chain["shipping"],
        "finalPay": chain["finalPay"],
        "savings": round_half_up(orig_pay
                                 - chain["finalPay"]),
        "hint": _build_hint(goods_total, base),
    }
