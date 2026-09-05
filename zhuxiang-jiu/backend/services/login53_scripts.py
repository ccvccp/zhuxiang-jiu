"""53号·小竹智能登录引擎 话术注册表(login53_scripts)

计划(docs/53号_小竹智能登录引擎实施计划.md §四):
    三组 17 场景——正向 7+异常 7+退出 3, 每场景
    自含 tts 文本+语气+TTS 参数组键。

设计原则(原方案 §六-1):
    - 价值前置: 话术携带个性化信值信息占位符
    - 情绪适配: 四态 TTS 参数(轻快/温和/严肃/安抚)
    - 归因可见: 拦截必附通俗原因
    - 包容表达: 禁用技术术语黑名单(启动自检)
    - 隐私显式告知: 生物采集主动说明用途

异常话术铁律(原方案 §三-3):
    绝不使用责备性/模糊性语言("验证失败/系统错误")——
    归因外部化+提供出路+情感支持("这不是您的错")。

占位符: {nickname} {score} {delta} {days} {missed}
        {amount} {taskCount} {reward}(运行时渲染, 缺省降级)。
"""

import logging

logger = logging.getLogger("login53_scripts")

# ------------------------------------------------------------
# TTS 四态参数(原方案 §五-1 语音工程实施规范)
# ------------------------------------------------------------

TTS_PROFILES = {
    "success": {"pitch": "+2st", "rate": "1.1x",
                "emotion": "happy",
                "note": "成功——轻快鼓励"},
    "security_warn": {"pitch": "-1st", "rate": "0.9x",
                      "emotion": "serious",
                      "note": "安全提示——严肃但温和"},
    "elderly_mode": {"pitch": "+1st", "rate": "0.8x",
                     "emotion": "warm", "volumeBoost": True,
                     "note": "适老化——缓慢洪亮重复关键信息"},
    "error_recovery": {"pitch": "0", "rate": "1.0x",
                       "emotion": "gentle",
                       "note": "异常安抚——温和去污名化"},
}

# ------------------------------------------------------------
# 正向组(7): 价值前置+情绪适配
# ------------------------------------------------------------

POSITIVE_SCRIPTS = {
    "wake_login": {
        "label": "唤醒即认证",
        "trigger": "唤醒词+声纹置信度达标",
        "text": "欢迎回来，{nickname}！您昨日社区服务"
                "获得3条点赞，信值+5分。今天想先看看"
                "修复任务，还是随便逛逛？",
        "ttsProfile": "success",
        "valueHook": True,
    },
    "passkey_silent": {
        "label": "Passkey 静默登录",
        "trigger": "设备指纹匹配+风险<25",
        "text": "早上好！当前信值{score}，较上周"
                "{delta}。需要我带您查看最新报告吗？",
        "ttsProfile": "success",
        "valueHook": True,
        "zeroInterrupt": True,   # 登录瞬间不打断(0.5s 提示音后)
    },
    "face_success": {
        "label": "刷脸登录成功",
        "trigger": "活体检测通过(mock 通道)",
        "text": "面容确认成功！L1合规层积分已更新。"
                "正在为您同步档案...",
        "ttsProfile": "success",
        "note": "mock 通道——国标 SDK 外部待办",
    },
    "voice_confirm": {
        "label": "声纹二次确认",
        "trigger": "声纹置信度 70%-95%",
        "text": "声音有点像{nickname}，为了安全，"
                "请再说一次'我回来了'让我确认一下好吗？",
        "ttsProfile": "security_warn",
        "note": "请求式语气降低压迫感",
    },
    "qr_cross_device": {
        "label": "扫码跨端续接",
        "trigger": "手机端授权成功",
        "text": "手机已确认！正在将您的信值档案"
                "安全同步到电脑端，请稍候片刻~",
        "ttsProfile": "success",
    },
    "elderly_login": {
        "label": "老年模式登录",
        "trigger": "年龄≥60 或手动开启",
        "text": "您好！指纹验证通过了。今天有{taskCount}"
                "项志愿服务待确认，需要我现在慢慢"
                "念给您听吗？",
        "ttsProfile": "elderly_mode",
    },
    "visual_impaired": {
        "label": "视障用户登录",
        "trigger": "屏幕阅读器激活",
        "text": "登录成功。当前信值等级{level}，"
                "可用余额{amount}分。双击屏幕右侧"
                "可进入个人中心，长按可听取今日摘要。",
        "ttsProfile": "success",
        "note": "结构化+含操作指引(无障碍导航)",
    },
}

# ------------------------------------------------------------
# 异常组(7): 归因外部化+去污名化铁律
# ------------------------------------------------------------

ERROR_SCRIPTS = {
    "voice_failed": {
        "label": "声纹识别失败",
        "trigger": "连续 2 次置信度<70%",
        "text": "环境有点嘈杂，没能听清您的声音。"
                "我们换种方式试试？您可以点击屏幕"
                "下方的指纹按钮，或者我说个验证码"
                "您复述一遍。",
        "ttsProfile": "error_recovery",
        "fallbackAction": "switch_alternative",
        "note": "归因外部化(环境)+提供多选项",
    },
    "liveness_failed": {
        "label": "活体检测失败",
        "trigger": "TTS/深伪攻击疑似",
        "text": "为了保护您的账户安全，刚才的"
                "面容验证未能通过。请确保本人面对"
                "镜头，光线充足。需要人工协助吗？",
        "ttsProfile": "error_recovery",
        "fallbackAction": "human_support",
        "note": "强调保护意图+人工兜底(防深伪安全挑战)",
    },
    "new_device_login": {
        "label": "异地/新设备登录",
        "trigger": "地理位置/设备指纹突变",
        "text": "检测到您在新地点登录，为保障信值安全，"
                "需要额外确认身份。这是常规保护措施，"
                "请放心。",
        "ttsProfile": "security_warn",
        "fallbackAction": "step_up_sms",
        "note": "解释风控逻辑+消除恐慌",
    },
    "budget_exhausted": {
        "label": "隐私预算耗尽",
        "trigger": "当日预算≤0(49号)",
        "text": "您今日的隐私预算已用完，深度行为分析"
                "暂时暂停。基础登录仍可正常使用，"
                "也可在设置中调整预算或等待明日重置。",
        "ttsProfile": "security_warn",
        "fallbackAction": "basic_mode",
        "note": "透明告知+用户控制权",
    },
    "account_protected": {
        "label": "账号临时保护",
        "trigger": "UEBA 判定高风险",
        "text": "您的账户因近期异常活动被临时保护。"
                "这不是您的错，可能是他人误操作。"
                "请联系客服解冻，我会全程陪伴您"
                "完成验证。",
        "ttsProfile": "error_recovery",
        "fallbackAction": "human_support",
        "note": "去污名化铁律直译",
    },
    "network_timeout": {
        "label": "网络超时",
        "trigger": "API 响应>5s",
        "text": "网络似乎有点慢，正在重试...如果持续"
                "无法连接，您可以稍后再试，或切换至"
                "离线模式查看本地缓存的信值摘要。",
        "ttsProfile": "error_recovery",
        "fallbackAction": "offline_mode",
        "note": "管理预期+保障基本可用",
    },
    "child_access": {
        "label": "儿童用户访问",
        "trigger": "年龄<14 且无监护人授权",
        "text": "小朋友你好！使用这个功能需要爸爸妈妈"
                "同意哦。请把设备交给大人，让他们帮我"
                "确认一下好吗？",
        "ttsProfile": "elderly_mode",
        "fallbackAction": "guardian_auth",
        "note": "未成年人保护+责任转移",
    },
}

# ------------------------------------------------------------
# 退出组(3): 非强制挽留+功能教育
# ------------------------------------------------------------

EXIT_SCRIPTS = {
    "proactive_exit": {
        "label": "主动退出",
        "trigger": "用户点击退出/关闭",
        "text": "要离开啦？下次来可以试试对我说"
                "'查信值'，更快哦~ 祝您今天愉快！",
        "ttsProfile": "success",
        "note": "非弹窗拦截——尊重选择",
    },
    "idle_30s": {
        "label": "长时间未操作",
        "trigger": "登录页停留>30s",
        "text": "看起来您在犹豫？需要我介绍一下如何"
                "快速登录，或者讲讲信值能为您做什么吗？",
        "ttsProfile": "security_warn",
        "note": "主动援助+价值再触达",
    },
    "streak_achieved": {
        "label": "连续登录成就",
        "trigger": "连续 N 天达成",
        "text": "太棒了！您已连续登录{days}天，解锁了"
                "'小竹'专属星空语音包！现在就想"
                "听听看吗？",
        "ttsProfile": "success",
        "note": "游戏化激励+即时奖励兑现",
    },
}

# 全量注册表(17 场景)
ALL_SCRIPTS = {}
ALL_SCRIPTS.update(POSITIVE_SCRIPTS)
ALL_SCRIPTS.update(ERROR_SCRIPTS)
ALL_SCRIPTS.update(EXIT_SCRIPTS)

# 话术分组视图(注册表自描述)
SCRIPT_GROUPS = {
    "positive": POSITIVE_SCRIPTS,
    "error": ERROR_SCRIPTS,
    "exit": EXIT_SCRIPTS,
}

# ------------------------------------------------------------
# 责备性/模糊性话术黑名单(启动自检——合规铁律)
# ------------------------------------------------------------

FORBIDDEN_PHRASES = (
    "验证失败", "系统错误", "非法请求", "你的错误",
    "你失败了", "违规操作", "已被监控", "警告你",
)

# ------------------------------------------------------------
# 占位符缺省值(渲染降级——钩子数据缺失不报错)
# ------------------------------------------------------------

PLACEHOLDER_DEFAULTS = {
    "nickname": "用户", "score": "—", "delta": "持平",
    "days": "7", "missed": "若干", "amount": "0",
    "taskCount": "1", "level": "L1", "reward": "小惊喜",
}


def render_script(script_key: str,
                  params: dict | None = None) -> dict:
    """渲染一条话术(占位符替换——缺省降级不报错)

    Returns:
        {key, label, text, ttsProfile, tts, ...元数据}
    Raises:
        KeyError: 未注册话术
    """
    script = ALL_SCRIPTS.get(script_key)
    if script is None:
        raise KeyError(f"话术 {script_key} 未注册")
    params = params or {}
    text = script["text"]
    for key, default in PLACEHOLDER_DEFAULTS.items():
        text = text.replace(
            "{%s}" % key, str(params.get(key, default)))
    return {
        "key": script_key,
        "label": script["label"],
        "text": text,
        "ttsProfile": script["ttsProfile"],
        "tts": TTS_PROFILES[script["ttsProfile"]],
        "trigger": script.get("trigger", ""),
        "fallbackAction": script.get(
            "fallbackAction", ""),
        "valueHook": bool(script.get("valueHook")),
        "note": script.get("note", ""),
    }


def validate_scripts() -> None:
    """启动自检: 话术注册表完整性+合规铁律

    Raises:
        RuntimeError: 任一断言失败(宪法级)
    """
    # ① 三组 17 场景
    total = (len(POSITIVE_SCRIPTS)
             + len(ERROR_SCRIPTS) + len(EXIT_SCRIPTS))
    if total != 17 or len(ALL_SCRIPTS) != 17:
        raise RuntimeError(
            f"login53 话术注册表不一致: {total} != 17")
    # ② TTS 参数组引用合法
    for key, script in ALL_SCRIPTS.items():
        profile = script.get("ttsProfile")
        if profile not in TTS_PROFILES:
            raise RuntimeError(
                f"login53 话术 {key} TTS 参数组 "
                f"{profile} 未注册")
    # ③ 责备性话术黑名单扫描(合规铁律)
    for key, script in ALL_SCRIPTS.items():
        for phrase in FORBIDDEN_PHRASES:
            if phrase in script["text"]:
                raise RuntimeError(
                    f"login53 话术 {key} 含责备性"
                    f"用语 '{phrase}'(去污名化铁律)")
    # ④ 异常组必有 fallbackAction(出路提供铁律)
    for key, script in ERROR_SCRIPTS.items():
        if not script.get("fallbackAction"):
            raise RuntimeError(
                f"login53 异常话术 {key} 缺少 "
                f"fallbackAction(出路铁律)")
    # ⑤ 异常组必须使用安抚/严肃参数(不用 success)
    for key, script in ERROR_SCRIPTS.items():
        if script["ttsProfile"] == "success":
            raise RuntimeError(
                f"login53 异常话术 {key} 禁用 success "
                f"参数组(情绪适配铁律)")
    logger.info("login53_scripts_validated total=17 "
                "groups=3 ttsProfiles=4")


# 模块导入即自检(宪法级——注册表封闭)
validate_scripts()
