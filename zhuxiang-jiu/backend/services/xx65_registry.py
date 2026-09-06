"""65号·网店及商品AI智能管理 刚性规则注册表
(xx65_registry, P0)

计划(docs/65号_网店及商品AI智能管理模块
实施计划.md §二/§四.1):
    刚性规则宪法 S1-S8(封闭注册
    ——AI 不可修改, 启动自检
    RuntimeError 宪法级):
        S1 合规前置: AI 生成内容
           未过合规校验禁止发布
           (draft 态终审不可跳过)
        S2 信值准入: 开店门槛=
           信用等级×47号 tier 双维
           动态映射——不足即拒
        S3 服务端权威: AI 生成价格
           仅为前端展示, 扣减以
           64号规则引擎为准
        S4 双轨定价: 现金价+信值
           支付额度展示(30%信值+
           70%现付经 64号 R1)
        S5 撤销窗口: 营销承诺发布
           后 5 分钟内可无理由撤销
        S6 人工兜底: AI 拦截/生成
           失败一键转人工审核
        S7 赋能分级: AI 服务配额与
           店铺信值等级动态绑定
        S8 溯源水印: AI 生成内容嵌
           入生成溯源标记(哈希
           指纹链)

    店铺六态状态机(服务端强制):
        applying → prechecked(信值
        预检过) → claimed(认领)
        → active(激活) →
        suspended(违规冻结) /
        closed(自主关店)

铁律(计划 §一):
    - LLM 不进判定链(意图路由/
      准入/合规校验全确定性)
    - 45/47/23/46/64号零改动
      (纯读取/审批总线调用/
      观测面对接——不写 64号
      任何表)
"""

import os

MODEL_VERSION = "v1-xx65-registry"

DEFAULT_MODE = "off"

MODE_VALUES = ("off", "shadow", "assist")

# ============================================================
# 刚性规则参数(宪法级)
# ============================================================

# S2 信值准入(信用等级门槛——23号
# creditLevel L1-L5; 低于门槛即拒)
SHOP_MIN_CREDIT_LEVEL = "L3"

# 信用等级排序(L1<L2<L3<L4<L5)
LEVEL_ORDER = (
    "L1", "L2", "L3", "L4", "L5")

# 47号 tier 加严映射(watched/
# restricted 门店准入加一档)
TIER_STRICTEN = {
    "restricted": 2,  # 需 L5
    "watched": 1,     # 需 L4
    "standard": 0,    # 基准 L3
    "trusted": 0,     # 基准 L3
}

# S5 撤销窗口(营销承诺)
REVOKE_WINDOW_SECONDS = 300  # 5 分钟

# S7 赋能分级(AI 服务配额——
# 按店铺等级: active/coach/
# top 三档)
AI_QUOTA_TIERS = {
    "starter": {"contentGen": 10,
                "campaigns": 2},
    "growth": {"contentGen": 50,
               "campaigns": 10},
    "premium": {"contentGen": 200,
                "campaigns": 50},
}

# 店铺六态状态机
SHOP_STATES = (
    "applying",    # 申请中(意图已解析)
    "prechecked",  # 信值预检通过
    "claimed",     # 已认领(初始化完成)
    "active",      # 激活(可经营)
    "suspended",   # 违规冻结
    "closed",      # 自主关店
)

# 状态机合法迁移(六态)
SHOP_TRANSITIONS = {
    "applying": ("prechecked",
                 "closed"),
    "prechecked": ("claimed",
                   "closed"),
    "claimed": ("active",
                "closed"),
    "active": ("suspended",
               "closed"),
    "suspended": ("active",
                  "closed"),
    "closed": (),
}

# 意图类目模板表(确定性路由——
# 关键词→类目; 匹配失败回退人工)
CATEGORY_TEMPLATES = {
    "handicraft": {
        "label": "手工艺品",
        "keywords": ("手工", "木雕",
                     "皮具", "编织",
                     "陶艺", "刺绣",
                     "定制"),
        "complianceQuestions": (
            "是否涉及珍稀材质"
            "(濒危木材/动物制品)?",),
        "minLevel": "L3",
    },
    "food": {
        "label": "食品饮品",
        "keywords": ("食品", "零食",
                     "茶叶", "酒",
                     "饮料", "特产"),
        "complianceQuestions": (
            "是否持有食品经营许可证?",
            "是否涉及保健功效宣称?"),
        "minLevel": "L4",
    },
    "service": {
        "label": "生活服务",
        "keywords": ("服务", "维修",
                     "家政", "咨询",
                     "培训", "设计"),
        "complianceQuestions": (
            "是否需要行业执业资质?",),
        "minLevel": "L3",
    },
    "digital": {
        "label": "数字商品",
        "keywords": ("数字", "课程",
                     "软件", "模板",
                     "电子书"),
        "complianceQuestions": (
            "是否涉及知识产权授权?",),
        "minLevel": "L3",
    },
    "apparel": {
        "label": "服饰家居",
        "keywords": ("服装", "服饰",
                     "家居", "饰品",
                     "箱包", "鞋"),
        "complianceQuestions": (
            "是否涉及品牌授权?",),
        "minLevel": "L3",
    },
    "general": {
        "label": "综合",
        "keywords": ("其他", "综合",
                     "通用", "杂货"),
        "complianceQuestions": (
            "是否涉及品牌授权?",),
        "minLevel": "L3",
    },
}

# 兜底类目(匹配失败回退)
CATEGORY_FALLBACK = "general"

# 准入预检口径
PRECHECK_CODES = {
    "S2_CREDIT": "信用等级达开店门槛",
    "S2_TIER": "47号 tier 无加严拦截",
    "S2_TRUST": "45号信值档案存在",
}


def current_mode() -> str:
    """模块开关(XX65_MODE, 默认 off——
    决策面关闭: off=仅观测面;
    shadow=开店观察期(留痕不初始化);
    assist=辅助经营期(开店开放))"""
    mode = os.environ.get(
        "XX65_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES \
        else DEFAULT_MODE


def level_rank(level: str) -> int:
    """信用等级序(L1=1...L5=5)"""
    try:
        return LEVEL_ORDER.index(
            str(level or "").upper())
    except ValueError:
        return 0


def required_level(tier: str = None
                   ) -> str:
    """按 47号 tier 计算开店门槛
    (watched/restricted 加严)"""
    base = level_rank(
        SHOP_MIN_CREDIT_LEVEL)
    stricten = TIER_STRICTEN.get(
        str(tier or "standard"), 0)
    idx = min(len(LEVEL_ORDER) - 1,
              base + stricten)
    return LEVEL_ORDER[idx]


def quota_tier(credit_level: str
               ) -> str:
    """S7 AI 服务配额档(L3=starter/
    L4=growth/L5=premium)"""
    rank = level_rank(credit_level)
    if rank >= 4:
        return "premium"
    if rank == 3:
        return "growth"
    return "starter"


def registry_view() -> dict:
    """刚性规则自描述(观测面
    ——不受开关影响)"""
    return {
        "success": True,
        "module": "65号·网店及商品"
                  "AI智能管理",
        "modelVersion": MODEL_VERSION,
        "mode": current_mode(),
        "rules": {
            "S1_COMPLIANCE":
                "AI 内容未过合规校验"
                "禁止发布(draft 终审"
                "不可跳过)",
            "S2_ADMISSION":
                f"开店门槛≥{SHOP_MIN_CREDIT_LEVEL}"
                "(tier 加严映射)",
            "S3_SERVER_AUTH":
                "AI 价格仅展示, 扣减以"
                "64号规则引擎为准",
            "S4_DUAL_PRICING":
                "双轨定价: 现金+信值"
                "支付额度(经 64号 R1)",
            "S5_REVOKE":
                f"营销承诺 {REVOKE_WINDOW_SECONDS}"
                "s 撤销窗口",
            "S6_HUMAN_FALLBACK":
                "AI 拦截/失败一键转人工",
            "S7_QUOTA_TIERS":
                "AI 配额与信值等级"
                "动态绑定",
            "S8_WATERMARK":
                "AI 生成内容溯源水印",
        },
        "shopStates": SHOP_STATES,
        "shopTransitions":
            SHOP_TRANSITIONS,
        "categories": {
            k: {
                "label": v["label"],
                "minLevel": v["minLevel"],
                "questions": len(
                    v["complianceQuestions"]),
            }
            for k, v in
            CATEGORY_TEMPLATES.items()},
        "quotaTiers": AI_QUOTA_TIERS,
        "note": "P0 底座: S1-S8 刚性"
                "规则+店铺六态状态机"
                "+意图路由+信值准入",
    }


def _validate_registry() -> None:
    """启动自检(宪法级——
    状态机全态可达+规则参数
    域合法)"""
    # 状态机全态可达(BFS from
    # applying)
    reachable = {"applying"}
    changed = True
    while changed:
        changed = False
        for src, dsts in \
                SHOP_TRANSITIONS.items():
            if src in reachable:
                for d in dsts:
                    if d not in reachable:
                        reachable.add(d)
                        changed = True
    missing = set(SHOP_STATES) \
        - reachable
    if missing:
        raise RuntimeError(
            f"65号店铺状态机存在"
            f"不可达态: {missing}")
    # 门槛映射合法
    for tier in TIER_STRICTEN:
        lv = required_level(tier)
        if lv not in LEVEL_ORDER:
            raise RuntimeError(
                f"65号门槛映射非法: "
                f"{tier}→{lv}")
    # 配额档覆盖
    for qt in ("starter", "growth",
               "premium"):
        if qt not in AI_QUOTA_TIERS:
            raise RuntimeError(
                f"65号配额档缺失: {qt}")


# 模块导入即自检(宪法级)
_validate_registry()
