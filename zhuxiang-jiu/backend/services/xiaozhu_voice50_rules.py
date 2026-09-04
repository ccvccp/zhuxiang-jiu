"""50号·小竹语音信值积分引擎 行为注册表(voice50_rules)

计划(docs/50号_小竹语音信值积分引擎实施计划.md §六):
    《语音交互信值积分规则细则 v2.0》14 行为全量工程化——
    三层映射(对齐 45号九因子) + 加成链 + 扣分项 + 日限。

三轨架构(计划 §三-2 裁定):
    - L1 实时轨: 台账+风控域(不入 45号法治因子——防交互域
      信号污染 legal_record/regulatory/asset_integrity)
    - L2/L3 T+1 轨: 结算清洗后聚合走 45号 submit_deposit
      验真(ethics_evidence/contribution_net/longtail_good)
    - 台账轨(本体): 全部行为先入 voice50 独立台账, 防刷封顶
      在台账层消化后才具备桥接资格

启动自检红线:
    - L2/L3 行为的 targetFactor 必须在 45号九因子注册表内
      且层归属一致(LAYER_OF)——九因子封闭注册表不可新增
    - L1 行为 voiceFactor 独立命名(voice_*——不与 45号因子
      冲突, 桥接轨道硬编码不接 L1)

设计红线(计划 §八 10-14):
    - 积分独立台账: 入信值必经 deposit 验真
    - 封顶先于桥接: 超限溢出(×0.1)只入池不进信值
"""

import logging

logger = logging.getLogger("xiaozhu_voice50_rules")

# 引擎总开关(默认 off——48号 turn 钩子空转, 零影响交付)
DEFAULT_MODE = "off"

# 声纹验证模式(计划 §三-3 双态: proxy 加成只入台账;
# real(SDK 后)才可入 T+1 桥接轨道)
VOICEPRINT_PROXY = "proxy"
VOICEPRINT_REAL = "real"

# 防刷封顶参数(计划 §六: 日上限=滚动 7 日日均基线×3,
# 超限部分 ×0.1)
CAP_BASELINE_WINDOW = 7
CAP_MULTIPLIER = 3.0
CAP_OVERFLOW_RATE = 0.1

# 声纹验证系数(v2.0 §一-1)
VOICEPRINT_VERIFIED = 1.5     # 声纹+活体验证通过
VOICEPRINT_UNVERIFIED = 0.3   # 未通过验证

# 质量加权口径(v2.0 §一-2: 有效意图置信度 ≥0.8 计分)
QUALITY_THRESHOLD = 0.8

# L1 降级阈值(累计扣分 >20 → 台账 frozen——只冻结积分域,
# 绝不阻断语音入口; 计划 §三-4)
L1_DEGRADE_THRESHOLD = 20.0

# 新用户冷启动(P3): 首月 L3 上浮 50%
NEWCOMER_L3_BOOST = 1.5
NEWCOMER_WINDOW_DAYS = 30

# 激励池衰减(P5): 90 天无语音交互月衰减 5%, 保底 30%——
# 只作用池, 绝不碰已入账信值(计划 §三-1)
DECAY_IDLE_DAYS = 90
DECAY_MONTHLY_RATE = 0.05
DECAY_FLOOR = 0.30

# 修复对冲上限(P5): 池余额抵扣历史违规 ≤50%/次
OFFSET_MAX_RATIO = 0.5


# ============================================================
# 行为注册表(14 行为——v2.0 §二/§三/§四 全量)
# ============================================================

VOICE_RULES = {
    # ---------- L1 实时轨(台账+风控域, 不入 45号) ----------
    "voice_login": {
        "label": "声纹登录验证",
        "layer": "L1",
        "voiceFactor": "voice_login",
        "base": 2.0,
        "gain": {"liveness": 1.5},
        "penalty": -5.0,       # 声纹疑似合成
        "dailyCap": 6,
        "trigger": "语音唤醒+声纹比对成功",
    },
    "voice_confirm": {
        "label": "敏感操作语音确认",
        "layer": "L1",
        "voiceFactor": "voice_confirm",
        "base": 3.0,
        "gain": {"dualFactor": 2.0},   # 语义+声纹双因子
        "penalty": -1.0,       # 确认后撤销
        "dailyCap": 10,
        "trigger": "修复/兑换/授权等高危操作语音确认",
    },
    "voice_env_verify": {
        "label": "异常环境自适应验证",
        "layer": "L1",
        "voiceFactor": "voice_env_verify",
        "base": 5.0,
        "gain": {"firstPass": 1.5},
        "penalty": None,
        "penaltyFactor": 0.5,  # 多次失败后成功 ×0.5
        "dailyCap": 3,
        "trigger": "设备/IP变更时主动语音核验",
    },
    "voice_antifraud_coop": {
        "label": "反欺诈配合响应",
        "layer": "L1",
        "voiceFactor": "voice_antifraud_coop",
        "base": 4.0,
        "gain": {"consistency": 1.3},
        "penalty": -3.0,       # 回避/矛盾回答
        "dailyCap": 5,
        "trigger": "系统风控问询时如实语音应答",
    },
    # ---------- L2 T+1 轨(→ 45号 ethics_evidence) ----------
    "voice_clear_intent": {
        "label": "清晰意图表达",
        "layer": "L2",
        "targetFactor": "ethics_evidence",
        "base": 1.0,
        "gain": {"coherence": 1.2},    # 多轮连贯 >0.8
        "penaltyFactor": 0.5,          # 频繁修正/重试
        "dailyCap": 30,
        "trigger": "单次交互意图置信度 ≥0.9 且无需澄清",
    },
    "voice_privacy_grant": {
        "label": "主动隐私授权",
        "layer": "L2",
        "targetFactor": "ethics_evidence",
        "base": 8.0,
        "gain": {"specificScope": 1.3},
        "penalty": -2.0,       # 授权后立即撤回
        "dailyCap": 3,
        "trigger": "明确同意数据用于模型优化/服务改进",
    },
    "voice_feedback": {
        "label": "建设性反馈提交",
        "layer": "L2",
        "targetFactor": "ethics_evidence",
        "base": 6.0,
        "gain": {"adopted": 10.0},     # 建议被采纳 +10
        "penaltyFactor": 0.3,          # 重复/无效反馈
        "dailyCap": 5,
        "trigger": "对语音服务提出具体改进建议(非情绪宣泄)",
    },
    "voice_polite": {
        "label": "礼貌交互习惯",
        "layer": "L2",
        "targetFactor": "ethics_evidence",
        "base": 0.5,
        "gain": {"streak3": 1.5},      # 持续 3 轮+
        "penalty": -10.0,      # 辱骂/威胁
        "dailyCap": None,      # 不限
        "trigger": "使用敬语/感谢词且无攻击性语言",
    },
    "voice_inclusive": {
        "label": "跨文化包容表达",
        "layer": "L2",
        "targetFactor": "ethics_evidence",
        "base": 2.0,
        "gain": {"minorityLang": 2.0},  # 小众语种数据积累
        "penalty": None,
        "dailyCap": 10,
        "trigger": "方言/少数民族语言/外语切换且被正确识别",
    },
    # ---------- L3 T+1 轨(→ contribution_net/
    # longtail_good) ----------
    "voice_fl_gradient": {
        "label": "联邦梯度贡献",
        "layer": "L3",
        "targetFactor": "contribution_net",
        "base": 15.0,
        "gain": {"quality": 1.5},      # 梯度质量 >0.7
        "dailyCap": 50,
        "trigger": "本地训练后上传有效加密梯度(FL 外部待办"
                   "——P3 预留接口)",
        "deferred": True,
    },
    "voice_corpus_donate": {
        "label": "新场景语料捐赠",
        "layer": "L3",
        "targetFactor": "contribution_net",
        "base": 10.0,
        "gain": {"adopted": 20.0},     # 场景纳入训练集
        "dailyCap": 30,
        "trigger": "主动描述未被覆盖的使用场景(人工审核)",
    },
    "voice_evidence": {
        "label": "真伪鉴别辅助验证",
        "layer": "L3",
        "targetFactor": "contribution_net",
        "base": 12.0,
        "gain": {"accepted": 2.0},     # 佐证被采信
        "dailyCap": 24,
        "trigger": "对可疑行为提供语音佐证(per-claim 走 "
                   "45号验真)",
    },
    "voice_community_qa": {
        "label": "社区知识问答",
        "layer": "L3",
        "targetFactor": "longtail_good",
        "base": 8.0,
        "gain": {"liked": 1.5},        # 答案被点赞
        "dailyCap": 40,
        "trigger": "用语音回答其他用户关于信值的疑问",
    },
    "voice_companion": {
        "label": "长期语音伴侣关系",
        "layer": "L3",
        "targetFactor": "longtail_good",
        "base": 100.0,        # 月度奖励
        "gain": {"diversity": 1.3},    # 多样性指数 >0.6
        "dailyCap": None,
        "monthlyCap": 1,
        "trigger": "连续 30 天日均有效交互 ≥3 次",
    },
}


def rules_view() -> dict:
    """规则注册表视图(管理端/自描述)"""
    return {
        "total": len(VOICE_RULES),
        "layers": {
            "L1": sum(1 for r in VOICE_RULES.values()
                      if r["layer"] == "L1"),
            "L2": sum(1 for r in VOICE_RULES.values()
                      if r["layer"] == "L2"),
            "L3": sum(1 for r in VOICE_RULES.values()
                      if r["layer"] == "L3"),
        },
        "rules": {k: {
            "label": v["label"], "layer": v["layer"],
            "base": v["base"], "dailyCap": v["dailyCap"],
            "trigger": v["trigger"],
            "factor": v.get("voiceFactor")
                      or v.get("targetFactor"),
        } for k, v in VOICE_RULES.items()},
        "params": {
            "capMultiplier": CAP_MULTIPLIER,
            "capOverflowRate": CAP_OVERFLOW_RATE,
            "voiceprintVerified": VOICEPRINT_VERIFIED,
            "voiceprintUnverified": VOICEPRINT_UNVERIFIED,
            "qualityThreshold": QUALITY_THRESHOLD,
            "l1DegradeThreshold": L1_DEGRADE_THRESHOLD,
        },
        "note": "三轨: L1 实时台账+风控域(不入 45号法治因子); "
                "L2/L3 T+1 经 deposit 验真入信值; 封顶先于桥接",
    }


def _validate_rules() -> None:
    """启动自检: L2/L3 行为因子必须对齐 45号九因子注册表

    红线: 45号九因子封闭注册表(L1×3/L2×3/L3×3)不可新增——
    语音行为只能映射到既有因子; L1 行为留在风控域不映射。
    """
    from services.trust_scoring_service import TrustValueScorer
    layer_of = TrustValueScorer.LAYER_OF
    for behavior, rule in VOICE_RULES.items():
        layer = rule["layer"]
        if layer == "L1":
            if rule.get("targetFactor"):
                raise RuntimeError(
                    f"voice50 规则不一致: L1 行为 {behavior} "
                    f"不得映射 45号因子(防法治域污染)")
            continue
        factor = rule.get("targetFactor")
        if factor not in layer_of:
            raise RuntimeError(
                f"voice50 规则不一致: {behavior} 目标因子 "
                f"{factor} 不在 45号九因子注册表")
        if layer_of[factor] != layer:
            raise RuntimeError(
                f"voice50 规则不一致: {behavior} 声明 {layer} "
                f"但因子 {factor} 属于 {layer_of[factor]}")


_validate_rules()
