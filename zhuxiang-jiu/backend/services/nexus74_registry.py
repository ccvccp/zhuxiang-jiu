"""74号·NexusFlow(智枢·流)AI智能全域发布大模型
体验注册表(nexus74_registry, P1)

规划(docs/74号_NexusFlow智枢流_AI智能全域发布大模型_
创新规划方案.md §二/§四/§七/§九):
    模式四档(NEXUSFLOW74_MODE off/shadow/
    assist/full——发布面门控; B 档平台
    操作永远人工确认[铁律二])
    +六平台域(微信公众号/抖音/小红书/
    知乎/B站/头条)
    +适配器分级(A 档 API 直连仅微信;
    B 档半自动+回执登记; C 档预留)
    +内容意图域(资讯/教程/种草/观点)
    +平台选择矩阵(意图×平台适配分——
    确定性查表)
    +合规规则四类型(禁止/条件禁止/
    强制要求/建议)
    +合规四态(pass/review_required/
    block/legal_risk)
    +酒类六红线(R1 诱导饮酒/R2 醉酒
    形象/R3 健康功效/R4 未成年人/
    R5 绝对化用语/R6 警示语缺失)
    +灰度边界词+安全港例外
    +人设状态机(专业/观察者/贴心)

设计(71/72/73号封闭注册表范式平移):
    - 封闭注册: 可断言/可测试/启动自检
    - 合规判定 100% 确定性(词表+模式
      匹配+结构校验), LLM 禁入判定链
      铁律(规划 §二 2.3)
    - 72号感知子系统物理复用,
      本注册表零触碰 ATTRACT72_*

启动自检 _validate_registry()(RuntimeError
宪法级):
    - 模式四档/平台/意图/规则类型/
      合规四态/六红线域封闭
    - 词表非空+法律高危子集归属
    - 矩阵行完备(意图×六平台)
    - 警示语规范域内
"""

import logging
import os

logger = logging.getLogger("nexus74_registry")

MODEL_VERSION = "v1-nexus74-registry"

DEFAULT_MODE = "off"

# 四档(发布域: A 档低风险可 full;
# B 档平台操作永远人工确认——
# 发布显式性铁律)
MODE_VALUES = ("off", "shadow", "assist", "full")


def current_mode() -> str:
    """模块开关(NEXUSFLOW74_MODE, 默认 off——
    off=仅观测面(规则库/人格/矩阵只读),
    发布面关闭; shadow=适配包留痕不输出;
    assist=适配包输出至人工确认位;
    full=仅 A 档低风险域自主"""
    mode = (os.environ.get("NEXUSFLOW74_MODE")
            or DEFAULT_MODE)
    return (mode if mode in MODE_VALUES
            else DEFAULT_MODE)


def is_kill() -> bool:
    """紧急静默(NEXUSFLOW74_KILL——发布面
    强制关闭, 观测面保留)"""
    return (os.environ.get("NEXUSFLOW74_KILL")
            == "1")


# 解冻环境变量(免疫双保险——P5)
IMMUNITY_UNFREEZE_ENV = "NEXUSFLOW74_IMMUNITY"

# ============================================================
# 六平台域+适配器分级(诚实工程)
# ============================================================

# 平台域(封闭)
PLATFORMS: tuple = (
    "wechat_mp",      # 微信公众号
    "douyin",         # 抖音
    "xiaohongshu",    # 小红书
    "zhihu",          # 知乎
    "bilibili",       # B站
    "toutiao",        # 今日头条
)

PLATFORM_NAMES: dict = {
    "wechat_mp": "微信公众号",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "zhihu": "知乎",
    "bilibili": "B站",
    "toutiao": "今日头条",
}

# 适配器分级(A=API 直连; B=半自动适配包
# +人工操作+回执登记; C=预留)
ADAPTER_TIERS: dict = {
    "wechat_mp": "A",
    "douyin": "B",
    "xiaohongshu": "B",
    "zhihu": "B",
    "bilibili": "B",
    "toutiao": "B",
}

ADAPTER_TIER_LABELS: dict = {
    "A": "API 直连",
    "B": "半自动(人工操作+回执)",
    "C": "预留",
}

# ============================================================
# 内容意图域(感知层——源内容分类)
# ============================================================

INTENT_TYPES: tuple = (
    "news",       # 资讯
    "tutorial",   # 教程
    "seeding",    # 种草
    "opinion",   # 观点
)

INTENT_LABELS: dict = {
    "news": "资讯",
    "tutorial": "教程",
    "seeding": "种草",
    "opinion": "观点",
}

# 平台选择矩阵(意图×平台适配分——
# 确定性查表, LLM 禁入)
INTENT_PLATFORM_SCORES: dict = {
    "news": {
        "toutiao": 0.9, "wechat_mp": 0.8,
        "zhihu": 0.6, "bilibili": 0.5,
        "douyin": 0.4, "xiaohongshu": 0.3,
    },
    "tutorial": {
        "zhihu": 0.9, "wechat_mp": 0.85,
        "bilibili": 0.7, "xiaohongshu": 0.6,
        "douyin": 0.5, "toutiao": 0.4,
    },
    "seeding": {
        "xiaohongshu": 0.95, "douyin": 0.85,
        "bilibili": 0.6, "wechat_mp": 0.5,
        "toutiao": 0.4, "zhihu": 0.2,
    },
    "opinion": {
        "zhihu": 0.9, "wechat_mp": 0.8,
        "toutiao": 0.7, "bilibili": 0.6,
        "douyin": 0.5, "xiaohongshu": 0.4,
    },
}

# 平台发布建议数(按适配分降序取 Top-N)
PLATFORM_TOP_N = 3

# ============================================================
# 合规规则四类型+四态
# ============================================================

RULE_TYPES: tuple = (
    "prohibition",              # 禁止
    "conditional_prohibition",   # 条件禁止
    "mandatory_requirement",     # 强制要求
    "recommendation",            # 建议
)

COMPLIANCE_STATES: tuple = (
    "pass",             # 合规
    "review_required",  # 灰度边界→人工复核
    "block",            # 红线命中→禁止输出
    "legal_risk",       # 高危组合→法务介入
)

# ============================================================
# 酒类六红线(规划 §四——确定性词表)
# ============================================================

REDLINES: tuple = (
    "R1_induce",    # 诱导饮酒
    "R2_drunk",     # 醉酒危险形象
    "R3_health",    # 健康功效宣称
    "R4_minor",     # 未成年人关联
    "R5_absolute",  # 绝对化用语
    "R6_warning",   # 警示语缺失(结构校验)
)

REDLINE_LABELS: dict = {
    "R1_induce": "诱导饮酒",
    "R2_drunk": "醉酒危险形象",
    "R3_health": "健康功效宣称",
    "R4_minor": "未成年人关联",
    "R5_absolute": "绝对化用语",
    "R6_warning": "警示语缺失",
}

# 红线禁止模式(确定性子串匹配——
# 可执行的检测粒度)
REDLINE_PATTERNS: dict = {
    "R1_induce": (
        "劝酒", "拼酒", "干杯",
        "一口闷", "不醉不归", "必须喝",
        "不喝不是", "今晚必须醉",
        "姐妹们冲", "必须喝翻",
    ),
    "R2_drunk": (
        "醉酒", "瘫倒", "烂醉",
        "喝到不省人事", "酒后驾车",
        "酒驾",
    ),
    "R3_health": (
        "养生", "护肝", "助眠",
        "不上头", "根治", "壮阳",
        "疗效", "治疗功效", "药酒治病",
    ),
    "R4_minor": (
        "未成年人饮酒", "学生饮酒",
        "校园饮酒", "学生装",
    ),
    "R5_absolute": (
        "国家级", "最高级", "最佳",
        "国酒", "国宴指定", "第一品牌",
        "全网第一", "销量冠军",
    ),
}

# 法律高危子集(命中即 legal_risk——
# 刑事/行政高压线)
LEGAL_RISK_PATTERNS: tuple = (
    "酒后驾车", "酒驾",
)

# 高危组合(R3+R5 同命中 → legal_risk)
LEGAL_RISK_COMBO = ("R3_health", "R5_absolute")

# 灰度边界词(命中→review_required;
# "低度/适量"属口感/分量描述——安全域,
# 不入边界表[源文档测试矩阵])
BOUNDARY_PATTERNS: tuple = (
    "微醺", "节日小酌", "小酌",
)

# 安全港上下文标记(品鉴科普语境)
SAFE_CONTEXT_MARKERS: tuple = (
    "品鉴", "科普", "文化", "知识",
)

# 安全口感描述(不属 R3——防误杀)
SAFE_TASTE_WORDS: tuple = (
    "低度", "纯粮酿造", "口感柔和",
    "入口柔顺", "口感清爽",
)

# 酒类内容标记(触发 R6 警示语要求)
LIQUOR_MARKERS: tuple = (
    "酒", "白酒", "红酒", "威士忌",
    "果酒", "黄酒", "啤酒", "米酒",
    "酱香", "浓香", "清酒",
)

# 警示语规范(结构校验)
WARNING_TEXT = "过量饮酒有害健康"
WARNING_POSITION = "footer"
WARNING_MIN_FONT_SIZE = 12
WARNING_TEMPLATE = (
    f"\n\n——{WARNING_TEXT}——"
)

# 安全港条款公示(例外——防误杀)
SAFE_HARBORS: tuple = (
    "专业品鉴/文化科普 + 显著警示语 → 边界词豁免",
    "持证商家/预包装/正规渠道 → 电商侧合规",
    "口感描述(低度/纯粮酿造/口感柔和) → 不属健康功效",
)

# ============================================================
# 人设状态机(记忆与人设层)
# ============================================================

PERSONA_STATES: tuple = (
    "professional",   # 专业导师(平时)
    "observer",       # 理性观察者(热点)
    "companion",      # 贴心朋友(节日)
)

PERSONA_STATE_LABELS: dict = {
    "professional": "专业导师",
    "observer": "理性观察者",
    "companion": "贴心朋友",
}

# 平台人格档案种子(六平台——P1 播种)
PERSONA_SEEDS: list = [
    {
        "platform": "wechat_mp",
        "displayName": "竹映·公众号",
        "tone": "亲切、深度",
        "sentenceStyle": "长文结构+导读摘要",
        "emojiDensity": 1,
        "tagStyle": "无标签, 依赖标题",
        "defaultState": "professional",
    },
    {
        "platform": "douyin",
        "displayName": "竹映·抖音",
        "tone": "口语化、悬念",
        "sentenceStyle": "黄金 3 秒开头+互动引导",
        "emojiDensity": 1,
        "tagStyle": "话题标签 3-5 个",
        "defaultState": "professional",
    },
    {
        "platform": "xiaohongshu",
        "displayName": "竹映·小红书",
        "tone": "情绪化、体验感",
        "sentenceStyle": "短分段+感叹收尾",
        "emojiDensity": 3,
        "tagStyle": "标签 6-10 个",
        "defaultState": "companion",
    },
    {
        "platform": "zhihu",
        "displayName": "竹映·知乎",
        "tone": "严谨、客观",
        "sentenceStyle": "逻辑链+数据支撑+长文",
        "emojiDensity": 0,
        "tagStyle": "无标签, 依赖目录",
        "defaultState": "professional",
    },
    {
        "platform": "bilibili",
        "displayName": "竹映·B站",
        "tone": "年轻化、玩梗",
        "sentenceStyle": "弹幕互动引导+分段",
        "emojiDensity": 2,
        "tagStyle": "标签 3-5 个",
        "defaultState": "companion",
    },
    {
        "platform": "toutiao",
        "displayName": "竹映·头条",
        "tone": "资讯体、大众化",
        "sentenceStyle": "倒金字塔+标题规范",
        "emojiDensity": 0,
        "tagStyle": "话题标签 2-3 个",
        "defaultState": "observer",
    },
]

# ============================================================
# P2 内容适配管线(决策面——确定性模板)
# ============================================================

# 平台标题字符上限(结构校验)
TITLE_MAX_LEN: dict = {
    "wechat_mp": 64,
    "douyin": 55,
    "xiaohongshu": 20,
    "zhihu": 50,
    "bilibili": 80,
    "toutiao": 30,
}

# 意图×开头钩子(确定性选择)
HOOK_WORDS: dict = {
    "news": "1分钟看懂",
    "tutorial": "干货教程",
    "seeding": "真实测评",
    "opinion": "深度观点",
}

# 意图×品类词(B站标题用)
CATEGORY_WORDS: dict = {
    "news": "酒圈资讯",
    "tutorial": "品鉴教学",
    "seeding": "好物开箱",
    "opinion": "行业观察",
}

# 平台标题模板(确定性拼接——占位符
# {title}/{hook}/{emoji}/{exclaim}/
# {category}/{keypoint})
TITLE_TEMPLATES: dict = {
    "wechat_mp": "深度｜{title}",
    "douyin": "{title}｜{hook}",
    "xiaohongshu": "{emoji} {title}{exclaim}",
    "zhihu": "如何理性看待：{title}？",
    "bilibili": "【{category}】{title}",
    "toutiao": "{title}，{keypoint}",
}

# 摘要截取长度(确定性 digest)
DIGEST_LEN = 60

# 平台摘要模板
SUMMARY_TEMPLATES: dict = {
    "wechat_mp": "导读：{digest}",
    "douyin": "黄金3秒开头：{digest}",
    "xiaohongshu": "{digest}",
    "zhihu": "核心观点：{digest}",
    "bilibili": "本期看点：{digest}",
    "toutiao": "摘要：{digest}",
}

# 意图×基础标签域(标签拼接素材)
TAG_BASES: dict = {
    "news": ("酒类资讯", "行业动态"),
    "tutorial": ("品鉴教程", "入门指南"),
    "seeding": ("好物分享", "真实测评"),
    "opinion": ("行业观察", "理性讨论"),
}

# 平台标签上限(0=不产标签——
# 依赖标题/目录的形态)
TAG_LIMITS: dict = {
    "wechat_mp": 0,
    "douyin": 5,
    "xiaohongshu": 8,
    "zhihu": 0,
    "bilibili": 5,
    "toutiao": 3,
}

# 平台标签前缀
TAG_PREFIX: dict = {
    "douyin": "#",
    "xiaohongshu": "#",
    "toutiao": "#",
}

# Emoji 合规集(按 emojiDensity 消费;
# 不含碰杯/干杯类画面——R1/R2 合规)
EMOJI_SETS: dict = {
    "wechat_mp": (),
    "douyin": ("🔥",),
    "xiaohongshu": ("🍷", "✨", "🌿"),
    "zhihu": (),
    "bilibili": ("▶️",),
    "toutiao": (),
}

# 感叹收尾(小红书——情绪化)
EXCLAIM_MARK = "！"

# 人设状态×话术包(记忆人设层——
# 确定性模板, LLM 禁入)
TALKING_POINTS: dict = {
    "professional": {
        "opening": "从专业角度看，",
        "closing": "以上内容供参考，"
                   "欢迎交流。",
    },
    "observer": {
        "opening": "理性观察：",
        "closing": "欢迎理性讨论，"
                   "共同探讨。",
    },
    "companion": {
        "opening": "朋友们，",
        "closing": "咱们评论区见～",
    },
}

# ============================================================
# P3 发布编排与自愈域(执行与工具层)
# ============================================================

# 发布状态机(六态——shadowed 仅影子期)
PUBLISH_STATES: tuple = (
    "shadowed",         # 影子留痕(不派发)
    "awaiting_manual",  # B 档待人工操作
    "published",        # 已发布(直连/回执确认)
    "rejected",         # 平台驳回(负样本)
    "throttled",        # 平台限流(负样本)
    "failed",           # A 档直连失败(可自愈)
)

# B 档回执结果域(数据诚实铁律——
# 未登记视为未发布)
RECEIPT_RESULTS: tuple = (
    "published", "rejected", "throttled",
)

# 错误归因域(自愈决策依据)
ERROR_KINDS: tuple = (
    "content_violation",  # 内容违规(不可自动重试)
    "api_transient",      # 接口临时故障(指数退避)
    "auth_expired",       # 凭证缺失(换档建议)
    "quota_exceeded",     # 配额超限
    "unknown",
)

# 归因→处置/换档建议
TIER_ADVICE: dict = {
    "content_violation":
        "fix_content_readapt",
    "api_transient":
        "backoff_retry",
    "auth_expired":
        "configure_credentials_or_switch_B",
    "quota_exceeded":
        "backoff_retry",
    "unknown": "manual_inspect",
}

# 自愈重试上限+指数退避基秒
# (退避序列: 60→120→240→480)
MAX_PUBLISH_RETRY = 3
RETRY_BACKOFF_BASE = 60

# 单平台每日发布封顶(打扰保护)
DAILY_PUBLISH_CAP = 3

# 静默窗默认时段(北京时间——
# 夜间免打扰)
SILENCE_HOURS_DEFAULT: tuple = (
    23, 0, 1, 2, 3, 4, 5, 6,
)

# ============================================================
# P4 数据回流与学习进化域(进化与对齐层)
# ============================================================

# 指标类型域(四类——数据回流)
METRIC_TYPES: tuple = (
    "read", "like", "comment", "share",
)

# 审核结果域(平台回流——
# rejected/throttled 为负样本)
AUDIT_RESULTS: tuple = (
    "passed", "rejected", "throttled",
)

# 学习域(三类留痕)
LEARNING_KINDS: tuple = (
    "form_learning",      # 形式学习(互动率)
    "negative_feedback",  # 负样本(驳回/限流)
    "rule_reinforce",     # 规则强化(46号提案)
)

# 形式学习阈值
# (互动率=(like+comment+share)/read)
FORM_LEARNING_MIN_SAMPLES = 3
HIGH_ENGAGEMENT_LINE = 0.15
LOW_ENGAGEMENT_LINE = 0.03

# 负反馈强化触发(同平台连续
# 驳回/限流次数——达线提 46号)
NEGATIVE_TRIGGER_CONSECUTIVE = 2

# 46号审批链档案(进化不自动生效
# 铁律——提案 pending 人工签核)
GOVERNANCE_SCORER_ID = "nexus_publishing"

# ============================================================
# P6 发布后复盘域(运营与对齐层)
# ============================================================

# 复盘范围域(单篇/分组)
RETRO_SCOPES: tuple = (
    "single",    # 单篇发布复盘
    "group",     # 平台×意图分组复盘
)

# 复盘结论域(确定性阈值——复用 P4
# 互动率线 HIGH/LOW_ENGAGEMENT_LINE)
RETRO_VERDICTS: tuple = (
    "effective",     # 高效传播(≥HIGH)
    "neutral",       # 中性观察(LOW–HIGH)
    "ineffective",   # 低效传播(<LOW)
    "blocked",       # 传播受阻(驳回/限流)
    "pending_data",  # 数据未回流
)

RETRO_VERDICT_LABELS: dict = {
    "effective": "高效传播",
    "neutral": "中性观察",
    "ineffective": "低效传播",
    "blocked": "传播受阻",
    "pending_data": "数据未回流",
}

# 复盘建议码域(确定性建议——LLM 禁入,
# 模板拼接+数字插值)
RETRO_ADVICE_CODES: tuple = (
    "reinforce_form",            # 强化形式组合
    "keep_observing",            # 继续观察
    "adjust_form_ab",            # 形式 A/B 调整
    "adjust_content_direction",  # 内容方向调整(驳回)
    "adjust_timing_frequency",   # 时机/频次调整(限流)
    "reflow_metrics",            # 登记数据回流
    "scale_up_combination",      # 组合放大(分组)
    "maintain_observation",      # 维持观察(分组)
    "change_form_strategy",      # 形式变革(分组)
)

# 分组复盘最小指标样本(策略判定线——
# 不足时附加"继续积累"建议)
RETRO_GROUP_MIN_PUBLISHED = 3

# ============================================================
# 合规规则库种子(对象化 Schema——P1 播种)
# ============================================================

# 通用六红线(all_platforms)+平台专属
RULE_SEEDS: list = [
    {
        "platform": "all_platforms",
        "ruleType": "prohibition",
        "redline": "R1_induce",
        "content": ("酒类内容不得出现诱导、"
                    "怂恿饮酒或宣传无节制"
                    "饮酒的话术/画面"),
        "legalBasis": "《广告法》第23条",
        "confidence": 0.95,
    },
    {
        "platform": "all_platforms",
        "ruleType": "prohibition",
        "redline": "R2_drunk",
        "content": ("不得出现醉酒、瘫倒、"
                    "酒后驾车等违法违规形象"),
        "legalBasis": ("《广告法》第23条+"
                       "各平台社区规范"),
        "confidence": 0.95,
    },
    {
        "platform": "all_platforms",
        "ruleType": "prohibition",
        "redline": "R3_health",
        "content": ("严禁将酒与医疗、保健、"
                    "治疗功效关联(护肝/助眠/"
                    "不上头等)"),
        "legalBasis": ("《广告法》+"
                       "《食品安全法》"),
        "confidence": 0.93,
    },
    {
        "platform": "all_platforms",
        "ruleType": "prohibition",
        "redline": "R4_minor",
        "content": ("不得出现未成年人饮酒"
                    "场景/学生装/校园关联"),
        "legalBasis": "《未成年人保护法》",
        "confidence": 0.95,
    },
    {
        "platform": "all_platforms",
        "ruleType": "prohibition",
        "redline": "R5_absolute",
        "content": ("不得使用国家级/最高级/"
                    "最佳/国酒等绝对化用语"),
        "legalBasis": "《广告法》第9条",
        "confidence": 0.95,
    },
    {
        "platform": "all_platforms",
        "ruleType": "mandatory_requirement",
        "redline": "R6_warning",
        "content": ("酒类内容必须在文末显著"
                    "位置标注'过量饮酒"
                    "有害健康'警示语"),
        "legalBasis": "各平台酒类发布规范",
        "confidence": 0.92,
    },
    {
        "platform": "xiaohongshu",
        "ruleType": "conditional_prohibition",
        "redline": "R3_health",
        "content": ("美妆类'使用前 vs 使用后'"
                    "对比图范式——酒类疗效"
                    "对比图(饮酒前后)同类禁止"),
        "legalBasis": ("小红书社区规范"
                       "美妆类目"),
        "confidence": 0.85,
    },
    {
        "platform": "douyin",
        "ruleType": "prohibition",
        "redline": "R2_drunk",
        "content": ("视频前 3 秒不得出现"
                    "饮酒动作特写/碰杯画面"),
        "legalBasis": "抖音视频审核规则",
        "confidence": 0.85,
    },
    {
        "platform": "wechat_mp",
        "ruleType": "mandatory_requirement",
        "redline": "R5_absolute",
        "content": ("商业推广内容须标注"
                    "'广告'标识; 酒类营销"
                    "须显著展示警示语"),
        "legalBasis": ("《互联网广告管理"
                       "办法》"),
        "confidence": 0.9,
    },
    {
        "platform": "zhihu",
        "ruleType": "recommendation",
        "redline": "",
        "content": ("数据引用建议注明来源; "
                    "长文建议带目录锚点"),
        "legalBasis": "知乎社区规范",
        "confidence": 0.7,
    },
]

# ============================================================
# P5 预定义(元认知——宪法级先行封闭)
# ============================================================

# 漂移三信号(发布异常)
DRIFT_SIGNALS: tuple = (
    "publish_anomaly",    # 发布总量异常(轰炸)
    "rejection_anomaly",  # 驳回率骤升(形式失效)
    "review_anomaly",    # 复核率异常(灰度失控)
)

DRIFT_THRESHOLDS: dict = {
    "publishAnomaly": 30,   # 全站当日发布≥30
    "rejectionDrop": 0.3,   # 驳回率>30%
    "reviewAnomaly": 0.4,   # 复核率>40%
    "minSamples": 5,        # 最小样本
}

# 冻结规则(信号数≥2 → 自动冻结)
IMMUNITY_FREEZE_RULES: dict = {
    "driftSignalCount": 2,
}

IMMUNITY_STATES: tuple = ("active", "frozen")

# 红队四向量(发布域)
REDTEAM_VECTORS: tuple = (
    "RT-01",  # 红线穿透(违规内容混入发布)
    "RT-02",  # 频次轰炸(封顶熔断失效)
    "RT-03",  # 越权直发(B 档绕过人工确认)
    "RT-04",  # 回执伪造(数据诚实铁律)
)

# 进化日志六类(学习进化留痕)
EVOLUTION_LOG_KINDS: tuple = (
    "form_learning",       # 形式学习
    "negative_feedback",   # 负样本(驳回/限流)
    "rule_reinforce",     # 规则强化
    "drift_detected",     # 漂移
    "freeze", "unfreeze",
    "redteam",
)


# ============================================================
# 启动自检(宪法级)
# ============================================================

def _validate_registry() -> None:
    """注册表封闭自检(导入即验——
    RuntimeError 宪法级)"""
    # ① 模式四档封闭
    if set(MODE_VALUES) != {
            "off", "shadow", "assist", "full"}:
        raise RuntimeError(
            "nexus74 模式四档域非法")
    # ② 平台域封闭+名称/分级闭合
    if set(PLATFORMS) != {
            "wechat_mp", "douyin",
            "xiaohongshu", "zhihu",
            "bilibili", "toutiao"}:
        raise RuntimeError(
            "nexus74 平台域非法")
    if (set(PLATFORM_NAMES)
            != set(PLATFORMS)
            or set(ADAPTER_TIERS)
            != set(PLATFORMS)):
        raise RuntimeError(
            "nexus74 平台名称/分级不闭合")
    if not all(t in ("A", "B", "C")
               for t in
               ADAPTER_TIERS.values()):
        raise RuntimeError(
            "nexus74 适配器分级域外")
    # ③ 意图域封闭+矩阵行完备
    if set(INTENT_TYPES) != {
            "news", "tutorial",
            "seeding", "opinion"}:
        raise RuntimeError(
            "nexus74 意图域非法")
    if set(INTENT_LABELS) \
            != set(INTENT_TYPES):
        raise RuntimeError(
            "nexus74 意图标签不闭合")
    if set(INTENT_PLATFORM_SCORES) \
            != set(INTENT_TYPES):
        raise RuntimeError(
            "nexus74 矩阵意图行缺失")
    for intent, scores in \
            INTENT_PLATFORM_SCORES.items():
        if set(scores) != set(PLATFORMS):
            raise RuntimeError(
                "nexus74 矩阵平台列缺失 "
                f"({intent})")
        for v in scores.values():
            if not (0 <= v <= 1):
                raise RuntimeError(
                    "nexus74 矩阵分值域外")
    # ④ 规则类型/合规四态封闭
    if set(RULE_TYPES) != {
            "prohibition",
            "conditional_prohibition",
            "mandatory_requirement",
            "recommendation"}:
        raise RuntimeError(
            "nexus74 规则类型域非法")
    if set(COMPLIANCE_STATES) != {
            "pass", "review_required",
            "block", "legal_risk"}:
        raise RuntimeError(
            "nexus74 合规四态域非法")
    # ⑤ 六红线封闭+词表非空
    if set(REDLINES) != {
            "R1_induce", "R2_drunk",
            "R3_health", "R4_minor",
            "R5_absolute", "R6_warning"}:
        raise RuntimeError(
            "nexus74 六红线域非法")
    if (set(REDLINE_LABELS)
            != set(REDLINES)):
        raise RuntimeError(
            "nexus74 红线标签不闭合")
    if set(REDLINE_PATTERNS) != set(
            REDLINES) - {"R6_warning"}:
        raise RuntimeError(
            "nexus74 红线词表键不闭合")
    for redline, patterns in \
            REDLINE_PATTERNS.items():
        if not patterns:
            raise RuntimeError(
                "nexus74 红线词表空 "
                f"({redline})")
    # ⑥ 法律高危子集归属 R2
    if not set(LEGAL_RISK_PATTERNS) \
            <= set(
                REDLINE_PATTERNS[
                    "R2_drunk"]):
        raise RuntimeError(
            "nexus74 法律高危子集"
            "不属于 R2 词表")
    # ⑦ 人设状态域封闭+种子闭合
    if set(PERSONA_STATES) != {
            "professional", "observer",
            "companion"}:
        raise RuntimeError(
            "nexus74 人设状态域非法")
    if len(PERSONA_SEEDS) \
            != len(PLATFORMS):
        raise RuntimeError(
            "nexus74 人格种子数"
            "与平台数不闭合")
    for seed in PERSONA_SEEDS:
        if seed["platform"] \
                not in PLATFORMS:
            raise RuntimeError(
                "nexus74 人格种子"
                "平台域外")
        if seed["defaultState"] \
                not in PERSONA_STATES:
            raise RuntimeError(
                "nexus74 人格种子"
                "状态域外")
    # ⑧ P2 适配模板域校验
    for domain in (TITLE_MAX_LEN,
                   TITLE_TEMPLATES,
                   SUMMARY_TEMPLATES,
                   TAG_LIMITS, EMOJI_SETS):
        if set(domain) != set(PLATFORMS):
            raise RuntimeError(
                "nexus74 适配模板平台列"
                "不闭合")
    if (set(HOOK_WORDS)
            != set(INTENT_TYPES)
            or set(CATEGORY_WORDS)
            != set(INTENT_TYPES)
            or set(TAG_BASES)
            != set(INTENT_TYPES)):
        raise RuntimeError(
            "nexus74 意图素材行缺失")
    for t in TITLE_MAX_LEN.values():
        if not (0 < t <= 100):
            raise RuntimeError(
                "nexus74 标题上限域外")
    if set(TALKING_POINTS) \
            != set(PERSONA_STATES):
        raise RuntimeError(
            "nexus74 话术包状态不闭合")
    # ⑨ 规则种子域校验
    for seed in RULE_SEEDS:
        if seed["ruleType"] \
                not in RULE_TYPES:
            raise RuntimeError(
                "nexus74 规则种子"
                "类型域外")
        if seed.get("redline") \
                and seed["redline"] \
                not in REDLINES:
            raise RuntimeError(
                "nexus74 规则种子"
                "红线域外")
        if not (0 < seed["confidence"]
                <= 1):
            raise RuntimeError(
                "nexus74 规则种子"
                "置信度域外")
    # ⑩ P3 发布编排域校验
    if set(PUBLISH_STATES) != {
            "shadowed", "awaiting_manual",
            "published", "rejected",
            "throttled", "failed"}:
        raise RuntimeError(
            "nexus74 发布状态机域非法")
    if set(RECEIPT_RESULTS) != {
            "published", "rejected",
            "throttled"}:
        raise RuntimeError(
            "nexus74 回执结果域非法")
    if set(ERROR_KINDS) != {
            "content_violation",
            "api_transient",
            "auth_expired",
            "quota_exceeded",
            "unknown"}:
        raise RuntimeError(
            "nexus74 错误归因域非法")
    if set(TIER_ADVICE) \
            != set(ERROR_KINDS):
        raise RuntimeError(
            "nexus74 处置建议与归因"
            "不闭合")
    if not (0 < MAX_PUBLISH_RETRY
            <= 5):
        raise RuntimeError(
            "nexus74 重试上限域外")
    if RETRY_BACKOFF_BASE <= 0:
        raise RuntimeError(
            "nexus74 退避基秒域外")
    if DAILY_PUBLISH_CAP <= 0:
        raise RuntimeError(
            "nexus74 每日封顶域外")
    if not (set(SILENCE_HOURS_DEFAULT)
            <= set(range(24))):
        raise RuntimeError(
            "nexus74 静默时段域外")
    # ⑪ P4 学习进化域校验
    if set(METRIC_TYPES) != {
            "read", "like",
            "comment", "share"}:
        raise RuntimeError(
            "nexus74 指标类型域非法")
    if set(AUDIT_RESULTS) != {
            "passed", "rejected",
            "throttled"}:
        raise RuntimeError(
            "nexus74 审核结果域非法")
    if set(LEARNING_KINDS) != {
            "form_learning",
            "negative_feedback",
            "rule_reinforce"}:
        raise RuntimeError(
            "nexus74 学习域非法")
    if not (1 <= FORM_LEARNING_MIN_SAMPLES
            <= 10):
        raise RuntimeError(
            "nexus74 形式学习最小"
            "样本域外")
    if not (0 < LOW_ENGAGEMENT_LINE
            < HIGH_ENGAGEMENT_LINE < 1):
        raise RuntimeError(
            "nexus74 互动率阈值"
            "单调性非法")
    if NEGATIVE_TRIGGER_CONSECUTIVE < 2:
        raise RuntimeError(
            "nexus74 负反馈触发线域外")
    # ⑫ P5 预定义域封闭
    if set(DRIFT_SIGNALS) != {
            "publish_anomaly",
            "rejection_anomaly",
            "review_anomaly"}:
        raise RuntimeError(
            "nexus74 漂移三信号域非法")
    if set(REDTEAM_VECTORS) != {
            "RT-01", "RT-02",
            "RT-03", "RT-04"}:
        raise RuntimeError(
            "nexus74 红队向量域非法")
    if set(EVOLUTION_LOG_KINDS) != {
            "form_learning",
            "negative_feedback",
            "rule_reinforce",
            "drift_detected",
            "freeze", "unfreeze",
            "redteam"}:
        raise RuntimeError(
            "nexus74 进化日志域非法")
    # ⑬ P6 复盘域校验
    if set(RETRO_SCOPES) != {
            "single", "group"}:
        raise RuntimeError(
            "nexus74 复盘范围域非法")
    if set(RETRO_VERDICTS) != {
            "effective", "neutral",
            "ineffective", "blocked",
            "pending_data"}:
        raise RuntimeError(
            "nexus74 复盘结论域非法")
    if set(RETRO_VERDICT_LABELS) \
            != set(RETRO_VERDICTS):
        raise RuntimeError(
            "nexus74 复盘结论标签不闭合")
    if set(RETRO_ADVICE_CODES) != {
            "reinforce_form",
            "keep_observing",
            "adjust_form_ab",
            "adjust_content_direction",
            "adjust_timing_frequency",
            "reflow_metrics",
            "scale_up_combination",
            "maintain_observation",
            "change_form_strategy"}:
        raise RuntimeError(
            "nexus74 复盘建议码域非法")
    if not (1 <= RETRO_GROUP_MIN_PUBLISHED
            <= 10):
        raise RuntimeError(
            "nexus74 分组样本线域外")
    logger.info(
        "nexus74_registry_validated "
        "platforms=%s rules=%s "
        "personas=%s redlines=%s",
        len(PLATFORMS), len(RULE_SEEDS),
        len(PERSONA_SEEDS), len(REDLINES))
