"""73号·AI智能会员体验大模型 体验注册表
(member73_registry, P1)

规划(docs/73号_AI智能会员体验大模型_创新规划方案.md
§六/§九):
    模式四档(MEMBER73_MODE off/shadow/
    assist/full——触达呈现域可 full,
    代办域永远 assist[P3])
    +关键时刻域(四类触发×确定性权重)
    +入口域(场景上下文因子)
    +缺口因子分档(成长值缺口进度带)
    +决策三态(present/defer/abandon)
    +呈现形式域(progress_hint/card/
    banner——响应率学习淘汰)
    +静默窗(夜间免打扰)+每日打扰封顶
    +新会员冷启动影子期(7 天只观测)
    +响应类型域(click/upgrade/ignore)

设计(71号 pay71_registry 封闭注册表范式平移):
    - 封闭注册: 可断言/可测试/启动自检
    - 73号=体验共生层, 消费 member 等级
      底盘+智客织物/权益矩阵只读——
      永不修改源数据(叠加铁律)
    - 触发分=确定性查表公式(动作权重×
      缺口因子×入口因子), LLM 禁入
      判定链铁律不变; LLM 仅组织
      hint 文案模板(数字 100% 插值)

启动自检 _validate_registry()(RuntimeError
宪法级):
    - 模式四档封闭
    - 时刻/入口/形式/响应域封闭
    - 权重域内+缺口分档单调递减
    - 阈值分级单调+封顶/影子域内
    - 权益矩阵键域=member L1-L5(消费方
      铁律——不增不减)
"""

import logging
import os

logger = logging.getLogger("member73_registry")

MODEL_VERSION = "v1-member73-registry"

DEFAULT_MODE = "off"

# 四档(体验域: 触达呈现可 full;
# 代办域 P3 永远 assist——授权显式性
# 优先于自主性)
MODE_VALUES = ("off", "shadow", "assist", "full")


def current_mode() -> str:
    """模块开关(MEMBER73_MODE, 默认 off——
    off=仅观测面(视野/统计/面板只读),
    触达面关闭; shadow=触达留痕不呈现;
    assist=触达建议注入前端确认位;
    full=低风险触达域自主(呈现位)"""
    mode = os.environ.get("MEMBER73_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


def is_kill() -> bool:
    """紧急静默(MEMBER73_KILL——秒级
    回退基础模式, 观测面保留; 参考方案
    "紧急静默"宪法化, 对齐 71号 KILL
    惯例)"""
    return os.environ.get("MEMBER73_KILL") == "1"


# ============================================================
# P1 关键时刻域(感知层——四类触发)
# ============================================================

# 时刻类型×权重(封闭——触发分第一因子)
MOMENT_WEIGHTS: dict = {
    "order_done": 0.9,       # 下单完成(黄金时刻)
    "achieved": 0.8,         # 成就达成(首购/连续消费)
    "points_changed": 0.5,   # 积分变动(返分/兑换)
    "profile_gap": 0.4,      # 资料缺口(补全提示)
}

# 时刻中文标签(hint 文案确定性模板用)
MOMENT_LABELS: dict = {
    "order_done": "本次消费",
    "achieved": "本次成就",
    "points_changed": "积分变动",
    "profile_gap": "资料完善",
}

# 入口域×因子(封闭——触发分第三因子,
# 场景上下文)
ENTRY_WEIGHTS: dict = {
    "order_page": 1.0,    # 订单完成页(顺势最佳)
    "profile_page": 0.9,  # 个人中心
    "points_page": 0.8,   # 积分页
    "home": 0.6,          # 首页(弱场景)
}

# 缺口进度→因子分档(封闭, 单调递减;
# gapProgress=(成长值-当前级阈值)/
# (下级阈值-当前级阈值))
GAP_FACTOR_BANDS = (
    (0.9, 1.0),   # ≥90% 缺口几乎填平
    (0.7, 0.8),
    (0.5, 0.6),
    (0.2, 0.3),
    (0.0, 0.1),   # 刚起步
)


def gap_factor(progress: float) -> float:
    """缺口进度 → 因子(确定性查表)"""
    p = max(0.0, min(1.0, float(progress or 0)))
    for line, factor in GAP_FACTOR_BANDS:
        if p >= line:
            return factor
    return 0.1


# ============================================================
# P1 决策域(时机三态——确定性阈值)
# ============================================================

# 决策三态(封闭)
DECISION_STATES = ("present", "defer", "abandon")

# 呈现线(触发分≥0.70 → present)
PRESENT_LINE = 0.70

# 观察线(≥0.40 → defer; <0.40 abandon)
DEFER_LINE = 0.40

# ============================================================
# P1 呈现形式域(响应率学习淘汰)
# ============================================================

# 形式域(封闭——进度条微动/权益卡片/
# 底部浮层)
HINT_FORMS = ("progress_hint", "card", "banner")

# 形式种子效果(无响应数据时的初值——
# 确定性; 有数据后按 响应数/呈现数 滚动)
FORM_SEED_EFFECT: dict = {
    "progress_hint": 0.6,
    "card": 0.5,
    "banner": 0.3,
}

# 形式滚动统计最小样本(呈现件数不足
# 此值 → 用种子——防单件样本翻转)
MIN_FORM_SAMPLES = 3

# 连续忽略降权线(同形式连续 N 次忽略
# → 该会员该形式效果×惩罚)
RESPONSE_IGNORE_BREAK = 2

# 连续忽略惩罚系数(确定性)
IGNORE_PENALTY = 0.7

# 响应类型域(封闭)
RESPONSE_TYPES = ("click", "upgrade", "ignore")

# ============================================================
# P1 静默窗与打扰封顶(无感度先导约束)
# ============================================================

# 夜间静默窗(小时闭区间——22:00-08:00
# 免打扰; 注册默认开启可改[P2 用户面])
SILENCE_START_HOUR = 22
SILENCE_END_HOUR = 8


def in_silence(hour: int) -> bool:
    """小时 → 静默窗判定(确定性)"""
    h = int(hour) % 24
    return h >= SILENCE_START_HOUR or h < SILENCE_END_HOUR


# 每日打扰封顶(24h 呈现的 present 时刻数)
DAILY_DISTURB_CAP = 3

# 新会员冷启动影子期(天——7 天内
# 只观测不呈现, 触发分照算留痕)
SHADOW_DAYS = 7

# 视野外推保底天数(消费史不足
# 1 天时防除零)
MIN_RATE_DAYS = 1.0

# ============================================================
# P1 权益对比口径(消费 zk_operation
# LEVEL_BENEFITS 只读铁律)
# ============================================================

# 即效权益关键词(含此 → instantEffects;
# 其余 → claimRequired 需领取)
INSTANT_KEYWORDS = ("折扣", "免邮")


def classify_benefit(benefit: str) -> str:
    """权益条目 → 即效/需领取(确定性)"""
    return ("instant" if any(k in (benefit or "")
            for k in INSTANT_KEYWORDS)
            else "claim")


# ============================================================
# P2 无感度量域(规划 §四 4.5——无感度
# 四维公式, 确定性, LLM 禁入)
# ============================================================

# 无感度四维满分(0-100, 越高越无感)
EFFORTLESS_CEIL = 100

# 四维基准(封顶线——达到即该项满分;
# 步数≤3 满分/字段≤4 满分/等待≤2s
# 满分/打扰 0 次满分)
EFFORTLESS_BENCH: dict = {
    "steps": 3,
    "formFields": 4,
    "waitSeconds": 2.0,
    "disturbCount": 0,
}

# 无感度=100-(步骤超基准×8+字段超基准
# ×6+等待超基准×10+打扰×15), 下限 0)
EFFORTLESS_PENALTY: dict = {
    "steps": 8,
    "formFields": 6,
    "waitSeconds": 10.0,
    "disturbCount": 15,
}

# 自适应参数域(服务端查表下发——
# 前端直读, 无端侧模型[部署现实])
ADAPT_KEYS = (
    "uiDensity",       # 界面密度
    "animSpeed",       # 动画节奏
    "notifyFrequency",  # 通知频率
)

# 情境档位(封闭——时段×状态确定性路由)
ADAPT_CONTEXTS = (
    "night",        # 深夜(静默联动)
    "rush_hour",    # 高峰时段
    "normal",       # 常规
)

# 情境→自适应参数规则表(封闭——
# 每档三键确定性取值)
ADAPT_RULES: dict = {
    "night": {
        "uiDensity": "compact",
        "animSpeed": "slow",
        "notifyFrequency": "mute",
    },
    "rush_hour": {
        "uiDensity": "compact",
        "animSpeed": "fast",
        "notifyFrequency": "low",
    },
    "normal": {
        "uiDensity": "standard",
        "animSpeed": "standard",
        "notifyFrequency": "standard",
    },
}

# 高峰时段(小时闭区间——
# 早 7-9/晚 17-19)
RUSH_HOURS = ((7, 9), (17, 19))


def adapt_context(hour: int,
                  silenced: bool) -> str:
    """情境路由(静默优先>高峰>常规
    ——确定性)"""
    if silenced or in_silence(hour):
        return "night"
    h = int(hour) % 24
    for lo, hi in RUSH_HOURS:
        if lo <= h < hi:
            return "rush_hour"
    return "normal"


# 静默设置域(用户面——注册默认开启
# 夜间静默, 可关)
SILENCE_DEFAULT = True


# ============================================================
# P3 预判代办域(规划 §四 4.4——授权
# 白名单三档, 宪法级)
# ============================================================

# 代办动作域(封闭五动作)
DELEGATE_ACTIONS = (
    "profile_completion",   # 资料补全
    "benefit_claim",        # 权益领取
    "renewal_prefill",      # 续费预填(不代付)
    "review_order",         # 订单评价
    "address_confirm",      # 收货地址确认
)

# 动作风险三档(封闭——执行语义)
DELEGATE_RISK_TIERS = (
    "low",       # 低风险: 授权后可代
    "medium",    # 中风险: 只预填不执行
    "high",      # 高风险: 仅单步确认引导
)

# 动作 → 风险档/执行语义(封闭映射)
DELEGATE_RISK: dict = {
    "profile_completion": ("low",
                            "authorized_execute"),
    "benefit_claim": ("low",
                      "authorized_execute"),
    "renewal_prefill": ("medium",
                        "prefill_only"),
    "review_order": ("low",
                     "authorized_execute"),
    "address_confirm": ("low",
                        "authorized_execute"),
}

# 高风险确认动作域(资金/跨平台——
# 永不代办, 仅单步确认引导)
CONFIRM_ONLY_ACTIONS = (
    "payment",              # 支付
    "cross_platform_bind",  # 跨平台绑定
)

# 授权来源域(封闭)
GRANT_SOURCES = (
    "user",                  # 用户显式授权
    "registration_default",  # 注册默认(低风险)
)

# 预判动作池(封闭——按历史序列频率
# 排序 top1; 值域=DELEGATE_ACTIONS)
PREDICT_POOL = DELEGATE_ACTIONS

# 预判数据源: 会员行为序列(member
# orders/资料完整度/积分——只读)

# 代办日志结果域(封闭)
DELEGATE_RESULTS = (
    "executed",   # 已代办(白名单+授权)
    "prefilled",  # 已预填(中风险)
    "confirmed",  # 单步确认引导(高风险)
    "rejected",   # 拒绝(未授权/白名单外)
)


# ============================================================
# P4 信任共生域(规划 §四 4.6——四可
# 原则, 用户侧透明)
# ============================================================

# 面板动作流种类域(封闭——"AI 为我
# 做了什么"统一动作流)
TRUST_LOG_KINDS = (
    "hint",      # 引导呈现(P1)
    "reveal",    # 权益告知(P1)
    "delegate",  # 代办执行(P3)
)

# 面板可撤回种类域(封闭——revoke
# 统一撤回位)
REVOKABLE_KINDS = (
    "delegate",  # 代办授权可撤回
)

# 遗忘留痕表(封闭——硬删除后 seq
# 留痕; 49号隐私预算联动口径)
FORGET_TABLES = (
    "moments",     # 引导留痕
    "reveals",     # 权益告知
    "effortless",  # 无感度快照
    "grants",      # 授权位图
    "logs",        # 代办留痕
)

# 负反馈降权(连续忽略/撤销→该会员
# 触发分全局乘数; 确定性)
NEGATIVE_FEEDBACK_ACTIONS = (
    "revoke",    # 授权撤销
    "forget",    # 画像遗忘
)

# 触发分负反馈降权乘数(连续 N 次
# 负反馈后 × 此系数)
TRIGGER_PENALTY_FACTOR = 0.5

# 负反馈生效线(连续 N 次)
NEGATIVE_FEEDBACK_BREAK = 2

# 信任报告周期(天)
TRUST_REPORT_DAYS = 30


# ============================================================
# P5 元认知域(规划 §五 5.3/§六——漂移
# 监控+红队四向量+免疫冻结+L1 白名单)
# ============================================================

# 漂移信号种类域(封闭三信号)
DRIFT_SIGNALS = (
    "present_anomaly",   # 触达总量异常(骚扰化)
    "response_drop",     # 响应率骤降(形式失效)
    "revoke_anomaly",   # 撤销率异常(信任受损)
)

# 漂移检测阈值(封闭——确定性)
DRIFT_THRESHOLDS = {
    # 全站当日呈现量异常线(≥30 触达
    # 总量——骚扰化倾向)
    "presentAnomaly": 30,
    # 响应率骤降线(<10% 且样本足)
    "responseDrop": 0.10,
    # 负反馈占比异常线(>30% 且样本足)
    "revokeAnomaly": 0.30,
    # 最小样本量
    "minSamples": 5,
}

# 红队向量域(封闭四向量——体验域)
REDTEAM_VECTORS = (
    "RT-01",  # 打扰轰炸(伪造高频
              # 触发骗过封顶)
    "RT-02",  # 诱导升级(伪造紧迫
              # 感文案注入)
    "RT-03",  # 越权代办(白名单外
              # 动作伪造授权)
    "RT-04",  # 画像投毒(异常字段
              # 注入伪造缺口)
)

# 紧迫词表(RT-02 检测——hint 文案
# 禁用域; 模板生成永不包含, 注入
# 检测命中即失守)
URGENCY_WORDS = (
    "仅剩", "最后一天", "马上消失",
    "立即升级否则", "限时秒杀",
)

# 免疫冻结状态域(封闭——冻结自动
# [安全方向]/解冻人工专属)
IMMUNITY_STATES = ("active", "frozen")

# 分布监控冻结规则(封闭——漂移信号
# 数达到即冻结进化[触达面])
IMMUNITY_FREEZE_RULES = {
    "driftSignalCount": 2,
}

# 解冻人工专属双保险(环境变量
# MEMBER73_IMMUNITY=1——免疫自动
# 永不解冻+运维显式授权)
IMMUNITY_UNFREEZE_ENV = "MEMBER73_IMMUNITY"

# L1 白名单(full 档自主域——封闭;
# 代办/权益变更永不入白名单铁律)
L1_AUTONOMY_DOMAINS = (
    "hint_render",     # hint 呈现
    "silence_rule",    # 静默规则
    "form_ranking",    # 形式排序
)

# 进化日志条目种类域(封闭)
EVOLUTION_LOG_KINDS = (
    "form_learning",      # 形式响应率学习
    "negative_feedback",  # 负反馈模式
    "drift_detected",    # 漂移检测
    "freeze",            # 免疫冻结
    "unfreeze",          # 解冻
    "redteam",           # 红队批次
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
            "member73 模式四档域非法")
    # ② 时刻域封闭+权重域内
    if set(MOMENT_WEIGHTS) != {
            "order_done", "achieved",
            "points_changed", "profile_gap"}:
        raise RuntimeError(
            "member73 时刻类型域非法")
    if set(MOMENT_LABELS) \
            != set(MOMENT_WEIGHTS):
        raise RuntimeError(
            "member73 时刻标签与类型不闭合")
    for w in MOMENT_WEIGHTS.values():
        if not (0 < w <= 1):
            raise RuntimeError(
                "member73 时刻权重域外")
    # ③ 入口域封闭+因子域内
    if set(ENTRY_WEIGHTS) != {
            "order_page", "profile_page",
            "points_page", "home"}:
        raise RuntimeError(
            "member73 入口域非法")
    for w in ENTRY_WEIGHTS.values():
        if not (0 < w <= 1):
            raise RuntimeError(
                "member73 入口因子域外")
    # ④ 缺口分档单调递减+域内
    for i in range(1, len(GAP_FACTOR_BANDS)):
        if GAP_FACTOR_BANDS[i][0] \
                >= GAP_FACTOR_BANDS[i - 1][0]:
            raise RuntimeError(
                "member73 缺口分档线非递减")
    for line, factor in GAP_FACTOR_BANDS:
        if not (0 <= line <= 1) \
                or not (0 < factor <= 1):
            raise RuntimeError(
                "member73 缺口分档域外")
    # ⑤ 决策三态+阈值分级单调
    if set(DECISION_STATES) != {
            "present", "defer", "abandon"}:
        raise RuntimeError(
            "member73 决策三态域非法")
    if not (0 < DEFER_LINE
            < PRESENT_LINE <= 1):
        raise RuntimeError(
            "member73 决策阈值非单调")
    # ⑥ 形式域封闭+种子闭合
    if set(FORM_SEED_EFFECT) \
            != set(HINT_FORMS):
        raise RuntimeError(
            "member73 形式种子与形式域不闭合")
    for e in FORM_SEED_EFFECT.values():
        if not (0 < e <= 1):
            raise RuntimeError(
                "member73 形式种子效果域外")
    if not (1 <= MIN_FORM_SAMPLES <= 20):
        raise RuntimeError(
            "member73 形式统计最小样本域外")
    if RESPONSE_IGNORE_BREAK < 1:
        raise RuntimeError(
            "member73 连续忽略线域外")
    if not (0 < IGNORE_PENALTY < 1):
        raise RuntimeError(
            "member73 忽略惩罚系数域外")
    # ⑦ 响应类型域封闭
    if set(RESPONSE_TYPES) != {
            "click", "upgrade", "ignore"}:
        raise RuntimeError(
            "member73 响应类型域非法")
    # ⑧ 静默窗合法(跨午夜区间)
    if not (0 <= SILENCE_START_HOUR < 24
            and 0 <= SILENCE_END_HOUR < 24
            and SILENCE_START_HOUR
            != SILENCE_END_HOUR):
        raise RuntimeError(
            "member73 静默窗区间非法")
    # ⑨ 封顶/影子/保底域内
    if DAILY_DISTURB_CAP < 1:
        raise RuntimeError(
            "member73 每日打扰封顶域外")
    if not (1 <= SHADOW_DAYS <= 30):
        raise RuntimeError(
            "member73 冷启动影子期域外")
    if MIN_RATE_DAYS < 1:
        raise RuntimeError(
            "member73 外推保底天数域外")
    # ⑩ 权益矩阵键域=member L1-L5
    #    (消费方铁律——不增不减)
    from services.zk_operation_service import (
        LEVEL_BENEFITS,
    )
    if set(LEVEL_BENEFITS) != {1, 2, 3, 4, 5}:
        raise RuntimeError(
            "member73 权益矩阵键域与 "
            "L1-L5 不闭合")
    if not INSTANT_KEYWORDS:
        raise RuntimeError(
            "member73 即效关键词域为空")
    # ⑪ P2 无感度量: 基准/惩罚四维
    #     闭合+域内+自适应规则表闭合
    if set(EFFORTLESS_BENCH) \
            != set(EFFORTLESS_PENALTY):
        raise RuntimeError(
            "member73 无感度基准与惩罚"
            "不闭合")
    for k, v in EFFORTLESS_BENCH.items():
        if v < 0:
            raise RuntimeError(
                f"member73 无感度基准域外: "
                f"{k}={v}")
    for k, v in EFFORTLESS_PENALTY.items():
        if not v > 0:
            raise RuntimeError(
                f"member73 无感度惩罚域外: "
                f"{k}={v}")
    if EFFORTLESS_CEIL != 100:
        raise RuntimeError(
            "member73 无感度满分口径非法")
    if set(ADAPT_RULES) != set(ADAPT_CONTEXTS):
        raise RuntimeError(
            "member73 自适应规则表与情境域"
            "不闭合")
    for ctx, params in ADAPT_RULES.items():
        if set(params) != set(ADAPT_KEYS):
            raise RuntimeError(
                f"member73 自适应情境 {ctx} "
                f"参数键域非法")
    for lo, hi in RUSH_HOURS:
        if not (0 <= lo < hi <= 24):
            raise RuntimeError(
                "member73 高峰时段区间非法")
    if set(ADAPT_CONTEXTS) != {
            "night", "rush_hour", "normal"}:
        raise RuntimeError(
            "member73 情境档位域非法")
    # ⑫ P3 代办: 动作/风险档/映射/
    #     授权源/结果域封闭+白名单铁律
    if set(DELEGATE_ACTIONS) != {
            "profile_completion",
            "benefit_claim",
            "renewal_prefill",
            "review_order",
            "address_confirm"}:
        raise RuntimeError(
            "member73 代办动作域非法")
    if set(DELEGATE_RISK_TIERS) != {
            "low", "medium", "high"}:
        raise RuntimeError(
            "member73 风险三档域非法")
    if set(DELEGATE_RISK) \
            != set(DELEGATE_ACTIONS):
        raise RuntimeError(
            "member73 风险映射与动作域"
            "不闭合")
    for action, (tier, mode_) in \
            DELEGATE_RISK.items():
        if tier not in DELEGATE_RISK_TIERS:
            raise RuntimeError(
                f"member73 动作 {action} "
                f"风险档域外")
        expected = ("prefill_only"
                    if tier == "medium"
                    else "authorized_execute")
        if mode_ != expected:
            raise RuntimeError(
                f"member73 动作 {action} "
                f"执行语义与风险档不符")
    # 资金/跨平台永不入代办白名单(铁律)
    if set(CONFIRM_ONLY_ACTIONS) \
            & set(DELEGATE_ACTIONS):
        raise RuntimeError(
            "member73 资金类动作渗入"
            "代办白名单——铁律违反")
    if set(GRANT_SOURCES) != {
            "user", "registration_default"}:
        raise RuntimeError(
            "member73 授权来源域非法")
    if set(PREDICT_POOL) \
            - set(DELEGATE_ACTIONS):
        raise RuntimeError(
            "member73 预判池域外")
    if set(DELEGATE_RESULTS) != {
            "executed", "prefilled",
            "confirmed", "rejected"}:
        raise RuntimeError(
            "member73 代办结果域非法")
    # ⑬ P4 信任: 面板/撤回/遗忘/负
    #     反馈/报告域封闭
    if set(TRUST_LOG_KINDS) != {
            "hint", "reveal",
            "delegate"}:
        raise RuntimeError(
            "member73 面板动作流域非法")
    if not set(REVOKABLE_KINDS) \
            <= set(TRUST_LOG_KINDS):
        raise RuntimeError(
            "member73 可撤回域外溢")
    if set(FORGET_TABLES) != {
            "moments", "reveals",
            "effortless", "grants",
            "logs"}:
        raise RuntimeError(
            "member73 遗忘留痕表域非法")
    if set(NEGATIVE_FEEDBACK_ACTIONS) \
            - {"revoke", "forget"}:
        raise RuntimeError(
            "member73 负反馈动作域非法")
    if not (0 < TRIGGER_PENALTY_FACTOR
            < 1):
        raise RuntimeError(
            "member73 负反馈降权系数域外")
    if NEGATIVE_FEEDBACK_BREAK < 1:
        raise RuntimeError(
            "member73 负反馈生效线域外")
    if TRUST_REPORT_DAYS < 1:
        raise RuntimeError(
            "member73 信任报告周期域外")
    # ⑭ P5 元认知: 漂移/红队/紧迫词/
    #     免疫/L1 白名单/进化日志域封闭
    if set(DRIFT_SIGNALS) != {
            "present_anomaly",
            "response_drop",
            "revoke_anomaly"}:
        raise RuntimeError(
            "member73 漂移信号域非法")
    if set(DRIFT_THRESHOLDS) != {
            "presentAnomaly",
            "responseDrop",
            "revokeAnomaly",
            "minSamples"}:
        raise RuntimeError(
            "member73 漂移阈值表不闭合")
    for k, v in DRIFT_THRESHOLDS.items():
        if k == "presentAnomaly" \
                and v < 1:
            raise RuntimeError(
                "member73 漂移阈值域外: "
                f"{k}")
        if k in ("responseDrop",
                 "revokeAnomaly") \
                and not (0 < v < 1):
            raise RuntimeError(
                "member73 漂移阈值域外: "
                f"{k}")
        if k == "minSamples" and v < 1:
            raise RuntimeError(
                "member73 漂移最小样本域外")
    if set(REDTEAM_VECTORS) != {
            "RT-01", "RT-02",
            "RT-03", "RT-04"}:
        raise RuntimeError(
            "member73 红队向量域非法")
    if not URGENCY_WORDS:
        raise RuntimeError(
            "member73 紧迫词表为空")
    if set(IMMUNITY_STATES) != {
            "active", "frozen"}:
        raise RuntimeError(
            "member73 免疫冻结状态域非法")
    for k, v in IMMUNITY_FREEZE_RULES.items():
        if not (isinstance(v, int)
                and v >= 1):
            raise RuntimeError(
                f"member73 免疫冻结规则"
                f"非法: {k}={v}")
    if IMMUNITY_UNFREEZE_ENV \
            != "MEMBER73_IMMUNITY":
        raise RuntimeError(
            "member73 解冻环境变量口径非法")
    if set(L1_AUTONOMY_DOMAINS) != {
            "hint_render",
            "silence_rule",
            "form_ranking"}:
        raise RuntimeError(
            "member73 L1 白名单域非法")
    if set(EVOLUTION_LOG_KINDS) != {
            "form_learning",
            "negative_feedback",
            "drift_detected",
            "freeze", "unfreeze",
            "redteam"}:
        raise RuntimeError(
            "member73 进化日志域非法")


_validate_registry()
