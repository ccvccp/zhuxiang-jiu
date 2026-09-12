"""71号·AI智能支付端口大模型 端口池认知注册表
(pay71_registry, P0)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§七 P0):
    端口池注册表(消费 69号七通道字典——
    只读 import, 71号永不建第二套通道字典)
    +端口态扩展域(healthy/degraded/broken)
    +前兆信号域(失效前兆——确定性权重)
    +四维调配目标域(P3 帕累托口径预注册)
    +模式三态(PAY71_MODE off/shadow/assist
    ——默认 off)

设计(69号 pay69_registry 封闭注册表范式平移):
    - 封闭注册: 可断言/可测试/启动自检
    - 71号=支付端口大模型(端口池生命周期+
      资金流全程编排), 消费 69号 P0 健康度
      视图/P1 routeScore 作为输入, 永不
      修改 69号数据(叠加铁律)
    - 资金路由零重复(69号 P1 唯一负责);
      71号调配结果仅作建议注入

启动自检 _validate_registry()(RuntimeError
宪法级):
    - 端口域与 69号通道域完全一致(消费方
      铁律——不增不减不改)
    - 端口态域封闭
    - 前兆信号域封闭+权重合法
    - 四维调配权重封闭+和=1.0
    - 预警阈值合法
"""

import logging
import os

logger = logging.getLogger("pay71_registry")

MODEL_VERSION = "v1-pay71-registry"

DEFAULT_MODE = "off"

MODE_VALUES = ("off", "shadow", "assist")


def current_mode() -> str:
    """模块开关(PAY71_MODE, 默认 off——
    决策面关闭: off=仅观测面; shadow=
    影子学习期(调配建议只留痕); assist=
    辅助生产期(建议注入 69号路由参考)"""
    mode = os.environ.get("PAY71_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


def is_kill() -> bool:
    """紧急制动(PAY71_KILL——秒级冻结
    一切进化参数, 退回只读基线; 对齐
    PAY69_KILL/QR70_KILL 惯例)"""
    return os.environ.get("PAY71_KILL") == "1"


# ============================================================
# 端口池域(消费 69号七通道——只读铁律)
# ============================================================

# 71号端口池 = 69号通道域(消费方铁律:
# 不建第二套通道字典, 永不修改 69号注册表)
# import 保持与 pay69_registry 完全同源
def _port_ids() -> tuple:
    """端口域(69号 CHANNEL_IDS 只读消费)"""
    from services.pay69_registry import (
        CHANNEL_IDS,
    )
    return tuple(CHANNEL_IDS)


def _port_registry() -> dict:
    """端口注册表(69号 CHANNEL_REGISTRY
    只读消费——费率/限额/时效/特征)"""
    from services.pay69_registry import (
        CHANNEL_REGISTRY,
    )
    return CHANNEL_REGISTRY


# ============================================================
# 端口态扩展域(P1 自愈编排的消费口径)
# ============================================================

# 端口态三态(封闭——规划 §4.1:
# 端口池三态治理)
PORT_STATES = (
    "healthy",    # 健康(正常参与调配)
    "degraded",   # 降级(自动降权建议+留痕
                  # ——保护方向)
    "broken",     # 熔断摘除(新请求不再
                  # 建议; 半开探测恢复)
)

# 端口态权重因子(调配评分的端口态
# 折减系数——确定性; P3 帕累托消费)
PORT_STATE_FACTORS: dict = {
    "healthy": 1.0,
    "degraded": 0.5,
    "broken": 0.0,
}

# ============================================================
# 前兆信号域(P0 观测面——失效前兆
# 确定性权重; 预警仅观测, 阈值变更
# 走慢环 46号审批)
# ============================================================

PRECURSOR_SIGNALS = (
    "latency_rising",     # 响应时间渐增
    "errorcode_climb",    # 错误码占比爬升
    "success_decline",    # 成功率缓降
    "callback_delay",     # 回调延迟拉长
)

# 前兆信号权重(封闭——确定性; 叠加
# ≥阈值→预警观测)
PRECURSOR_WEIGHTS: dict = {
    "latency_rising": 0.35,
    "errorcode_climb": 0.35,
    "success_decline": 0.20,
    "callback_delay": 0.10,
}

# 前兆预警阈值(确定性——达到即预警
# 观测留痕; 变更走慢环审批)
PRECURSOR_ALERT_THRESHOLD = 0.50

# ============================================================
# P1 端口自愈编排(保护方向分级动作
# ——熔断/恢复可自动+全留痕铁律)
# ============================================================

# 前兆滚动窗口容量(P1——每端口最近
# N 条信号去重叠加; 变更走慢环审批)
SELFHEAL_WINDOW_SIZE = 10

# 熔断阈值(窗口多信号叠加≥0.80
# →broken 摘除——保护方向自动)
PRECURSOR_FUSE_THRESHOLD = 0.80

# 降级阈值(窗口叠加≥0.50 或 69号
# critical →degraded 降权)
# = P0 PRECURSOR_ALERT_THRESHOLD(同源)

# 半开探测恢复条件(broken 态连续
# N 次探针成功→healthy)
PROBE_REQUIRED_SUCCESSES = 3

# 影子冷启动期(天——新端口接入只观测
# 不参与调配; 达标后转正建议书)
SHADOW_DAYS = 7

# 端口治理生命周期域(封闭——影子
# 冷启动/转正/摘牌全链; 正式启停
# 永远走建议书+admin 终审铁律)
GOVERNANCE_STATES = (
    "ungoverned",   # 未纳管(默认)
    "shadow",       # 影子冷启动期
    "active",       # 转正(参与调配
                    # ——P3 消费)
    "offboarded",   # 摘牌(经建议书
                    # 人工终审)
)

# 转正/摘牌建议书状态机(封闭——端口
# 正式启停永不自动: proposed→approved/
# rejected, admin 终审)
PROPOSAL_STATES = (
    "proposed",   # 已建议(待终审)
    "approved",   # admin 确认生效
    "rejected",   # admin 拒绝留痕
)

# 建议书种类域(封闭)
PROPOSAL_KINDS = (
    "promote",    # 转正(shadow→active)
    "offboard",   # 摘牌(active→
                  # offboarded)
)

# 自愈动作域(封闭——全留痕口径)
SELFHEAL_ACTIONS = (
    "degrade",      # 降级(healthy→
                    # degraded, 保护)
    "fuse",         # 熔断(→broken,
                    # 保护)
    "recover",      # 恢复(→healthy,
                    # 保护)
    "probe_pass",   # 探针通过(计数)
    "probe_fail",   # 探针失败(清零)
    "no_change",    # 评估无变更
)

# ============================================================
# P2 预判式支付(预载可撤销观测域+资金
# 永不自动铁律——用户显式触发)
# ============================================================

# 习惯样本下限(69号 habits 计数——不足
# 则退化为费率优选默认建议)
PREDICT_MIN_SAMPLES = 3

# 大额拆分阈值(元——对齐 69号 AMOUNT_
# BANDS ¥5000 档; ≥阈值可发起拆分建议书)
SPLIT_THRESHOLD = 5000.0

# 拆分份数上限(并行度——对齐 69号
# AMOUNT_BANDS ¥20000 档: 2 万内 2 份
# /2 万以上 3 份)
SPLIT_MAX_PARTS = 3

# 拆分确认令牌 TTL(秒——单次消费口径)
SPLIT_CONFIRM_TTL = 1800

# 拆分建议书状态机(封闭——确认仅生成
# 建议包 executed=False, 资金执行永远
# 60号收银台显式调用铁律)
SPLIT_STATES = (
    "proposed",    # 已建议(待用户确认)
    "confirmed",   # 用户已确认(令牌
                   # 已消费——建议包)
    "rejected",   # 用户已拒绝(留痕)
)

# 重试退避序列(确定性毫秒——支付失败
# 案例重试策略, 避免无效请求堆积)
RETRY_BACKOFF_MS = (800, 1600, 3200)

# 重试上限(与 60号 P3 补单重试对齐)
RETRY_MAX_ATTEMPTS = 3

# ============================================================
# 四维调配目标域(P3 帕累托口径预注册
# ——确定性向量评分, LLM 禁入)
# ============================================================

ALLOCATION_DIMENSIONS = (
    "cost",         # 成本维(费率越低越优)
    "success",      # 成功率维(健康度)
    "experience",   # 体验维(时效/摩擦感)
    "compliance",   # 合规维(规则表校验)
)

# 四维基准权重(封闭注册——变更走慢环
# 46号审批; 情境权重规则表在 P3 展开)
ALLOCATION_BASE_WEIGHTS: dict = {
    "cost": 0.25,
    "success": 0.35,
    "experience": 0.20,
    "compliance": 0.20,
}

# 情境档位域(P3 帕累托选解的情境
# 权重规则表——P0 预注册口径)
ALLOCATION_CONTEXTS = (
    "price_sensitive",   # 价格敏感商户
                         # (成本维主导)
    "high_value",         # 高价值用户
                          # (体验维主导)
    "large_amount",       # 大额场景
                          # (合规维主导)
    "balanced",           # 均衡(默认)
)

# ============================================================
# P3 帕累托调配(四维向量+支配分析
# ——确定性, LLM 禁入; 调配结果仅作
# 建议注入 69号 P1 路由情境参考,
# 永不直接执行路由铁律)
# ============================================================

# 情境权重规则表(封闭——每档四维权重
# 和=1.0; 变更走外部信号建议书+admin
# 终审, 永不自动)
ALLOCATION_CONTEXT_WEIGHTS: dict = {
    "price_sensitive": {
        "cost": 0.45, "success": 0.30,
        "experience": 0.10,
        "compliance": 0.15},
    "high_value": {
        "cost": 0.15, "success": 0.30,
        "experience": 0.40,
        "compliance": 0.15},
    "large_amount": {
        "cost": 0.05, "success": 0.30,
        "experience": 0.15,
        "compliance": 0.50},
    "balanced": {
        "cost": 0.25, "success": 0.35,
        "experience": 0.20,
        "compliance": 0.20},
}

# 合规维评分(封闭——端口×确定性规则表;
# 大额场景合规维主导时 bank/unionpay 优)
ALLOCATION_COMPLIANCE: dict = {
    "qr": 0.75,
    "wechat": 0.85,
    "alipay": 0.85,
    "bank": 0.95,
    "unionpay": 0.90,
    "credit_tv": 0.70,
    "biometric": 0.80,
}

# 体验维时效封顶(小时——settleHours
# ≥此值体验分为 0; 69号注册表最慢 24h)
ALLOCATION_SETTLE_CAP_HOURS = 24

# 外部信号种类域(封闭——费率调整/
# 汇率波动/渠道政策变化)
EXTERNAL_SIGNAL_KINDS = (
    "fee_change",       # 费率调整
    "fx_fluctuation",  # 汇率波动
    "policy_change",    # 渠道政策变化
)

# 外部信号→情境维度偏移(封闭映射
# ——确定性建议: 目标维+delta 且从
# 最大其他维扣减, 权重和守恒)
EXTERNAL_SIGNAL_SHIFTS: dict = {
    "fee_change": (
        "price_sensitive", "cost", 0.05),
    "fx_fluctuation": (
        "balanced", "cost", 0.05),
    "policy_change": (
        "large_amount", "compliance", 0.05),
}

# 外部信号建议书状态机(封闭——外部
# 信号→建议书→admin 终审, 权重变更
# 永不自动铁律)
EXTERNAL_STATES = (
    "proposed",    # 已建议(待终审)
    "approved",    # admin 确认生效
                   #(权重覆盖激活)
    "rejected",    # admin 拒绝留痕
)

# ============================================================
# P4 对账自愈(T+0 笔级三向核验+幂等域
# 自动重试补单——冲正/退款/大额终审
# 永远 60号 P3 人工铁律)
# ============================================================

# 三向核验源(封闭——平台订单/支付
# 流水/渠道回执)
VERIFY_SOURCES = (
    "order",       # 平台订单(60号
                   # 归因链)
    "flow",        # 支付流水(60号
                   # pay60_flows)
    "receipt",     # 渠道回执(60号
                   # 渠道回执)
)

# 核验结果域(封闭)
VERIFY_STATES = (
    "matched",     # 三方匹配(一致)
    "mismatch",    # 差异(需分类处置)
)

# 差错分类域(封闭——消费 60号 P3
# 分类范式: 金额/订单号/时间窗三方
# 匹配的确定性分类)
DISCREPANCY_KINDS = (
    "amount_diff",        # 金额不符
    "timeout_no_callback",  # 超时未回调
    "duplicate_charge",   # 重复扣款
    "partial_refund",     # 部分退款
)

# 补单状态机(封闭——幂等域自动
# + 留痕; 冲正/退款走 60号 P3 人工)
RECON_STATES = (
    "pending",      # 差异待处置
    "retrying",     # 幂等重试中
    "auto_healed",  # 自动补单成功
    "manual_referral",  # 转人工
                       # (60号 P3)
    "failed",       # 重试上限耗尽
                    # (转人工)
)

# 幂等重试上限(对齐 60号 P3 补单
# 重试惯例——上限 3 次)
RECON_RETRY_MAX = 3

# 幂等重试退避(秒——确定性序列)
RECON_RETRY_BACKOFF_S = (60, 300, 900)

# 差错自动处置映射(封闭——仅幂等
# 修复类可自动: timeout_no_callback
# 走重试补单; 其余全部转 60号 P3
# 人工终审铁律)
RECON_AUTO_KINDS = (
    "timeout_no_callback",
)

# 渠道对账延迟基线观测档(封闭——
# 快环统计口径; 阈值变更走慢环审批)
RECON_DELAY_BANDS = (
    (60, "instant"),        # ≤60s 即时
    (3600, "fast"),         # ≤1h 快速
    (float("inf"), "slow"),  # >1h 缓慢
)

# ============================================================
# P5 风控叙事(69号 P2 熵判定之上的
# 认知解释层——判定零重复; 叙事数字
# 100% 查询层插值, LLM 不产数字)
# ============================================================

# 因果规则表(封闭——环境/情境模式
# →合法解释 vs 谨慎归因; 区分"真欺诈"
# 与"异常但合法"; 变更走慢环 46号审批)
CAUSAL_RULES: dict = {
    "night_virtual_new_device": {
        "label": "凌晨+陌生设备",
        "signals": ("odd_hour",
                    "new_device"),
        "verdict": "caution",
        "note": "凌晨时段+陌生设备——"
                "凭证盗用高发组合",
    },
    "business_trip": {
        "label": "差旅场景",
        "signals": ("new_location",),
        "verdict": "legitimate",
        "note": "IP 突变+差旅——出差"
                "常致异地支付(异常但合法)",
    },
    "family_gift": {
        "label": "节日送礼",
        "signals": ("odd_hour",),
        "verdict": "legitimate",
        "note": "夜间大额——节日前"
                "为家人购礼常见",
    },
    "new_device_only": {
        "label": "新设备登录",
        "signals": ("new_device",),
        "verdict": "caution",
        "note": "陌生设备——建议"
                "二次确认",
    },
}

# 因果判定域(封闭二值)
CAUSAL_VERDICTS = (
    "caution",       # 谨慎(真欺诈
                     # 方向信号)
    "legitimate",    # 合法(异常但
                     # 合法解释)
)

# 欺诈手法域(封闭——案例库归档分类)
FRAUD_PATTERNS = (
    "credential_theft",   # 凭证盗用
    "account_takeover",   # 账户接管
    "collusive_cashout",  # 串通套现
    "stolen_device",      # 设备盗用
)

# 案例状态机(封闭)
INCIDENT_STATES = (
    "pending",            # 待核实
    "confirmed_fraud",    # 已确认欺诈
    "confirmed_legit",    # 已确认合法
                          # (误拦平反)
)

# 误拦归因类型域(封闭——回流观测;
# 熵轴权重建议书走 P7→46号审批)
MISJUDGE_KINDS = (
    "context_blind",     # 情境盲(差旅/
                         # 送礼被拦)
    "baseline_stale",    # 基线过期
                         # (新习惯)
    "threshold_high",    # 阈值偏高
)

# 脱敏前缀(案例归档——memberId 哈希
# 化, 原始身份永不入库铁律)
MASK_PREFIX = "m-"
MASK_LENGTH = 8

# 叙事模板(封闭——确定性模板拼接;
# 占位符由查询层数值插值):
#   {amount}/{entropy}/{step}/{axis}
#   {channel}/{tier}/{verdict}
NARRATIVE_TEMPLATES: dict = {
    "free": (
        "会员 {member} 金额 ¥{amount} 经"
        "通道 {channel} 评估: 风险熵 "
        "{entropy}(信值档 {tier}), 六轴"
        "中金额轴 {amount_axis}/环境轴"
        " {env_axis}; 判定档位 {step}"
        "(免密)——情境 {verdict}。"
        "依据: 低熵小额+无环境异常。"),
    "elevated": (
        "会员 {member} 金额 ¥{amount} 经"
        "通道 {channel} 评估: 风险熵 "
        "{entropy}(信值档 {tier}), 六轴"
        "中金额轴 {amount_axis}/环境轴"
        " {env_axis}/行为轴 {behavior_axis}"
        "; 判定档位 {step}(增强认证)。"
        "情境 {verdict}——环境信号"
        "{signals} 命中因果规则"
        " {causal}。依据: {note}"),
}

# ============================================================
# P6 审计透明(决策链路图+监管证据链
# 哈希锚定+合规健康报告——只读导出,
# 永不修改原始留痕铁律)
# ============================================================

# 证据链节点域(封闭——71号决策链
# 全链节点: 意图→预判→调配→执行参考
# →核验→叙事)
EVIDENCE_NODES = (
    "intent",      # 意图预判(P2
                   # prediction)
    "allocation",  # 帕累托调配(P3)
    "entropy",     # 熵判定参考(69号
                   # P2——只读)
    "verify",      # 三向核验(P4)
    "narrative",   # 风控叙事(P5)
)

# 证据链哈希锚字段(封闭——每节点
# 参与锚定的确定性字段)
EVIDENCE_HASH_FIELDS: dict = {
    "intent": ("predictionSeq",
               "recommendedChannel",
               "amount", "engine"),
    "allocation": ("allocationSeq",
                   "recommended",
                   "context",
                   "weightsSource"),
    "entropy": ("entropySeq", "entropy",
                "step", "riskTier"),
    "verify": ("verifySeq", "state",
               "discrepancyKind"),
    "narrative": ("narrativeSeq",
                  "narrativeRefEntropy",
                  "causalVerdict"),
}

# 证据链状态域(封闭)
EVIDENCE_STATES = (
    "assembled",   # 已组装(哈希锚定)
    "exported",     # 已导出(监管
                    # 留痕)
)

# 链路图节点域(封闭——治理/自愈/
# 调配/核验/叙事五域)
TRACEGRAPH_DOMAINS = (
    "governance",   # 治理(纳管/建议书
                    # /外部信号)
    "selfheal",     # 自愈(端口态
                    # /轨迹)
    "allocation",    # 调配(四维
                    # /帕累托)
    "recon",        # 核验(三向
                    # /补单)
    "narrative",    # 叙事(归因
                    # /案例)
)

# 合规健康报告状态机(封闭)
REPORT_STATES = (
    "drafted",    # 已生成(观测)
    "published",  # 已发布(留痕
                  # ——非资金动作)
)

# 报告周期(封闭——观测口径)
REPORT_PERIODS = (
    "daily",    # 日报
    "weekly",   # 周报
)

# 合规健康报告维度(封闭——五维
# 确定性统计)
REPORT_DIMENSIONS = (
    "portCompliance",   # 端口合规分布
                        # (冻结/降级/
                        # 熔断计数)
    "selfhealActions",  # 自愈动作分布
    "reconOutcome",     # 对账结果分布
    "narrativeVerdicts",  # 叙事判定
                          # 分布
    "redLineTouches",   # 红线触碰
                        # (宪法级——
                        # 恒 0 断言
)

# ============================================================
# P7 三层进化引擎(快适应/深反思/元
# 认知——69号 P7 双环范式平移+三层
# 宪法化; 进化永不自动生效铁律)
# ============================================================

# 治理分级域(封闭——L0 观察默认/
# L1 受限(低风险)/L2 协同(权重类))
EVOLUTION_LEVELS = (
    "L0",   # 观察学习: 漂移检测+
            # 假设生成(不提交 46号)
    "L1",   # 受限进化: 低风险参数
            # 建议书可提交 46号
            # (仍需人工审批)
    "L2",   # 协同进化: 全参数域
            # 建议书可提交 46号
)


def current_level() -> str:
    """治理分级(PAY71_EVOLUTION_LEVEL,
    默认 L0)"""
    lv = os.environ.get(
        "PAY71_EVOLUTION_LEVEL") or "L0"
    return lv if lv in EVOLUTION_LEVELS \
        else "L0"


# 可进化参数白名单(封闭——版本化
# 基线管理域; kind: weights=权重
# 和=1.0 宪法校验/dict=同键对象/
# scalar=域内标量)
EVOLVABLE_PARAMS: dict = {
    "allocationContextWeights": {
        "riskLevel": "high",   # L2 才可提
        "kind": "weights-family",
        "factory": None,   # 四情境各自
                           # 独立(见
                           # ALLOCATION_
                           # CONTEXT_
                           # WEIGHTS)
        "domain": "帕累托情境权重"
                  "(与 P3 外部信号覆盖"
                  "互斥——进化走 P3 轨)",
    },
    "precursorFuseThreshold": {
        "riskLevel": "high",
        "kind": "scalar",
        "factory": 0.80,
        "domain": "端口熔断阈值",
        "vmin": 0.50, "vmax": 1.0,
    },
    "probeRequiredSuccesses": {
        "riskLevel": "low",    # L1 可提
        "kind": "scalar",
        "factory": 3,
        "domain": "半开探测恢复次数",
        "vmin": 1, "vmax": 10,
    },
    "reconRetryMax": {
        "riskLevel": "low",
        "kind": "scalar",
        "factory": 3,
        "domain": "补单重试上限",
        "vmin": 1, "vmax": 10,
    },
}

# 参数版本状态机(封闭——52号基线
# 管理灰度范式)
PARAM_VERSION_STATUSES = (
    "draft",    # 草稿(假设附带)
    "shadow",   # 影子(对照——状态
                # 占位)
    "active",   # 生效(消费口径)
    "retired",  # 退役(可回滚目标)
)

# 漂移检测阈值(封闭——快适应层
# 确定性统计口径)
DRIFT_THRESHOLDS: dict = {
    "channelSuccess": 0.05,  # 通道成功
                             # 率偏离幅度
    "misjudgeRate": 0.10,    # 误拦率
                             # 异常幅度
    "minSamples": 20,        # 最小
                             # 样本量
}

# 46号档案口径(第43批——submit_
# change 前置入册)
EVOLUTION_SCORER_ID = \
    "payment_port_intelligence"

# 假设状态机(封闭)
HYPOTHESIS_STATUSES = (
    "proposed",    # 已建议(待提交)
    "submitted",   # 已提交 46号审批
    "published",   # 已发布(版本生效)
    "rejected",    # 46号驳回留痕
)


# ============================================================
# 启动自检(宪法级)
# ============================================================

def _validate_registry() -> None:
    """注册表封闭自检(导入即验——
    RuntimeError 宪法级)"""
    from services.pay69_registry import (
        CHANNEL_IDS,
    )
    expected = set(CHANNEL_IDS)
    # ① 端口域与 69号通道域一致(消费方
    #    铁律——不增不减)
    ports = _port_ids()
    if set(ports) != expected or len(ports) != 7:
        raise RuntimeError(
            f"pay71 端口域与 69号通道域不一致: "
            f"{set(ports) ^ expected}")
    # ② 端口注册表同源只读(键集一致)
    if set(_port_registry()) != expected:
        raise RuntimeError(
            "pay71 端口注册表与 69号源不闭合")
    # ③ 端口态域封闭
    if set(PORT_STATES) != {
            "healthy", "degraded", "broken"}:
        raise RuntimeError(
            "pay71 端口态域非法")
    # ④ 端口态折减系数单调递减+域合法
    if PORT_STATE_FACTORS["healthy"] \
            <= PORT_STATE_FACTORS["degraded"] \
            or PORT_STATE_FACTORS["degraded"] \
            <= PORT_STATE_FACTORS["broken"]:
        raise RuntimeError(
            "pay71 端口态折减系数非单调递减")
    for state, factor in PORT_STATE_FACTORS.items():
        if not (0 <= factor <= 1):
            raise RuntimeError(
                f"pay71 端口态折减系数域外: "
                f"{state}={factor}")
    if set(PORT_STATE_FACTORS) != set(PORT_STATES):
        raise RuntimeError(
            "pay71 端口态与折减系数不闭合")
    # ⑤ 前兆信号域封闭+权重合法
    if set(PRECURSOR_WEIGHTS) \
            != set(PRECURSOR_SIGNALS):
        raise RuntimeError(
            "pay71 前兆信号与权重不闭合")
    for signal, w in PRECURSOR_WEIGHTS.items():
        if not (0 < w <= 1):
            raise RuntimeError(
                f"pay71 前兆信号权重域外: "
                f"{signal}={w}")
    # ⑥ 前兆预警阈值合法
    if not (0 < PRECURSOR_ALERT_THRESHOLD
            <= 1):
        raise RuntimeError(
            "pay71 前兆预警阈值域外")
    # ⑦ 四维调配域封闭+权重和=1.0
    if set(ALLOCATION_BASE_WEIGHTS) \
            != set(ALLOCATION_DIMENSIONS):
        raise RuntimeError(
            "pay71 四维调配维度与权重不闭合")
    atotal = sum(
        ALLOCATION_BASE_WEIGHTS.values())
    if abs(atotal - 1.0) > 1e-9:
        raise RuntimeError(
            f"pay71 四维调配权重和≠1.0: "
            f"{atotal}")
    # ⑧ 情境档位域封闭+非空
    if not ALLOCATION_CONTEXTS \
            or "balanced" not in \
            ALLOCATION_CONTEXTS:
        raise RuntimeError(
            "pay71 情境档位域非法(缺 balanced"
            " 默认档)")
    # ⑨ 模式三态封闭
    if set(MODE_VALUES) != {
            "off", "shadow", "assist"}:
        raise RuntimeError("pay71 模式三态域非法")
    # ⑩ P1 自愈: 熔断阈值>预警阈值
    #     (分级单调)+窗口/探测/影子参数
    #     合法+治理/建议书/动作域封闭
    if not (PRECURSOR_ALERT_THRESHOLD
            < PRECURSOR_FUSE_THRESHOLD <= 1):
        raise RuntimeError(
            "pay71 自愈阈值分级非单调递增")
    if not (1 <= SELFHEAL_WINDOW_SIZE <= 100):
        raise RuntimeError(
            "pay71 前兆滚动窗口容量域外")
    if not (1 <= PROBE_REQUIRED_SUCCESSES <= 10):
        raise RuntimeError(
            "pay71 半开探测次数域外")
    if not (1 <= SHADOW_DAYS <= 30):
        raise RuntimeError(
            "pay71 影子冷启动天数域外")
    if set(GOVERNANCE_STATES) != {
            "ungoverned", "shadow",
            "active", "offboarded"}:
        raise RuntimeError(
            "pay71 端口治理生命周期域非法")
    if set(PROPOSAL_STATES) != {
            "proposed", "approved",
            "rejected"}:
        raise RuntimeError(
            "pay71 建议书状态机非法")
    if set(PROPOSAL_KINDS) != {
            "promote", "offboard"}:
        raise RuntimeError(
            "pay71 建议书种类域非法")
    if set(SELFHEAL_ACTIONS) != {
            "degrade", "fuse", "recover",
            "probe_pass", "probe_fail",
            "no_change"}:
        raise RuntimeError(
            "pay71 自愈动作域非法")
    # ⑪ P2 预判: 拆分参数/状态机/退避
    #     序列/习惯样本下限合法
    if SPLIT_THRESHOLD <= 0:
        raise RuntimeError(
            "pay71 拆分阈值域外")
    if not (2 <= SPLIT_MAX_PARTS <= 5):
        raise RuntimeError(
            "pay71 拆分份数上限域外")
    if not (60 <= SPLIT_CONFIRM_TTL
            <= 86400):
        raise RuntimeError(
            "pay71 拆分确认 TTL 域外")
    if set(SPLIT_STATES) != {
            "proposed", "confirmed",
            "rejected"}:
        raise RuntimeError(
            "pay71 拆分状态机非法")
    if not RETRY_BACKOFF_MS or any(
            b <= 0 for b in
            RETRY_BACKOFF_MS):
        raise RuntimeError(
            "pay71 重试退避序列非法")
    for i in range(1, len(RETRY_BACKOFF_MS)):
        if RETRY_BACKOFF_MS[i] \
                <= RETRY_BACKOFF_MS[i - 1]:
            raise RuntimeError(
                "pay71 重试退避序列非递增")
    if RETRY_MAX_ATTEMPTS < 1:
        raise RuntimeError(
            "pay71 重试上限域外")
    if PREDICT_MIN_SAMPLES < 1:
        raise RuntimeError(
            "pay71 习惯样本下限域外")
    # ⑫ P3 帕累托: 情境权重表闭合(每档
    #     和=1.0+维度域一致)+合规分覆盖
    #     全端口+外部信号映射合法
    if set(ALLOCATION_CONTEXT_WEIGHTS) \
            != set(ALLOCATION_CONTEXTS):
        raise RuntimeError(
            "pay71 情境权重表与档位域不闭合")
    for ctx, weights in \
            ALLOCATION_CONTEXT_WEIGHTS.items():
        if set(weights) \
                != set(ALLOCATION_DIMENSIONS):
            raise RuntimeError(
                f"pay71 情境 {ctx} 权重维度"
                f"域非法")
        wtotal = sum(weights.values())
        if abs(wtotal - 1.0) > 1e-9:
            raise RuntimeError(
                f"pay71 情境 {ctx} 权重和"
                f"≠1.0: {wtotal}")
        for dim, w in weights.items():
            if not (0 < w < 1):
                raise RuntimeError(
                    f"pay71 情境 {ctx} 权重"
                    f"域外: {dim}={w}")
    from services.pay69_registry import (
        CHANNEL_IDS as _p69_ids,
    )
    if set(ALLOCATION_COMPLIANCE) \
            != set(_p69_ids):
        raise RuntimeError(
            "pay71 合规评分表不覆盖全端口")
    for pid, score in \
            ALLOCATION_COMPLIANCE.items():
        if not (0 < score <= 1):
            raise RuntimeError(
                f"pay71 合规分域外: "
                f"{pid}={score}")
    if not (1 <= ALLOCATION_SETTLE_CAP_HOURS
            <= 48):
        raise RuntimeError(
            "pay71 体验维时效封顶域外")
    if set(EXTERNAL_SIGNAL_KINDS) != {
            "fee_change",
            "fx_fluctuation",
            "policy_change"}:
        raise RuntimeError(
            "pay71 外部信号种类域非法")
    if set(EXTERNAL_SIGNAL_SHIFTS) \
            != set(EXTERNAL_SIGNAL_KINDS):
        raise RuntimeError(
            "pay71 外部信号映射不闭合")
    for kind, (ctx, dim, delta) in \
            EXTERNAL_SIGNAL_SHIFTS.items():
        if ctx not in \
                ALLOCATION_CONTEXT_WEIGHTS:
            raise RuntimeError(
                f"pay71 外部信号 {kind} "
                f"情境域外: {ctx}")
        if dim not in ALLOCATION_DIMENSIONS:
            raise RuntimeError(
                f"pay71 外部信号 {kind} "
                f"维度域外: {dim}")
        if not (0 < delta <= 0.10):
            raise RuntimeError(
                f"pay71 外部信号 {kind} "
                f"偏移量域外: {delta}")
    if set(EXTERNAL_STATES) != {
            "proposed", "approved",
            "rejected"}:
        raise RuntimeError(
            "pay71 外部信号状态机非法")
    # ⑬ P4 对账自愈: 核验源/结果域/
    #     差错分类/补单状态机/重试参数/
    #     自动处置映射/延迟档合法
    if set(VERIFY_SOURCES) != {
            "order", "flow", "receipt"}:
        raise RuntimeError(
            "pay71 三向核验源域非法")
    if set(VERIFY_STATES) != {
            "matched", "mismatch"}:
        raise RuntimeError(
            "pay71 核验结果域非法")
    if set(DISCREPANCY_KINDS) != {
            "amount_diff",
            "timeout_no_callback",
            "duplicate_charge",
            "partial_refund"}:
        raise RuntimeError(
            "pay71 差错分类域非法")
    if set(RECON_STATES) != {
            "pending", "retrying",
            "auto_healed",
            "manual_referral",
            "failed"}:
        raise RuntimeError(
            "pay71 补单状态机非法")
    if not (1 <= RECON_RETRY_MAX <= 10):
        raise RuntimeError(
            "pay71 补单重试上限域外")
    if not RECON_RETRY_BACKOFF_S or any(
            b <= 0 for b in
            RECON_RETRY_BACKOFF_S):
        raise RuntimeError(
            "pay71 补单重试退避非法")
    for i in range(
            1, len(RECON_RETRY_BACKOFF_S)):
        if RECON_RETRY_BACKOFF_S[i] \
                <= RECON_RETRY_BACKOFF_S[
                    i - 1]:
            raise RuntimeError(
                "pay71 补单退避序列非递增")
    if not set(RECON_AUTO_KINDS) \
            <= set(DISCREPANCY_KINDS):
        raise RuntimeError(
            "pay71 自动处置映射域外")
    if "duplicate_charge" in \
            RECON_AUTO_KINDS \
            or "amount_diff" \
            in RECON_AUTO_KINDS:
        raise RuntimeError(
            "pay71 资金类差错永不自动"
            "——铁律违反")
    for i in range(
            1, len(RECON_DELAY_BANDS)):
        if RECON_DELAY_BANDS[i][0] \
                <= RECON_DELAY_BANDS[
                    i - 1][0]:
            raise RuntimeError(
                "pay71 对账延迟档非递增")
    # ⑭ P5 风控叙事: 因果规则域封闭
    #     (signals 环境标志域内+verdict
    #     域内)+手法/案例/误拦域封闭+
    #     模板占位符合法
    _env_flags = {
        "odd_hour", "new_device",
        "new_location"}
    if set(CAUSAL_VERDICTS) != {
            "caution", "legitimate"}:
        raise RuntimeError(
            "pay71 因果判定域非法")
    for rid, rule in \
            CAUSAL_RULES.items():
        if not set(rule["signals"]) \
                <= _env_flags:
            raise RuntimeError(
                f"pay71 因果规则 {rid} "
                f"环境信号域外")
        if rule["verdict"] \
                not in CAUSAL_VERDICTS:
            raise RuntimeError(
                f"pay71 因果规则 {rid} "
                f"判定域外")
        if not rule.get("note"):
            raise RuntimeError(
                f"pay71 因果规则 {rid} "
                f"缺依据说明")
    # 双信号规则须先于单信号匹配
    # (特异性优先——确定性顺序)
    _sizes = [len(r["signals"])
              for r in
              CAUSAL_RULES.values()]
    if _sizes != sorted(
            _sizes, reverse=True):
        raise RuntimeError(
            "pay71 因果规则须按特异性"
            "降序排列")
    if set(FRAUD_PATTERNS) != {
            "credential_theft",
            "account_takeover",
            "collusive_cashout",
            "stolen_device"}:
        raise RuntimeError(
            "pay71 欺诈手法域非法")
    if set(INCIDENT_STATES) != {
            "pending",
            "confirmed_fraud",
            "confirmed_legit"}:
        raise RuntimeError(
            "pay71 案例状态机非法")
    if set(MISJUDGE_KINDS) != {
            "context_blind",
            "baseline_stale",
            "threshold_high"}:
        raise RuntimeError(
            "pay71 误拦归因域非法")
    if not (4 <= MASK_LENGTH <= 16):
        raise RuntimeError(
            "pay71 脱敏长度域外")
    if set(NARRATIVE_TEMPLATES) != {
            "free", "elevated"}:
        raise RuntimeError(
            "pay71 叙事模板域非法")
    # ⑮ P6 审计透明: 证据链节点域/
    #     锚字段闭合/状态机/链路图域/
    #     报告状态机+周期+维度封闭
    if set(EVIDENCE_NODES) != {
            "intent", "allocation",
            "entropy", "verify",
            "narrative"}:
        raise RuntimeError(
            "pay71 证据链节点域非法")
    if set(EVIDENCE_HASH_FIELDS) \
            != set(EVIDENCE_NODES):
        raise RuntimeError(
            "pay71 证据链锚字段不闭合")
    for node, fields in \
            EVIDENCE_HASH_FIELDS.items():
        if not fields or not all(
                isinstance(f, str)
                for f in fields):
            raise RuntimeError(
                f"pay71 证据链 {node} "
                f"锚字段非法")
    if set(EVIDENCE_STATES) != {
            "assembled", "exported"}:
        raise RuntimeError(
            "pay71 证据链状态域非法")
    if set(TRACEGRAPH_DOMAINS) != {
            "governance", "selfheal",
            "allocation", "recon",
            "narrative"}:
        raise RuntimeError(
            "pay71 链路图节点域非法")
    if set(REPORT_STATES) != {
            "drafted", "published"}:
        raise RuntimeError(
            "pay71 报告状态机非法")
    if set(REPORT_PERIODS) != {
            "daily", "weekly"}:
        raise RuntimeError(
            "pay71 报告周期域非法")
    if set(REPORT_DIMENSIONS) != {
            "portCompliance",
            "selfhealActions",
            "reconOutcome",
            "narrativeVerdicts",
            "redLineTouches"}:
        raise RuntimeError(
            "pay71 报告维度域非法")
    # ⑯ P7 进化引擎: 分级/参数白名单/
    #     版本状态机/漂移阈值/假设状态
    if set(EVOLUTION_LEVELS) != {
            "L0", "L1", "L2"}:
        raise RuntimeError(
            "pay71 进化分级域非法")
    if not EVOLVABLE_PARAMS:
        raise RuntimeError(
            "pay71 可进化参数白名单为空")
    for pid, meta in \
            EVOLVABLE_PARAMS.items():
        if meta.get("riskLevel") not in (
                "low", "high"):
            raise RuntimeError(
                f"pay71 参数风险级非法: "
                f"{pid}")
        if meta.get("kind") not in (
                "weights-family",
                "scalar"):
            raise RuntimeError(
                f"pay71 参数类型非法: "
                f"{pid}")
        if meta.get("kind") == "scalar":
            if "factory" not in meta:
                raise RuntimeError(
                    f"pay71 参数缺出厂默认: "
                    f"{pid}")
            if not (meta.get("vmin")
                    < meta.get("vmax")):
                raise RuntimeError(
                    f"pay71 参数值域非法: "
                    f"{pid}")
    if set(PARAM_VERSION_STATUSES) != {
            "draft", "shadow",
            "active", "retired"}:
        raise RuntimeError(
            "pay71 参数版本状态域非法")
    for k, v in DRIFT_THRESHOLDS.items():
        if not (0 < v <= 1) \
                and k != "minSamples":
            raise RuntimeError(
                f"pay71 漂移阈值域外: "
                f"{k}={v}")
    if DRIFT_THRESHOLDS["minSamples"] < 1:
        raise RuntimeError(
            "pay71 漂移最小样本量域外")
    if set(HYPOTHESIS_STATUSES) != {
            "proposed", "submitted",
            "published", "rejected"}:
        raise RuntimeError(
            "pay71 假设状态机非法")


_validate_registry()
