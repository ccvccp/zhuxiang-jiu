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
    不加情绪渲染, 只有兜底/高敏确认/用户负面三处落。
    P1.5·竹语权衡: wakeup("在呢!")降级为 ""——cheerful
    语速系数 1.02 会改变 TTS 缓存键, 破坏 preheatTTS 预取
    与服务端预合成的秒播命中(唤醒应答是全站最需秒播的一句,
    秒播 > 语调微调)。
    """
    if intent == "asr_failed" or user_mood == "negative":
        return "care"
    # 酒的问话·侍酒师语调: 信任/知识问话走稳重档(×0.95)
    # ——品酒师叙事沉稳有底蕴(泛化真伪/工艺故事两问)
    if intent in ("wine.verify", "wine.craft"):
        return "steady"
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


# ------------------------------------------------------------
# P1.5·竹语: TTS 首声延迟优化(resp→play 1.1~1.5s 压至亚秒)
# ------------------------------------------------------------

def tts_preheat_enabled() -> bool:
    """服务端响应内预合成开关(默认 on; off 一键关闭零影响)"""
    return os.environ.get(
        "XIAOZHU_TTS_PREHEAT", "on"
    ).strip().lower() not in ("off", "0", "false")


def tts_stream_enabled() -> bool:
    """P1·流式 TTS 灰度开关(默认 off——零影响上线; 生产
    compose 注入 XIAOZHU_TTS_STREAM=on 启用)

    /voices 响应携带本状态 → 前端据此走流式或整句双轨;
    /tts/stream 端点 on 时可用, off 时 403(回退链兜底)。
    """
    return os.environ.get(
        "XIAOZHU_TTS_STREAM", "off"
    ).strip().lower() in ("on", "1", "true")


def lat_report_enabled() -> bool:
    """P2·H2 观察期 [LAT] 上报开关(默认 off; 生产观察期
    注入 XIAOZHU_LAT_REPORT=on)

    客户端四段延迟(vad→submit/submit→resp/resp→play/total)
    服务端不可见(执行仅 18ms)——观察期靠前端 latReport 轻量
    上报落 Redis 日键(TTL 9 天覆盖观察周), 看板聚合 P50/P90。
    观察期结束(09-30 G1 完整版出)后关。
    """
    return os.environ.get(
        "XIAOZHU_LAT_REPORT", "off"
    ).strip().lower() in ("on", "1", "true")


def tts_cache_key(text: str, voice: str, speed: float) -> str:
    """TTS Redis 缓存键(text|voice|speed 三维)

    /tts 路由与 78号P1.5 服务端预合成共用本函数——键构造
    必须逐字节一致, 预合成写入的缓存前端 /tts 才能命中
    (等值重构自 routes /tts 内联键, 行为零变更)。
    """
    import hashlib
    t = str(text or "").strip()[:200]
    return ("xiaozhu:tts:mp3:" + hashlib.sha256(
        (t + "|" + str(voice or "") + "|"
         + f"{float(speed):g}").encode("utf-8")
    ).hexdigest()[:24])


# 播报情绪语调系数(与前端 moodSpeed 同值锚定——跨端常量
# 无法共享, 两处注释互指; P2·O2: 预合成按 mood 系数入键,
# care/steady 轮(识别失败/用户负面/高敏确认)亦享受秒播)
MOOD_SPEED = {"care": 0.92, "steady": 0.95, "cheerful": 1.02}

# 分句切分字符集(与前端 splitSpeech 逐字一致——P1.5 服务端
# 预合成首子句必须与前端请求的块文本逐字节相同, 否则键不命中)
_SPLIT_CHARS = "。！？；;，,—"
_SPLIT_MIN_FIRST = 4   # 首块最小字数(实证"好的——"4字稳)
_SPLIT_HARD = 14       # 无标点硬切(spike: 27字合成1.95s,
                       # 14字≈1.1s——首块调小收益近线性)


def split_speech(text: str) -> list[str]:
    """v3 分句流水线的服务端镜像(前端 splitSpeech 逐字一致)

    短回复(≤24字)整句; 长回复按标点切分, 首块最小 4 字
    ("好的——"恒定前缀——推荐轮 reply 统一带此前缀, preheat
    预取一次全网零合成秒播); 无标点硬切 14 字。
    破折号"—"入切分集为 P1.5 增量(spike 实证 4 字带破折号
    合成稳定)。
    """
    t = str(text or "").strip()
    if not t:
        return []
    if len(t) <= 24:
        return [t]
    parts: list[str] = []
    cur = ""
    for ch in t:
        cur += ch
        if ch in _SPLIT_CHARS:
            if len(cur.strip()) >= _SPLIT_MIN_FIRST:
                parts.append(cur.strip())
                cur = ""
        elif len(cur) >= _SPLIT_HARD:
            # 硬切回退: 切点回退越过尾部 ASCII 数字/字母段
            # 及紧贴符号(°×¥)——"52度"不在"5|2"处断裂数字读音;
            # 空格是自然词边界停在空格处切
            cut = cur
            while len(cut) > _SPLIT_MIN_FIRST and (
                    (cut[-1].isascii()
                     and cut[-1] not in _SPLIT_CHARS
                     and cut[-1] != " ")
                    or cut[-1] in "°×¥"):
                cut = cut[:-1]
            if len(cut.strip()) >= _SPLIT_MIN_FIRST:
                parts.append(cut)
                cur = cur[len(cut):]
            else:
                parts.append(cur)
                cur = ""
    if cur.strip():
        parts.append(cur.strip())
    if not parts:
        return [t]
    # v77 短块合并(与前端 splitSpeech 逐字一致): 切分产出的
    # ≤6 字短块百炼 TTS 高频拒答(98 字节 JSON 错误体)且块数
    # 多加剧并发限流——非首块并入前块; 首块保护("好的——"
    # preheat 秒播链依赖恒定键)
    merged = [parts[0]]
    for p in parts[1:]:
        if len(p.strip()) <= 6:
            merged[-1] += p
        else:
            merged.append(p)
    return merged
