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

# ============================================================
# P1·AI 内容工坊——合规三道防线+下单窗口参数(宪法级)
# ============================================================

# LLM 文案轨开关(XX65_LLM_MODE, 默认 off
# ——off=rule 轨确定性模板; on=glm 主档
# →备档→rule 三级降级; LLM 输出仍过
# 禁词替换——S1 不豁免)
LLM_TRACK_PRIMARY = "glm-5.3"
LLM_TRACK_FALLBACK = "glm-4-flash"
LLM_TRACK_RULE = "rule"

# 禁词替换映射(广告法极限词——替换级:
# 生成时自动替换+记录留痕, 同输入同输出)
BANNED_REPLACEMENTS = {
    "最好": "高品质", "最佳": "高品质",
    "第一": "领先", "顶级": "上乘",
    "极品": "上乘", "史上最": "十分",
    "绝无仅有": "少见", "百分百": "高",
    "绝对": "颇为", "永久": "长效",
    "全网最低": "实惠", "国家级": "优质",
    "世界级": "优质", "最高级": "高档",
    "万能": "多用途", "顶级工艺": "精工",
}

# 严重违禁词(拒绝级——S1 硬拦:
# 虚假功效/医疗宣称, 不可自动替换,
# 仅可人工核实后处置)
SEVERE_WORDS = (
    "包治百病", "根治", "治愈",
    "特效药", "无效退款", "治愈率",
    "抗癌", "降三高", "延年益寿",
    "提高免疫力", "医疗功效")

# 合规通过分(三道防线统一口径:
# ①生成时过滤 ②发布前二次 ③上架后巡检)
COMPLIANCE_PASS_SCORE = 80

# 严重词/极限词扣分权重(评分口径)
SEVERE_PENALTY = 40
BANNED_PENALTY = 10

# 草稿四态状态机(draft→published
# 发布流——S1 终审不可跳过)
DRAFT_STATES = (
    "draft",         # 已生成(待发布)
    "pending_review",  # 转人工审核(S6)
    "published",     # 已发布(生成商品)
    "rejected",      # 人工驳回
)

DRAFT_TRANSITIONS = {
    "draft": ("published",
              "pending_review",
              "rejected"),
    "pending_review": ("published",
                       "rejected"),
    "published": (),
    "rejected": (),
}

# 下单窗口展示参数(S4 双轨展示——
# 仅前端展示不做结算, 扣减以 64号
# 规则引擎为准——S3 服务端权威)
TRUST_DISPLAY_PORTION = 0.30  # 对齐 64号 R1
POINTS_PER_TRUST_DISPLAY = 100  # 对齐 64号 R6

# 下单窗口预警阈值(展示层口径:
# 单次≥15%/累计≥35% 触发二次确认)
ORDER_WINDOW_SINGLE_WARN = 0.15
ORDER_WINDOW_CUMULATIVE_WARN = 0.35

# 老年受众无障碍标记(意图受众
# 命中即 order-window 输出大字版
# +语音导购脚本提示)
ELDER_AUDIENCE_MARKERS = (
    "老年", "长辈", "中老年", "银发")


def llm_mode() -> str:
    """LLM 文案轨开关(XX65_LLM_MODE,
    默认 off——LLM 仅文案润色位,
    判定链全确定性)"""
    mode = os.environ.get(
        "XX65_LLM_MODE") or "off"
    return mode if mode in (
        "off", "on") else "off"


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
        "draftStates": DRAFT_STATES,
        "draftTransitions":
            DRAFT_TRANSITIONS,
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
        "llmMode": llm_mode(),
        "compliance": {
            "passScore":
                COMPLIANCE_PASS_SCORE,
            "bannedWords":
                len(BANNED_REPLACEMENTS),
            "severeWords":
                len(SEVERE_WORDS),
            "defenseLines": (
                "gen_filter",
                "publish_recheck",
                "post_inspect"),
        },
        "orderWindow": {
            "trustPortion":
                TRUST_DISPLAY_PORTION,
            "singleWarn":
                ORDER_WINDOW_SINGLE_WARN,
            "cumulativeWarn":
                ORDER_WINDOW_CUMULATIVE_WARN,
            "pointsPerTrust":
                POINTS_PER_TRUST_DISPLAY,
        },
        "note": "P1 内容工坊: S1-S8 刚性"
                "规则+店铺/草稿双状态机"
                "+意图路由+信值准入+合规"
                "三道防线+下单窗口双轨展示",
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
    # 草稿状态机全态可达(BFS
    # from draft)
    d_reach = {"draft"}
    changed = True
    while changed:
        changed = False
        for src, dsts in \
                DRAFT_TRANSITIONS.items():
            if src in d_reach:
                for d in dsts:
                    if d not in d_reach:
                        d_reach.add(d)
                        changed = True
    d_missing = set(DRAFT_STATES) \
        - d_reach
    if d_missing:
        raise RuntimeError(
            f"65号草稿状态机存在"
            f"不可达态: {d_missing}")
    # 禁词替换映射值不得再含
    # 禁词(防替换自嵌套)
    for word, repl in \
            BANNED_REPLACEMENTS.items():
        if any(w in repl for w
               in BANNED_REPLACEMENTS
               if w != word):
            raise RuntimeError(
                f"65号禁词替换自嵌套: "
                f"{word}→{repl}")
    # 严重词不得出现在替换映射键
    overlap = set(SEVERE_WORDS) & \
        set(BANNED_REPLACEMENTS)
    if overlap:
        raise RuntimeError(
            f"65号严重词与替换级重叠: "
            f"{overlap}")
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
    # 展示参数域合法(S4 对齐
    # 64号 R1/R6——仅展示)
    if not 0 < TRUST_DISPLAY_PORTION \
            < 1:
        raise RuntimeError(
            "65号信值展示占比越界")
    if not 0 < ORDER_WINDOW_SINGLE_WARN \
            <= ORDER_WINDOW_CUMULATIVE_WARN \
            < 1:
        raise RuntimeError(
            "65号下单窗口预警阈值"
            "域非法")


# 模块导入即自检(宪法级)
_validate_registry()
