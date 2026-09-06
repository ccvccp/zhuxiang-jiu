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

# ============================================================
# P2·智能营销中枢——三因子规则库+ROI 双算+渠道适配(宪法级)
# ============================================================

# 三因子权重(店铺信值/商品热度/
# 季节趋势——确定性线性加权)
CAMPAIGN_FACTOR_WEIGHTS = {
    "shop_trust": 0.40,
    "product_heat": 0.35,
    "season_trend": 0.25,
}

# 季度趋势(月→当季类目加成——
# 确定性映射; 商品类目命中=1.0,
# 未命中=0.5 中性)
SEASON_TRENDS = {
    1: ("apparel", "food"),
    2: ("apparel", "handicraft"),
    3: ("handicraft", "service"),
    4: ("handicraft", "service"),
    5: ("digital", "service"),
    6: ("food", "digital"),
    7: ("food", "digital"),
    8: ("apparel", "digital"),
    9: ("apparel", "general"),
    10: ("food", "handicraft"),
    11: ("apparel", "food"),
    12: ("food", "handicraft"),
}

# 策略类型(推荐输出——流动性
# 感知联动 64号 anchors/LIQ-CRUNCH)
CAMPAIGN_STRATEGIES = {
    # 信值专享爆款(购买力指数高
    # →通缩倾向→促信值消耗)
    "trust_exclusive": {
        "label": "信值专享爆款",
        "roiCashLift": 0.20,
        "trustPortion": 0.30,
        "channels": ("in_site", "community"),
        "note": "信值支付专享价——"
                "促进信值流通",
    },
    # 小额高频组合(流动性紧张
    # →LIQ-CRUNCH 口径预警→
    # 降低单笔信值消耗)
    "small_high_freq": {
        "label": "小额高频组合",
        "roiCashLift": 0.12,
        "trustPortion": 0.10,
        "channels": ("in_site",),
        "note": "低信值占比+"
                "高频次——流动性友好",
    },
    # 新客专享(店铺信值因子高
    # →扩张期获客)
    "new_customer": {
        "label": "新客专享",
        "roiCashLift": 0.18,
        "trustPortion": 0.30,
        "channels": ("in_site", "community",
                     "sms"),
        "note": "首单立减+信值券"
                "包——获客杠杆",
    },
    # 季节主推(类目命中当季
    # 趋势)
    "seasonal": {
        "label": "季节主推",
        "roiCashLift": 0.15,
        "trustPortion": 0.30,
        "channels": ("in_site", "community"),
        "note": "当季类目加权"
                "曝光",
    },
    # 清仓特卖(默认兜底)
    "clearance": {
        "label": "清仓特卖",
        "roiCashLift": 0.25,
        "trustPortion": 0.30,
        "channels": ("in_site",),
        "note": "去库存优先——"
                "回笼现金",
    },
}

# 流动性信号(对齐 64号 LIQ-CRUNCH
# 40% 口径——只读感知不处置)
LIQUIDITY_TENSION_RATIO = 0.40

# 购买力指数信号(对齐 64号
# anchors——指数高于该值=信值
# 购买力强→推荐信值专享)
ANCHOR_TRUST_SINK_THRESHOLD = 1.10

# ROI 双算参数(确定性公式:
# 预计现金GMV=价格×销量×
# (1+lift); 预计信值消耗=
# GMV×trustPortion)
ROI_BASE_SALES = 20  # 预计销量基准(件)

# 渠道适配表(跨渠道智能分发
# ——确定性适配)
CAMPAIGN_CHANNELS = {
    "in_site": {
        "label": "站内",
        "maxLength": 60,
        "voiceGuide": False,
    },
    "community": {
        "label": "社群",
        "maxLength": 120,
        "voiceGuide": False,
    },
    "sms": {
        "label": "短信",
        "maxLength": 30,
        "voiceGuide": False,
    },
}

# 类目互补表(跨店联动——确定性
# 映射; 仅建议, 执行经 46号)
CATEGORY_COMPLEMENTS = {
    "handicraft": ("apparel",
                   "digital"),
    "food": ("handicraft",),
    "apparel": ("handicraft",),
    "digital": ("handicraft",),
    "service": ("digital",),
    "general": ("food",),
}

# 活动状态机(active→revoked
# 5min 窗口内/active→expired
# 到期; revoked/expired 终态)
CAMPAIGN_STATES = (
    "active",    # 生效中
    "revoked",   # 5 分钟窗口内撤销(S5)
    "expired",   # 到期结束
)

CAMPAIGN_TRANSITIONS = {
    "active": ("revoked", "expired"),
    "revoked": (),
    "expired": (),
}

# ============================================================
# P3·治理与成长层——健康度口径+教练内容池+激励参数(宪法级)
# ============================================================

# 健康度三组件权重(合规事件命中/
# 商品标记/活动撤销——全反向)
HEALTH_WEIGHTS = {
    "compliance_events": 0.40,
    "product_flags": 0.35,
    "campaign_revokes": 0.25,
}

# 健康度通过分(看板绿色阈值)
HEALTH_PASS_SCORE = 70

# S7 配额升降档激励参数(信值正反馈
# ——仅建议, 惩罚性降档与奖励性升档
# 均经 46号审批轨, 永不自动执行)
QUOTA_UPLIFT_MIN_HEALTH = 85      # 健康度≥85 可建议升档
QUOTA_DOWNGRADE_MAX_HEALTH = 50   # 健康度≤50 触发降档建议
QUOTA_TIER_ORDER = (
    "starter", "growth", "premium")

# 教练内容池(确定性——按店铺配额档
# 分发, 非即时生成; 三类: daily_tip
# 每日贴士/hot_case 爆款案例/
# warning 错误预警)
COACH_TIPS = (
    {"tier": "starter",
     "kind": "daily_tip",
     "title": "保持店铺活跃",
     "body": "每日上架 1-2 件商品并"
             "完善描述——活跃度是"
             "店铺成长的第一杠杆。"},
    {"tier": "starter",
     "kind": "hot_case",
     "title": "手工艺类目标杆",
     "body": "定制木雕小店月销破百"
             "的秘诀: 故事化详情页+"
             "实拍图, 让买家看见手艺。"},
    {"tier": "starter",
     "kind": "warning",
     "title": "广告法极限词",
     "body": "避免使用'最好/第一'等"
             "极限词——系统自动替换"
             "并留痕, 人工核实耗时。"},
    {"tier": "growth",
     "kind": "daily_tip",
     "title": "双轨定价展示",
     "body": "开启信值支付额度展示"
             "——信值友好店铺可获"
             "更多平台曝光加权。"},
    {"tier": "growth",
     "kind": "hot_case",
     "title": "小额高频组合",
     "body": "当季特产+小额高频活动"
             "的 ROI 双算显示: 信值"
             "消耗更平稳、复购更高。"},
    {"tier": "growth",
     "kind": "warning",
     "title": "R2 整单互斥",
     "body": "信值支付订单整单互斥"
             "其他优惠——活动叠加前"
             "务必核对互斥声明。"},
    {"tier": "premium",
     "kind": "daily_tip",
     "title": "合规巡检习惯",
     "body": "定期查看合规健康度看板"
             "——一次过审率直接影响"
             "店铺健康度评分。"},
    {"tier": "premium",
     "kind": "hot_case",
     "title": "跨店联动",
     "body": "互补类目联合促销获"
             "更高 ROI——建议经"
             " 46号审批后执行。"},
    {"tier": "premium",
     "kind": "warning",
     "title": "S5 撤销窗口",
     "body": "营销承诺发布后 5 分钟"
             "内可无理由撤销——超窗"
             "需人工处置通道。"},
)

# 争议证据链(确定性聚合口径——
# 65号域内四源+64号订单只读)
DISPUTE_EVIDENCE_KINDS = (
    "shop",       # 店铺档案+准入快照
    "product",    # 商品+合规标记
    "compliance", # 合规事件链(三道防线)
    "campaign",   # 活动+撤销审计
    "order64",    # 64号订单(只读对接)
)


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
        "campaignStates": CAMPAIGN_STATES,
        "campaignStrategies": {
            k: {"label": v["label"],
                "trustPortion":
                    v["trustPortion"],
                "channels": list(
                    v["channels"])}
            for k, v in
            CAMPAIGN_STRATEGIES.items()},
        "campaignFactors":
            CAMPAIGN_FACTOR_WEIGHTS,
        "health": {
            "weights":
                HEALTH_WEIGHTS,
            "passScore":
                HEALTH_PASS_SCORE,
            "upliftMinHealth":
                QUOTA_UPLIFT_MIN_HEALTH,
            "downgradeMaxHealth":
                QUOTA_DOWNGRADE_MAX_HEALTH,
        },
        "coachPool": {
            "total": len(COACH_TIPS),
            "tiers": sorted(
                {t["tier"]
                 for t in COACH_TIPS}),
            "kinds": sorted(
                {t["kind"]
                 for t in COACH_TIPS}),
        },
        "note": "P3 治理与成长层: "
                "S1-S8 刚性规则+店铺/"
                "草稿/活动三状态机+健康"
                "度看板+教练内容池+S7 "
                "激励经 46号审批轨+争议"
                "证据链辅助",
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
    # P2: 三因子权重归一
    w_sum = sum(
        CAMPAIGN_FACTOR_WEIGHTS
        .values())
    if abs(w_sum - 1.0) > 0.001:
        raise RuntimeError(
            f"65号三因子权重未归一: "
            f"{w_sum}")
    # P2: 季度趋势全月覆盖
    for m in range(1, 13):
        if m not in SEASON_TRENDS:
            raise RuntimeError(
                f"65号季度趋势缺月: "
                f"{m}")
        for cat in SEASON_TRENDS[m]:
            if cat not in \
                    CATEGORY_TEMPLATES:
                raise RuntimeError(
                    f"65号季度趋势类目"
                    f"未注册: {cat}")
    # P2: 策略参数域合法
    for key, s in \
            CAMPAIGN_STRATEGIES.items():
        if not 0 < s["roiCashLift"] \
                < 1:
            raise RuntimeError(
                f"65号策略 lift 越界: "
                f"{key}")
        if not 0 < s["trustPortion"] \
                <= TRUST_DISPLAY_PORTION \
                + 0.001:
            raise RuntimeError(
                f"65号策略信值占比越界: "
                f"{key}")
        for ch in s["channels"]:
            if ch not in \
                    CAMPAIGN_CHANNELS:
                raise RuntimeError(
                    f"65号策略渠道未注册: "
                    f"{key}/{ch}")
    # P2: 互补表类目闭环
    for cat, comps in \
            CATEGORY_COMPLEMENTS.items():
        if cat not in \
                CATEGORY_TEMPLATES:
            raise RuntimeError(
                f"65号互补表类目未注册: "
                f"{cat}")
        for c in comps:
            if c not in \
                    CATEGORY_TEMPLATES:
                raise RuntimeError(
                    f"65号互补表目标未注册: "
                    f"{cat}→{c}")
    # P2: 活动状态机可达
    c_reach = {"active"}
    for dst in \
            CAMPAIGN_TRANSITIONS[
                "active"]:
        c_reach.add(dst)
    c_missing = set(
        CAMPAIGN_STATES) - c_reach
    if c_missing:
        raise RuntimeError(
            f"65号活动状态机不可达: "
            f"{c_missing}")
    # P2: 流动性阈值对齐 64号
    # LIQ-CRUNCH 40%
    if LIQUIDITY_TENSION_RATIO \
            != 0.40:
        raise RuntimeError(
            "65号流动性阈值未对齐"
            "64号 LIQ-CRUNCH")
    # P3: 健康度权重归一
    h_sum = sum(
        HEALTH_WEIGHTS.values())
    if abs(h_sum - 1.0) > 0.001:
        raise RuntimeError(
            f"65号健康度权重未归一: "
            f"{h_sum}")
    # P3: 激励阈值域合法
    # (升档阈值>通过分>降档阈值)
    if not QUOTA_DOWNGRADE_MAX_HEALTH \
            < HEALTH_PASS_SCORE \
            < QUOTA_UPLIFT_MIN_HEALTH:
        raise RuntimeError(
            "65号激励阈值域非法"
            "(须 降档<通过<升档)")
    # P3: 教练内容池覆盖
    # (每档×每类至少 1 条)
    for tier in ("starter",
                 "growth",
                 "premium"):
        for kind in ("daily_tip",
                     "hot_case",
                     "warning"):
            if not any(
                    t["tier"] == tier
                    and t["kind"]
                    == kind
                    for t in COACH_TIPS):
                raise RuntimeError(
                    f"65号教练池缺"
                    f"{tier}/{kind}")
    # P3: 争议证据链域合法
    for ek in DISPUTE_EVIDENCE_KINDS:
        if not ek:
            raise RuntimeError(
                "65号争议证据源空值")


# 模块导入即自检(宪法级)
_validate_registry()
