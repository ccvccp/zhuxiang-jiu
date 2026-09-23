"""78号·悦声灵犀 JoyVoice —— 愉悦感语音交互大模型(48号小竹链增强层)

三 Phase(对应用户方案"悦声 JoyVoice"的愉悦感三维度):
    P1 情感 TTS: cogtts 音色档案(多音色人格) + 场景情绪标签
       (cheerful/warm/steady/care)→前端 TTS 语调路由
    P2 愉悦交互: 容错话术温暖化("信号有点小差"式替代冷冰冰
       "我没听懂") + 选项引导(suggest chips 点哪发哪)
    P3 情绪共鸣: 用户语气轻量规则识别(负面/犹豫/积极/中性)
       → 回复话术与语调自适应

实证红线(生产 spike 2026-09-23, spike_joyvoice_voices.py 两轮):
    cogtts /audio/speech 的 voice 枚举仅 9 个有效名——未知名
    HTTP 400 硬拒绝(非静默回退, 官方 GLM-TTS 文档枚举交叉
    验证一致): tongtong(彤彤·默认) / xiaochen(小陈) / male /
    female / chuichui(锤锤) / jam / kazi / douji / luodo
    (后四为"动动动物圈"角色音色——档案策展暂不收录, 留扩展)。

设计约束:
    - 纯规则零 LLM 增量成本(情绪识别不上模型——48号智能轨
      的 context 注入由调用方拼, 本模块只产标签)
    - 数字/事实红线不动: 情绪只改"语气", 绝不改执行层数字
    - XIAOZHU_JOYVOICE_MODE=off 一键回归旧行为(kill-switch)
"""

import os

# ------------------------------------------------------------
# P1 音色档案
# ------------------------------------------------------------

# spike 实证 cogtts 可用音色全集(未命名一律 400)
COGTTS_VOICES_VERIFIED = (
    "tongtong", "xiaochen", "male", "female", "chuichui",
    "jam", "kazi", "douji", "luodo",
)

# 音色人格档案(id 即 cogtts voice 名, 前端透传 /tts?voice=)
# 策展 6 档: 覆盖用户方案"专业顾问/邻家好友/活力达人"三原型
JOYVOICE_PROFILES = (
    {"id": "tongtong", "name": "悦悦",
     "desc": "亲切甜暖 · 默认", "tag": "邻家好友"},
    {"id": "xiaochen", "name": "小陈",
     "desc": "干练清晰", "tag": "专业顾问"},
    {"id": "chuichui", "name": "锤锤",
     "desc": "低沉温和", "tag": "沉稳暖男"},
    {"id": "female", "name": "小灵",
     "desc": "知性柔美", "tag": "温柔女声"},
    {"id": "male", "name": "竹君",
     "desc": "沉稳大气", "tag": "稳重男声"},
    {"id": "luodo", "name": "乐乐",
     "desc": "活泼灵动", "tag": "活力达人"},
)

# ------------------------------------------------------------
# P3 情绪词表(轻量规则——不上 LLM)
# ------------------------------------------------------------

# 负面情绪词(命中即 userMood=negative: 先关怀再引导)
NEGATIVE_MOOD_WORDS = (
    "生气", "气死", "烦死", "烦人", "讨厌", "失望", "垃圾",
    "骗子", "骗人", "坑人", "太差", "差劲", "投诉", "退货",
    "慢死", "卡死", "没用", "无语", "受不了", "啥破",
)
# 犹豫情绪词(主动帮挑——"需要我再帮您挑挑吗";
# 不收泛问词"怎么样"——"明天天气怎么样"是提问非购物
# 犹豫, 命中会误触发帮挑文案(真机回归实证))
HESITANT_MOOD_WORDS = (
    "纠结", "犹豫", "不知道选", "不知道买", "不知道挑",
    "哪个好", "选哪个", "挑哪个", "拿不定", "帮挑", "帮选",
    "难选", "推荐哪个", "怎么选",
)
# 积极情绪词(自然轻快回应, 不刻意讨好)
POSITIVE_MOOD_WORDS = (
    "喜欢", "开心", "满意", "不错", "好用", "好听",
    "厉害", "真棒", "点赞", "舒服", "好耶",
)


def joyvoice_mode_enabled() -> bool:
    """78号总开关(默认 on——full 上线; off 一键回归旧行为)"""
    return os.environ.get(
        "XIAOZHU_JOYVOICE_MODE", "on"
    ).strip().lower() not in ("off", "0", "false")


def detect_user_mood(text: str) -> str:
    """P3 用户语气情绪识别(纯规则, 零 LLM 成本)

    优先级: negative > hesitant > positive > neutral
    (负面里犹豫/积极假阳性代价高——负面必须最先判)

    Returns: "negative" | "hesitant" | "positive" | "neutral"
    """
    t = str(text or "")
    if not t:
        return "neutral"
    if any(w in t for w in NEGATIVE_MOOD_WORDS):
        return "negative"
    if any(w in t for w in HESITANT_MOOD_WORDS):
        return "hesitant"
    if any(w in t for w in POSITIVE_MOOD_WORDS):
        return "positive"
    return "neutral"


def mood_for_turn(intent: str, card_type: str,
                  user_mood: str) -> str:
    """P1 播报情绪标签(场景×用户情绪→TTS 语调路由)

    前端语调映射: care 柔慢×0.92 / steady 稳重×0.95 /
    cheerful 明快×1.02 / ""(空) 跟随用户音色默认语速。

    红线: 执行轮(加购/结算成功等)返回 ""——数字事实播报
    不加情绪渲染, 只有兜底/唤醒/高敏确认/用户负面四处落。
    """
    if intent == "asr_failed" or user_mood == "negative":
        return "care"
    if intent == "wakeup":
        return "cheerful"
    if (card_type == "confirm"
            or intent in ("cart.submit", "order.pay")):
        return "steady"
    return ""


def fallback_suggests(has_product: bool) -> list[str]:
    """P2 兜底轮选项引导集(前端 suggest chips 点哪发哪)

    商品语境给购买决策选项(免唤醒窗口内直接可发),
    无语境给快捷指令("小竹"前缀自带唤醒)。
    """
    if has_product:
        return ["需要", "换一款", "问价格"]
    return ["小竹，看新品", "小竹，查订单", "你能干什么"]


def mood_context_line(user_mood: str) -> str:
    """P3 智能轨(LLM chat)上下文情绪注入行(48号 context_desc 拼接)"""
    return {
        "negative": "用户当前情绪: 不满/低落——reply 先一句温和关怀"
                    "再引导, 绝不冷冰冰不说教",
        "hesitant": "用户当前情绪: 犹豫纠结——reply 主动帮挑"
                    "(给明确建议或反问口味/预算)",
        "positive": "用户当前情绪: 积极愉快——reply 可自然轻快, "
                    "不刻意讨好",
    }.get(user_mood, "")
