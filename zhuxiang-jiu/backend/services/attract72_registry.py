"""72号·AI智能自动引流大模型 增长认知注册表
(attract72_registry, P1)

规划(docs/72号_AI智能自动引流大模型_创新规划方案.md
§三 感知层/§六):
    模式四档(ATTRACT72_MODE off/shadow/
    assist/full——引流域不碰资金, full 档
    首次开放, 受 L1 白名单约束)
    +渠道人格域(三类型+新晋型)
    +粉丝层级域(博主 T0-T3/会员 M0-M2)
    +意图快照域(意图标签/场景标签/犹豫
    信号/情绪词表——确定性, LLM 禁入)
    +外部信号域(节日/雷达事件/平台规则
    三类, 雷达 L1/L2 只读消费铁律)

设计(71号 pay71_registry 封闭注册表范式平移):
    - 封闭注册: 可断言/可测试/启动自检
    - 72号=增长因果认知中枢, 消费 40号
    P7 雷达事件/attract v1.0 归因/
    traffic 博主/promotion 会员数据
    作为输入, 永不修改源数据(叠加铁律)
    - 意图快照=词表密度(确定性公式),
      LLM 禁入判定链铁律不变

启动自检 _validate_registry()(RuntimeError
宪法级):
    - 模式四档封闭(引流域首创 full)
    - 人格域封闭+层级阈值单调
    - 词表域非空且互不重叠(情绪正负
      互斥——同词不可双计)
    - 信号域封闭+雷达消费档位合法
    - 节日日历权重域内+日期合法
"""

import logging
import os

logger = logging.getLogger("attract72_registry")

MODEL_VERSION = "v1-attract72-registry"

DEFAULT_MODE = "off"

# 四档(引流域不碰资金——full 首次开放,
# P6 受 L1 白名单约束)
MODE_VALUES = ("off", "shadow", "assist", "full")


def current_mode() -> str:
    """模块开关(ATTRACT72_MODE, 默认 off——
    off=仅观测面; shadow=影子学习期(决策
    只留痕); assist=辅助期(建议注入人工
    确认位); full=低风险域自主执行(L1
    白名单内)"""
    mode = os.environ.get("ATTRACT72_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


def is_kill() -> bool:
    """紧急制动(ATTRACT72_KILL——秒级冻结
    一切决策面, 退回只读基线; 对齐
    PAY71_KILL/QR70_KILL 惯例)"""
    return os.environ.get("ATTRACT72_KILL") == "1"


# ============================================================
# P1 渠道人格域(感知层——确定性分类)
# ============================================================

# 画像主体域(封闭)
SUBJECT_TYPES = (
    "member",       # 会员(矩阵码 owner)
    "influencer",   # 博主(KOL)
)

# 人格类型域(封闭——品鉴型/分享型/
# 优惠敏感型/新晋型[样本不足])
PERSONA_TYPES = (
    "connoisseur",      # 专业品鉴型(高客单
                        # 转化)
    "sharer",           # 生活分享型(高转化率)
    "bargain_hunter",   # 优惠敏感型(流量型)
    "newcomer",         # 新晋型(样本不足)
)

# 样本下限(点击数——低于则 newcomer)
PERSONA_MIN_SAMPLES = 5

# 品鉴型判定: 平均客单(元)≥此值且至少 1 单
CONNOISSEUR_ORDER_AMOUNT = 300.0

# 分享型判定: 转化率(下单/点击)≥此值
SHARER_CONVERSION_LINE = 0.15

# 置信度满样本数(点击数≥此值→confidence=1.0)
CONFIDENCE_FULL_SAMPLES = 50

# 信任分公式(确定性): 30 + 40×min(1,下单率
# /0.5) + 30×min(1,转化率/0.2), 上限 100
TRUST_BASE = 30.0
TRUST_ORDER_WEIGHT = 40.0
TRUST_ORDER_RATE_CAP = 0.5
TRUST_CONVERSION_WEIGHT = 30.0
TRUST_CONVERSION_CAP = 0.2
TRUST_CEIL = 100.0

# 画像历史滚动窗口(变更留痕最多 N 条)
PERSONA_HISTORY_WINDOW = 5

# ============================================================
# 粉丝层级域(博主按粉丝数/会员按注册归因数)
# ============================================================

# 博主粉丝层级(封闭, 阈值单调递减)
FOLLOWER_TIERS_INFLUENCER = ("T0", "T1", "T2", "T3")
FOLLOWER_TIER_LINES = (
    ("T0", 1_000_000),   # 头部
    ("T1", 100_000),     # 腰部
    ("T2", 10_000),      # 尾部
    ("T3", 0),           # 素人
)

# 会员层级(封闭, 按注册归因数, 阈值单调递减)
FOLLOWER_TIERS_MEMBER = ("M0", "M1", "M2")
MEMBER_TIER_LINES = (
    ("M2", 50),   # 邀新达人
    ("M1", 10),   # 活跃邀请
    ("M0", 0),    # 初期
)


def tier_for_followers(follower_count: int) -> str:
    """粉丝数 → 博主层级(确定性查表)"""
    for tier, line in FOLLOWER_TIER_LINES:
        if (follower_count or 0) >= line:
            return tier
    return "T3"


def tier_for_members(registered_count: int) -> str:
    """注册归因数 → 会员层级(确定性查表)"""
    for tier, line in MEMBER_TIER_LINES:
        if (registered_count or 0) >= line:
            return tier
    return "M0"


# ============================================================
# P1 意图快照域(感知层——词表密度确定性,
# LLM 禁入判定链铁律)
# ============================================================

# 情绪词表(正负互斥——同词不可双计)
EMOTION_POSITIVE_WORDS = (
    "好奇", "心动", "喜欢", "想要", "期待",
    "种草", "真香", "值得",
)
EMOTION_NEGATIVE_WORDS = (
    "太贵", "犹豫", "纠结", "再看看", "算了",
    "不值", "劝退", "失望",
)

# 场景标签词表(封闭域: 场景→触发词)
SCENE_TAGS = ("wedding", "gift", "party",
              "self_drink", "collection")
SCENE_WORDS: dict = {
    "wedding": ("婚宴", "喜酒", "婚礼", "结婚",
                "回门宴"),
    "gift": ("送礼", "送人", "长辈", "礼物",
             "礼盒"),
    "party": ("聚会", "宴请", "家宴", "朋友餐"),
    "self_drink": ("自己喝", "口粮", "日常",
                   "小酌"),
    "collection": ("收藏", "珍藏", "老酒", "陈年"),
}

# 犹豫信号词表(封闭域: 信号→触发词)
HESITATION_SIGNALS = ("price_hesitation",
                      "decision_delay")
HESITATION_WORDS: dict = {
    "price_hesitation": ("太贵", "便宜", "优惠",
                         "划算", "比价", "打折",
                         "再看看"),
    "decision_delay": ("纠结", "犹豫", "考虑",
                       "想想", "等等"),
}

# 意图标签词表(封闭域: 意图→触发词)
INTENT_TAGS = ("taste_curious", "price_sensitive",
               "gift_intent", "collect_intent",
               "high_engagement", "bounce_risk")
INTENT_WORDS: dict = {
    "taste_curious": ("工艺", "口感", "竹香",
                      "纯粮", "怎么酿", "发酵",
                      "绵甜"),
    "price_sensitive": ("价格", "多少钱", "贵",
                        "优惠", "便宜", "划算"),
    "gift_intent": ("送礼", "送人", "长辈",
                    "礼物", "礼盒"),
    "collect_intent": ("收藏", "珍藏", "老酒",
                       "陈年", "限量"),
}

# 停留时长阈值(秒——高参与/跳出风险)
DWELL_HIGH_SECONDS = 120.0
DWELL_BOUNCE_SECONDS = 15.0

# 指纹截断长度(SHA256 前 16 位——脱敏,
# 不含 PII 铁律)
FINGERPRINT_LENGTH = 16


def fingerprint_of(user_agent: str) -> str:
    """UA 哈希脱敏指纹(P1 enrich 同算法——
    v1.0 挂点(auth 注册/attract 下单)与点击
    侧指纹一致性全靠此唯一实现)"""
    import hashlib
    return hashlib.sha256(
        (user_agent or "").encode(
            "utf-8", "replace")).hexdigest()[
        :FINGERPRINT_LENGTH]

# ============================================================
# P1 外部信号域(感知层——雷达只读消费铁律)
# ============================================================

# 信号类型域(封闭)
SIGNAL_TYPES = (
    "festival",       # 节日日历(确定性种子)
    "radar_event",    # 雷达事件(40号 P7
                      # L1/L2 只读消费)
    "platform_rule",  # 平台规则(P3 预算
                      # 信号注入预留)
)

# 雷达事件消费档位(封闭——仅 L1/L2,
# L4 风险屏蔽永不消费)
RADAR_CONSUME_GRADES = ("L1", "L2")

# 雷达事件价值分下限(valueScore<此值
# 不入信号流)
RADAR_MIN_VALUE = 30.0

# 雷达类别→影响渠道映射(封闭)
RADAR_CATEGORY_CHANNELS: dict = {
    "finance": ("douyin", "xiaohongshu"),
    "history": ("xiaohongshu", "bilibili",
                "seo"),
    "current": ("douyin", "wechat",
                "xiaohongshu"),
    "politics": ("douyin",),
    "military": ("douyin", "bilibili"),
}

# 雷达事件消费上限(单次摄取——观测口径)
RADAR_INGEST_LIMIT = 50

# 节日前瞻窗口(天——窗口内节日入信号流)
FESTIVAL_LOOKAHEAD_DAYS = 30

# 影响渠道全量口径(attract v1.0 CHANNEL_SEEDS
# 消费——72号不建第二套渠道字典)
def _attract_channel_seeds() -> tuple:
    """渠道域(attract v1.0 CHANNEL_SEEDS
    只读消费)"""
    from repositories.attract_repository import (
        CHANNEL_SEEDS,
    )
    return tuple(CHANNEL_SEEDS)


# 节日日历(确定性种子: 名称/月/日/标签/权重
# ——农历节日按公历近似, 演示口径)
FESTIVAL_CALENDAR = (
    # (name, month, day, label, weight)
    ("new_year", 1, 1, "元旦", 0.5),
    ("spring_festival", 2, 17, "春节", 1.0),
    ("valentines", 2, 14, "情人节", 0.4),
    ("labour_day", 5, 1, "劳动节", 0.4),
    ("dragon_boat", 6, 19, "端午", 0.6),
    ("mid_autumn", 9, 25, "中秋", 0.9),
    ("national_day", 10, 1, "国庆", 0.8),
    ("double_eleven", 11, 11, "双11", 0.7),
    ("new_year_eve", 12, 31, "跨年", 0.5),
)


# ============================================================
# P2 因果认知域(规划 §四 4.3——反事实对照
# 确定性公式, LLM 禁入判定链)
# ============================================================

# 因果维度域(封闭三维度)
CAUSAL_DIMENSIONS = (
    "content_element",   # 内容要素(场景/意图
                        # 标签——P1 快照)
    "channel_feature",   # 渠道特征(渠道/码类型)
    "timing",            # 时段(点击时间分桶)
)

# 时段分桶(封闭——点击 at 小时 → 桶,
# 阈值单调递增)
TIMING_BUCKETS = (
    ("late_night", (0, 6)),     # 深夜 0-6
    ("morning", (6, 12)),        # 上午 6-12
    ("afternoon", (12, 18)),     # 下午 12-18
    ("evening", (18, 24)),       # 晚间 18-24
)


def bucket_for_hour(hour: int) -> str:
    """小时 → 时段桶(确定性查表)"""
    for name, (lo, hi) in TIMING_BUCKETS:
        if lo <= (hour % 24) < hi:
            return name
    return "morning"


# 因素前缀域(封闭——因素编码:
# scene:{tag}/intent:{tag}/channel:{ch}/
# code_type:{t}/timing:{bucket})
CAUSAL_FACTOR_PREFIXES = (
    "scene", "intent", "channel",
    "code_type", "timing",
)

# 效应类型域(封闭)
CAUSAL_EFFECT_TYPES = (
    "driver",    # 驱动因子(score≥线)
    "loss",      # 流失因子(score≤-线)
    "neutral",   # 中性(不足线/样本不足)
)

# 反事实效应线(|score|≥此值 → driver/loss;
# 转化率差 10 个百分点)
CAUSAL_EFFECT_LINE = 0.10

# 组最小样本(点击数——任一组低于 →
# neutral+pending, 不入验证)
CAUSAL_MIN_SAMPLES = 5

# 置信度满样本(较小组点击数达此值
# → confidence=1.0)
CAUSAL_CONF_SAMPLES = 20

# 洞察验证置信线(≥此值 → verified,
# 可结晶)
INSIGHT_VERIFY_CONFIDENCE = 0.5

# 洞察状态机(封闭)
INSIGHT_STATUSES = (
    "pending",    # 观测中(样本/置信不足)
    "verified",   # 已验证(可结晶)
    "archived",   # 归档(因子消失)
)

# 定律种类域(封闭——law=增长定律/
# anti=反知识: 验证无效或负向的
# 要素组合结晶, 防重复试错)
LAW_KINDS = ("law", "anti")

# 定律状态机(封闭——结晶走 46号审批;
# active 由发布显式生效; expired 由
# 连续未复现衰减触发)
LAW_STATUSES = (
    "draft",      # 草稿(46号未提交)
    "submitted",  # 已提交 46号(pending)
    "active",     # 已发布(46号 approved
                  # +显式 publish)
    "expired",    # 已过期(连续未复现)
    "rejected",   # 46号驳回留痕
)

# 定律衰减上限(连续 N 次因果运行
# 反向/消失 → expired)
LAW_DECAY_MAX = 2

# 定律边界口径(确定性格式:
# 样本 n1/n2·置信度 c·渠道全域)
LAW_BOUNDARY = ("样本 {na}/{nb}·置信度 {conf}"
                "·渠道全域")

# 46号治理档案(第 45 批——定律
# 结晶审批链)
GOVERNANCE_SCORER_ID = \
    "growth_intelligence"

# 57号知识库同步口径(定律 question
# 模板——数字 100% 查询层插值)
KB_LAW_QUESTION = ("72号增长定律:"
                   "{factor}({dimension})")
KB_LAW_CATEGORY = "growth_law"

# 自然语言查询路由域(封闭——确定性
# 关键词路由, LLM 仅组织文案)
NL_QUERY_ROUTES = (
    "channel_roi",   # 渠道 ROI/转化率
    "drivers",       # 驱动/流失因子
    "laws",          # 增长定律台账
)

# NL 路由关键词表(封闭映射——
# 按序匹配, 先中先得)
NL_ROUTE_KEYWORDS: dict = {
    "channel_roi": ("roi", "渠道", "转化率",
                    "抖音", "小红书", "微信",
                    "快手", "哔哩", "b站",
                    "淘宝", "seo"),
    "laws": ("定律", "规律", "知识"),
    "drivers": ("驱动", "流失", "为什么",
                "下滑", "下降", "提升"),
}

# 渠道中文别名表(NL 渠道定向匹配——
# 键域=attract v1.0 CHANNEL_SEEDS 消费
# 铁律, 启动自检闭合)
CHANNEL_ALIASES: dict = {
    "douyin": ("抖音", "douyin"),
    "kuaishou": ("快手", "kuaishou"),
    "wechat": ("微信", "wechat"),
    "xiaohongshu": ("小红书", "xhs",
                    "xiaohongshu"),
    "bilibili": ("哔哩", "b站", "bilibili"),
    "taobao": ("淘宝", "taobao"),
    "direct": ("直接访问", "direct"),
    "seo": ("seo", "搜索"),
}


# ============================================================
# P3 预算预判域(规划 §四 4.5——预判式预算
# 博弈器, 确定性公式, LLM 禁入)
# ============================================================

# 预分配窗口(小时——未来 72h)
FORECAST_WINDOW_HOURS = 72

# 月窗占比(72h/720h——月度池的窗口切片;
# 池总量锁定铁律: 此消彼长不新增总成本)
POOL_WINDOW_SHARE = 0.1

# 偏差重博弈阈值(实际消耗 vs 预分配
# 期望, >15% 触发——域内自动)
DEVIATION_THRESHOLD = 0.15

# 探索基金浮动域(5%-15%——新渠道
# 样本不足驱动上浮)
EXPLORATION_MIN = 0.05
EXPLORATION_MAX = 0.15
EXPLORATION_STEP = 0.05
EXPLORATION_LOW_SAMPLES = 5

# 渠道信号提升上限(雷达权重和封顶)
SIGNAL_LIFT_CAP = 0.5

# 定律助推上限(±封顶)
LAW_BOOST_CAP = 0.5

# 建议系数步长(对齐 v1.0 REBALANCE_STEP)
SUGGESTED_RATE_STEP = 0.1

# 系数置信度满样本(点击数)
RATE_CONF_SAMPLES = 20

# 预分配状态机(封闭——active 唯一;
# rebalanced=偏差重博弈退役留痕)
FORECAST_STATUSES = (
    "active",       # 生效(当前消费口径)
    "superseded",   # 被新生成取代
    "rebalanced",   # 偏差重博弈退役
)

# 三层时间尺度(封闭——分钟/日/月)
TIME_SCALES = (
    "minute",   # 分钟级(热点追投窗)
    "day",      # 日级(72h 渠道调配)
    "month",    # 月级(战略储备)
)

# 46号预算治理档案(第 46 批——奖励
# 系数变更建议书审批链)
BUDGET_SCORER_ID = "growth_budget"


# ============================================================
# P4 短链记忆域(规划 §四 4.2——ctx 上下文/
# 动态落地页/跨会话记忆/反作弊)
# ============================================================

# 落地页变体域(封闭——画像×变体匹配分查表)
LANDING_VARIANTS = (
    "trust_first",     # 新用户强化信任
    "benefit_first",   # 老用户直显权益
    "default",         # 兜底(v1.0 一致)
)

# 反作弊状态机(封闭)
ANTIFRAUD_STATES = (
    "normal",      # 正常
    "isolated",    # 已隔离(分布异常)
    "released",    # 人工解冻
)

# 指纹频次线(窗口内点击超此值→分布异常
# 候选隔离)
FINGERPRINT_RATE_LIMIT = 50

# 隔离窗(秒——隔离后此窗口内拒服务)
FINGERPRINT_ISOLATE_SECONDS = 3600.0

# ctx 有效期(秒——超期降级 default)
CTX_TTL_SECONDS = 3600.0


# ============================================================
# P5 热点卡位域(规划 §四 4.6——雷达联动
# 四维势能/卡位决策/结果回流)
# ============================================================

# 四维势能权重(和为 1)
POTENTIAL_WEIGHT_TIMELINESS = 0.25
POTENTIAL_WEIGHT_RELEVANCE = 0.25
POTENTIAL_WEIGHT_SAFETY = 0.30
POTENTIAL_WEIGHT_CONVERSION = 0.20

# 热点安全下限(安全性 < 此值 → 高风险自动拒追,
# "热点安全硬编码"铁律)
HOTSPOT_SAFETY_FLOOR = 0.5

# 追投势能阈值(综合势能 > 此值 → chase)
HOTSPOT_CHASE_THRESHOLD = 0.6

# 热点裁决域(封闭)
HOTSPOT_VERDICTS = (
    "chase",     # 卡位追投
    "observe",   # 观察
    "reject",    # 拒追(高风险/低势能)
)

# 卡位决策状态机(封闭)
HOTSPOT_DECISION_STATUSES = (
    "pending",    # shadow 落档
    "confirmed",  # assist 人工确认
    "executed",   # 已推送执行
    "closed",     # 结果回流闭环
)


# ============================================================
# P6 元认知与治理域(规划 §五 5.3/§六——
# 健康度三指标/红队四向量/沙箱实验/
# L1 白名单/转段)
# ============================================================

# 健康度三指标阈值(封闭——任一越界
# → verdict=frozen 自动冻结进化并告警)
HEALTH_DIVERSITY_FLOOR = 0.30   # 内容多样性指数
                                 # 下限(归一熵)
HEALTH_MATCH_FLOOR = 0.40       # 渠道匹配准确率
                                 # 下限
HEALTH_MAPE_CEIL = 0.30         # 预判偏差率
                                 # MAPE 上限

# 健康度样本下限(低于此样本量该指标
# 不判定——防小样本误冻结)
HEALTH_MIN_SAMPLES = 5

# 健康度裁决域(封闭)
HEALTH_VERDICTS = (
    "healthy",   # 三指标全在域内
    "degraded",  # 预警带(接近阈值)
    "frozen",    # 冻结进化(越界
                 # 自动/解冻人工专属)
)

# 冻结联动域(封闭——frozen 时拒绝的
# 进化动作面; 观测面不受影响)
HEALTH_FROZEN_DOMAINS = (
    "experiment_propose",   # 沙箱实验提案
    "mode_transfer_up",      # 模式升档
)

# 红队四向量(引流域封闭——71号 P8 范式
# 首次移植非资金域)
REDTEAM_VECTORS = (
    "RT-01",  # 刷量注入(伪造点击流)
    "RT-02",  # 归因投毒(伪造归并请求)
    "RT-03",  # 博弈操纵(伪造消耗
              # 骗取重博弈倾斜)
    "RT-04",  # 人格漂移(画像异常突变)
)

# 解冻人工专属双保险(环境变量——免疫
# 自动永不解冻铁律; 对齐 PAY71_IMMUNITY)
IMMUNITY_UNFREEZE_ENV = "ATTRACT72_IMMUNITY"

# 实验状态机(封闭——提案→46号→结论)
EXPERIMENT_STATUSES = (
    "proposed",   # 提案(46号 pending)
    "concluded",  # 已结论(双向结晶)
    "rejected",   # 46号驳回留痕
)

# 实验结论域(封闭——success 结晶知识/
# failure 结晶反知识/无定论)
EXPERIMENT_OUTCOMES = (
    "success", "failure", "inconclusive",
)

# 沙箱约束(铁律八: 非核心渠道/小预算/
# 灰度流量——失败不影响主业务)
SANDBOX_CHANNELS = (
    "kuaishou",   # 非核心渠道
    "bilibili",
    "seo",
)
SANDBOX_MAX_BUDGET = 50.0     # 小预算上限(元)
SANDBOX_MIN_SAMPLES = 100     # 灰度流量最小样本

# L1 白名单(full 档可开放参数——封闭)
FULL_AUTONOMY_PARAMS = (
    "exploration_ratio",       # 探索基金占比
    "landing_variant_weight",  # 落地页变体权重
    "topic_queue_threshold",   # 选题入队阈值
)

# full 档不可开放域(公示——奖励系数/
# 定律边界/合规参数永不可自主)
FULL_FORBIDDEN_DOMAINS = (
    "reward_rate",    # 奖励系数
    "law_boundary",   # 定律边界
    "compliance",     # 合规参数
)

# 46号档案口径(第48批——submit_change
# 前置入册)
EXPERIMENT_SCORER_ID = "growth_experiment"

# 进化日志窗口(条)
EVOLUTION_LOG_LIMIT = 50


# ============================================================
# 启动自检(宪法级)
# ============================================================

def _validate_registry() -> None:
    """注册表封闭自检(导入即验——
    RuntimeError 宪法级)"""
    # ① 模式四档封闭(引流域首创 full)
    if set(MODE_VALUES) != {
            "off", "shadow", "assist", "full"}:
        raise RuntimeError(
            "attract72 模式四档域非法")
    # ② 人格域封闭
    if set(PERSONA_TYPES) != {
            "connoisseur", "sharer",
            "bargain_hunter", "newcomer"}:
        raise RuntimeError(
            "attract72 人格类型域非法")
    # ③ 样本下限/判定阈值合法
    if PERSONA_MIN_SAMPLES < 1:
        raise RuntimeError(
            "attract72 人格样本下限域外")
    if CONNOISSEUR_ORDER_AMOUNT <= 0:
        raise RuntimeError(
            "attract72 品鉴客单阈值域外")
    if not (0 < SHARER_CONVERSION_LINE < 1):
        raise RuntimeError(
            "attract72 分享型转化线域外")
    if CONFIDENCE_FULL_SAMPLES < 1:
        raise RuntimeError(
            "attract72 置信度满样本域外")
    # ④ 层级域封闭+阈值单调递减
    if {t for t, _ in FOLLOWER_TIER_LINES} \
            != set(FOLLOWER_TIERS_INFLUENCER):
        raise RuntimeError(
            "attract72 博主层级域与阈值表不闭合")
    for i in range(
            1, len(FOLLOWER_TIER_LINES)):
        if FOLLOWER_TIER_LINES[i][1] \
                >= FOLLOWER_TIER_LINES[
                    i - 1][1]:
            raise RuntimeError(
                "attract72 博主层级阈值非单调递减")
    if {t for t, _ in MEMBER_TIER_LINES} \
            != set(FOLLOWER_TIERS_MEMBER):
        raise RuntimeError(
            "attract72 会员层级域与阈值表不闭合")
    for i in range(1, len(MEMBER_TIER_LINES)):
        if MEMBER_TIER_LINES[i][1] \
                >= MEMBER_TIER_LINES[i - 1][1]:
            raise RuntimeError(
                "attract72 会员层级阈值非单调递减")
    # ⑤ 情绪词表正负互斥
    pos, neg = (set(EMOTION_POSITIVE_WORDS),
                set(EMOTION_NEGATIVE_WORDS))
    if not pos or not neg or (pos & neg):
        raise RuntimeError(
            "attract72 情绪词表空或正负重叠")
    # ⑥ 场景/犹豫/意图词表域闭合+非空
    if set(SCENE_WORDS) != set(SCENE_TAGS):
        raise RuntimeError(
            "attract72 场景词表与标签域不闭合")
    if set(HESITATION_WORDS) \
            != set(HESITATION_SIGNALS):
        raise RuntimeError(
            "attract72 犹豫词表与信号域不闭合")
    if set(INTENT_WORDS) \
            != set(INTENT_TAGS) - {
                "high_engagement",
                "bounce_risk"}:
        raise RuntimeError(
            "attract72 意图词表与标签域不闭合")
    for words in (SCENE_WORDS.values(),
                  HESITATION_WORDS.values(),
                  INTENT_WORDS.values()):
        if not words or not all(
                w for w in words):
            raise RuntimeError(
                "attract72 词表存在空词条")
    # ⑦ 停留阈值分级单调(跳出<高参与)
    if not (DWELL_BOUNCE_SECONDS
            < DWELL_HIGH_SECONDS):
        raise RuntimeError(
            "attract72 停留阈值分级非单调")
    # ⑧ 信号域封闭+雷达消费档位合法
    if set(SIGNAL_TYPES) != {
            "festival", "radar_event",
            "platform_rule"}:
        raise RuntimeError(
            "attract72 信号类型域非法")
    if set(RADAR_CONSUME_GRADES) <= {
            "L3", "L4"}:
        raise RuntimeError(
            "attract72 雷达消费档位非法"
            "(L4 风险屏蔽永不消费)")
    if not (0 <= RADAR_MIN_VALUE <= 100):
        raise RuntimeError(
            "attract72 雷达价值下限域外")
    if RADAR_INGEST_LIMIT < 1:
        raise RuntimeError(
            "attract72 雷达消费上限域外")
    # ⑨ 雷达类别映射域内(40号 CATEGORIES
    #     消费方闭合)
    from repositories.radar_repository import (
        CATEGORIES,
    )
    if set(RADAR_CATEGORY_CHANNELS) \
            != set(CATEGORIES):
        raise RuntimeError(
            "attract72 雷达类别映射与 "
            "40号类别域不闭合")
    # ⑩ 节日日历合法(日期/权重域内)
    seen = set()
    for name, month, day, label, weight \
            in FESTIVAL_CALENDAR:
        if name in seen:
            raise RuntimeError(
                f"attract72 节日重复: {name}")
        seen.add(name)
        if not (1 <= month <= 12) \
                or not (1 <= day <= 31):
            raise RuntimeError(
                f"attract72 节日日期非法: "
                f"{name}")
        if not label:
            raise RuntimeError(
                f"attract72 节日缺标签: {name}")
        if not (0 < weight <= 1):
            raise RuntimeError(
                f"attract72 节日权重域外: "
                f"{name}")
    if FESTIVAL_LOOKAHEAD_DAYS < 1:
        raise RuntimeError(
            "attract72 节日前瞻窗口域外")
    # ⑪ 渠道域与 attract v1.0 一致(消费方
    #     铁律——不增不减)
    seeds = _attract_channel_seeds()
    if len(seeds) != 8 or "douyin" not in seeds:
        raise RuntimeError(
            "attract72 渠道域消费源异常")
    # ⑫ P2 因果: 维度/效应/状态机封闭+
    #     阈值合法+分桶覆盖 24 小时
    if set(CAUSAL_DIMENSIONS) != {
            "content_element",
            "channel_feature", "timing"}:
        raise RuntimeError(
            "attract72 因果维度域非法")
    if set(CAUSAL_EFFECT_TYPES) != {
            "driver", "loss", "neutral"}:
        raise RuntimeError(
            "attract72 因果效应域非法")
    if not (0 < CAUSAL_EFFECT_LINE < 1):
        raise RuntimeError(
            "attract72 反事实效应线域外")
    if CAUSAL_MIN_SAMPLES < 1 \
            or CAUSAL_CONF_SAMPLES \
            < CAUSAL_MIN_SAMPLES:
        raise RuntimeError(
            "attract72 因果样本参数域外")
    if not (0 < INSIGHT_VERIFY_CONFIDENCE
            <= 1):
        raise RuntimeError(
            "attract72 洞察验证置信线域外")
    if set(INSIGHT_STATUSES) != {
            "pending", "verified",
            "archived"}:
        raise RuntimeError(
            "attract72 洞察状态机非法")
    if set(LAW_KINDS) != {"law", "anti"}:
        raise RuntimeError(
            "attract72 定律种类域非法")
    if set(LAW_STATUSES) != {
            "draft", "submitted", "active",
            "expired", "rejected"}:
        raise RuntimeError(
            "attract72 定律状态机非法")
    if LAW_DECAY_MAX < 1:
        raise RuntimeError(
            "attract72 定律衰减上限域外")
    _hours_covered = set()
    for _name, (_lo, _hi) in TIMING_BUCKETS:
        if not (0 <= _lo < _hi <= 24):
            raise RuntimeError(
                "attract72 时段分桶区间非法")
        _hours_covered.update(
            range(_lo, _hi))
    if _hours_covered != set(range(24)):
        raise RuntimeError(
            "attract72 时段分桶未覆盖 24 小时")
    # ⑬ NL 路由域闭合(映射覆盖全部路由)
    if set(NL_ROUTE_KEYWORDS) \
            != set(NL_QUERY_ROUTES):
        raise RuntimeError(
            "attract72 NL 路由映射不闭合")
    for route, words in \
            NL_ROUTE_KEYWORDS.items():
        if not words:
            raise RuntimeError(
                f"attract72 NL 路由 {route} "
                f"关键词为空")
    # ⑭ 渠道别名表与 v1.0 渠道域一致
    #     (消费方铁律——不增不减)
    if set(CHANNEL_ALIASES) \
            != set(_attract_channel_seeds()):
        raise RuntimeError(
            "attract72 渠道别名表与 v1.0 "
            "渠道域不闭合")
    # ⑮ P3 预算: 状态机/阈值/窗口/基金域/
    #     时间尺度/档案口径封闭
    if set(FORECAST_STATUSES) != {
            "active", "superseded",
            "rebalanced"}:
        raise RuntimeError(
            "attract72 预分配状态机非法")
    if not (0 < POOL_WINDOW_SHARE < 1):
        raise RuntimeError(
            "attract72 月窗占比域外")
    if not (0 < DEVIATION_THRESHOLD < 1):
        raise RuntimeError(
            "attract72 偏差阈值域外")
    if not (0 < EXPLORATION_MIN
            < EXPLORATION_MAX < 0.5):
        raise RuntimeError(
            "attract72 探索基金浮动域非法")
    if EXPLORATION_LOW_SAMPLES < 1:
        raise RuntimeError(
            "attract72 探索样本线域外")
    if not (0 < SIGNAL_LIFT_CAP <= 1):
        raise RuntimeError(
            "attract72 信号提升上限域外")
    if not (0 < LAW_BOOST_CAP <= 1):
        raise RuntimeError(
            "attract72 定律助推上限域外")
    if FORECAST_WINDOW_HOURS <= 0:
        raise RuntimeError(
            "attract72 预分配窗口域外")
    if set(TIME_SCALES) != {
            "minute", "day", "month"}:
        raise RuntimeError(
            "attract72 三层时间尺度域非法")
    if BUDGET_SCORER_ID != "growth_budget":
        raise RuntimeError(
            "attract72 预算治理档案口径非法")
    # ⑯ P4 短链记忆: 变体/反作弊/记忆域封闭
    if set(LANDING_VARIANTS) != {
            "trust_first", "benefit_first",
            "default"}:
        raise RuntimeError(
            "attract72 落地页变体域非法")
    if set(ANTIFRAUD_STATES) != {
            "normal", "isolated", "released"}:
        raise RuntimeError(
            "attract72 反作弊状态机非法")
    if FINGERPRINT_RATE_LIMIT < 1:
        raise RuntimeError(
            "attract72 指纹频次线域外")
    if FINGERPRINT_ISOLATE_SECONDS <= 0:
        raise RuntimeError(
            "attract72 隔离窗域外")
    # ⑰ P5 热点: 势能权重/阈值/裁决域封闭
    _pw = (POTENTIAL_WEIGHT_TIMELINESS
           + POTENTIAL_WEIGHT_RELEVANCE
           + POTENTIAL_WEIGHT_SAFETY
           + POTENTIAL_WEIGHT_CONVERSION)
    if abs(_pw - 1.0) > 0.001:
        raise RuntimeError(
            "attract72 四维势能权重和非 1")
    if not (0 <= HOTSPOT_SAFETY_FLOOR <= 1):
        raise RuntimeError(
            "attract72 热点安全下限域外")
    if not (0 <= HOTSPOT_CHASE_THRESHOLD <= 1):
        raise RuntimeError(
            "attract72 追投势能阈值域外")
    if set(HOTSPOT_VERDICTS) != {
            "chase", "observe", "reject"}:
        raise RuntimeError(
            "attract72 热点裁决域非法")
    if set(HOTSPOT_DECISION_STATUSES) != {
            "pending", "confirmed",
            "executed", "closed"}:
        raise RuntimeError(
            "attract72 卡位状态机非法")
    # ⑱ P6 元认知: 健康度/红队/沙箱/
    #     L1 白名单域封闭
    if set(HEALTH_VERDICTS) != {
            "healthy", "degraded", "frozen"}:
        raise RuntimeError(
            "attract72 健康度裁决域非法")
    for th in (HEALTH_DIVERSITY_FLOOR,
               HEALTH_MATCH_FLOOR,
               HEALTH_MAPE_CEIL):
        if not (0 < th < 1):
            raise RuntimeError(
                "attract72 健康度阈值域外")
    if HEALTH_MIN_SAMPLES < 1:
        raise RuntimeError(
            "attract72 健康度样本下限域外")
    if set(HEALTH_FROZEN_DOMAINS) != {
            "experiment_propose",
            "mode_transfer_up"}:
        raise RuntimeError(
            "attract72 冻结联动域非法")
    if set(REDTEAM_VECTORS) != {
            "RT-01", "RT-02",
            "RT-03", "RT-04"}:
        raise RuntimeError(
            "attract72 红队四向量域非法")
    if set(EXPERIMENT_STATUSES) != {
            "proposed", "concluded",
            "rejected"}:
        raise RuntimeError(
            "attract72 实验状态机非法")
    if set(EXPERIMENT_OUTCOMES) != {
            "success", "failure",
            "inconclusive"}:
        raise RuntimeError(
            "attract72 实验结论域非法")
    if SANDBOX_MAX_BUDGET <= 0 \
            or SANDBOX_MIN_SAMPLES < 1:
        raise RuntimeError(
            "attract72 沙箱约束域外")
    if set(SANDBOX_CHANNELS) & {
            "douyin", "xiaohongshu",
            "wechat", "direct"}:
        raise RuntimeError(
            "attract72 沙箱渠道含核心"
            "渠道(铁律八违反)")
    if not FULL_AUTONOMY_PARAMS:
        raise RuntimeError(
            "attract72 L1 白名单为空")
    if set(FULL_AUTONOMY_PARAMS) & \
            set(FULL_FORBIDDEN_DOMAINS):
        raise RuntimeError(
            "attract72 L1 白名单与不可"
            "开放域重叠")
    if EXPERIMENT_SCORER_ID != \
            "growth_experiment":
        raise RuntimeError(
            "attract72 实验治理档案口径非法")


_validate_registry()
