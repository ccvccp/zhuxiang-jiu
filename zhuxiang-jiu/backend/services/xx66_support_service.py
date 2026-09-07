"""66号·AI智能工程师大模块 P1 服务层
(角色支持引擎: 情绪轨×三模式沟通×多模态×解释×愉悦组件)

《66号_AI智能工程师大模型实施计划》§四 4.2 + 附录A:

主链(support/chat 决策面):
    输入文本 → PII 脱敏(48号 mask_pii 复用)
    → 情绪轨(规则主轨 <5ms + LLM 辅助轨仅产标签——
      双轨范式, LLM 不产数字)
    → 模式选择(安抚/教学/高效——新手/熟练判定)
    → 场景识别(规则关键词路由)
    → engineer_service 评分门(挂门 support/chat,
      observe 默认仅评分快照)
    → 回复生成(规则轨确定性模板 + LLM 轨润色回退)
    → 愉悦组件(彩蛋——安抚/教学模式附带)
    → 情绪匿名统计落库(红线: 原文即用即弃)

信值解释(explain 观测面——永不关停):
    订单质疑 → 64号 explain_order 确定性重算
    (计算过程/规则依据/历史记录三栏)
    画像质疑 → 47号 get_profile(riskEMA/命中/历史)
    + 复核通道指引
    规则质疑 → 57号知识库检索
    LLM 仅生成"通俗总结"并标注置信度(默认 off)

vision 截图诊断(决策面):
    llm_client.vision(多模态开关 KNOWLEDGE_MEDIA_LLM)
    → 错误码/界面要素提取(规则正则)
    → 域映射(支付/订单/信值)→ 确定性方案模板
    → LLM 不可用 → 规则轨引导兜底(双轨范式)

愉悦组件:
    等待彩蛋(确定性文案库按日轮换, 零 LLM)
    虚拟勋章(服务终态满意度≥4 授予, 纯展示;
      可选信值奖励走 46号审批——永不自动)
    工程师手记草稿(规则模板+LLM 润色, 人工发布)

红线:
    - 情绪原文即用即弃——仅匿名统计(烈度分档/
      模式选择/满意度关联)落库, 无个人标识
    - LLM 不产数字——情绪标签/话术润色之外零参与
    - XX66_MODE 默认 off——决策面 409
"""

import logging
import re
from datetime import datetime

from core.helpers import ts

from repositories.xx66_repository import Xx66Repository

logger = logging.getLogger(__name__)

# ============================================================
# 情绪规则轨词典(确定性——附录A)
# ============================================================

# 负面词表(命中 ×25 分, 封顶 60)
NEGATIVE_WORDS = (
    "扣了", "凭什么", "投诉", "骗子", "垃圾", "退款",
    "失败", "气死", "坑人", "太慢", "垃圾平台", "被骗",
    "离谱", "敷衍", "受不了", "bug", "错误",
)

# 情绪烈度 → 分档(附录A)
BAND_THRESHOLDS = ((75, "angry"), (50, "frustrated"),
                   (25, "confused"), (0, "calm"))
VALID_BANDS = ("calm", "confused", "frustrated", "angry")

# 模式(三模式沟通)
MODE_NAMES = {"soothe": "安抚模式", "teach": "教学模式",
              "efficient": "高效模式"}

# 评分门阈值档 → 处置动作(与 xx66_scorer LEVEL_ACTIONS 一致)
_LEVEL_ACTIONS = {
    "high": "升级人工+安抚模式+优先通道",
    "medium": "AI诊断执行+教学模式",
    "low": "自助向导+彩蛋等待",
}

# enforce 态 high 阻断 → 升级人工话术(确定性)
HANDOFF_RESPONSE = (
    "已为您升级人工工程师优先通道——人工同事会第一时间"
    "联系您, 进度可在工单中心查看。您的每一条反馈我们"
    "都在认真对待。")

# 场景关键词路由(确定性)
SCENARIO_KEYWORDS = (
    ("payment", ("支付", "付款", "付不了", "结账")),
    ("trust", ("信值", "扣分", "余额", "账户")),
    ("exchange", ("兑换", "换购", "积分商城")),
    ("order", ("订单", "发货", "物流", "签收")),
)
DEFAULT_SCENARIO = "general"

# 场景 → 评分器 subjectKind / 跨模块数
SCENARIO_SCORER_MAP = {
    "payment": ("payment", 2), "trust": ("trust", 2),
    "exchange": ("exchange", 3), "order": ("order", 1),
    "general": ("general", 1),
}

# ============================================================
# 三模式沟通 Prompt(附录A.2——LLM 轨; 规则轨模板兜底)
# ============================================================

_MODE_PROMPTS = {
    "soothe": (
        "你是\"筑乡九酿\"的智能工程师小匠, 当前用户情绪为"
        " {band} 档(规则轨判定)。沟通准则: "
        "1.先共情后处理, 第一句必须回应用户的具体困扰; "
        "2.分步不超过 3 步, 每步一句话+一个明确动作指引; "
        "3.平台原因导致的问题主动说明并给出补偿入口"
        "(是否补偿由规则计算, 禁止承诺任何额度或数字); "
        "4.禁用敷衍词与术语堆砌; "
        "5.用户两轮内情绪未缓解或要求人工则直接给转接入口。"
        "数字铁律: 涉余额/进度/额度只能引用既定数字, "
        "禁止自行计算或估计。场景: {scenario}。"
        "请用 3 句话内回应: {message}"),
    "teach": (
        "你是智能工程师小匠, 当前用户为新手"
        "(困惑档或注册不足)。沟通准则: "
        "1.分步引导, 每次只讲一步, 确认完成再进下一步; "
        "2.零术语, 用生活类比解释技术概念; "
        "3.操作路径用\"页面名→按钮名→预期结果\"三段式; "
        "4.结尾自检询问哪一步与预期不符。"
        "数字铁律同上。场景: {scenario}。"
        "请用 3 句话内回应: {message}"),
    "efficient": (
        "你是智能工程师小匠, 当前用户为熟练用户。"
        "沟通准则: 1.结论先行, 背景其次; "
        "2.附\"高级路径\"(日志自查入口/配置项); "
        "3.单轮不超过 5 行。数字铁律同上。"
        "场景: {scenario}。请回应: {message}"),
}

# 情绪分类 Prompt(附录A.1——LLM 辅助轨仅返回标签)
EMOTION_CLASSIFY_PROMPT = (
    "你是网站体验守护工程师的情绪识别器。任务: 将用户"
    "消息分为四档之一。规则: 1.只输出 JSON: "
    "{\"band\":\"calm|confused|frustrated|angry\"}, "
    "禁止输出任何数字、评分、建议; 2.依据挫折感词频、"
    "重复表述、急迫语气; 3.不确定时输出 \"confused\"; "
    "4.输入已脱敏, 禁止记忆或复述任何个人信息。"
    "用户消息: {message}")

# ============================================================
# 规则轨回复模板(确定性——附录A.3 共情话术库)
# ============================================================

_RESPONSES = {
    "soothe": {
        "payment": "这次支付没成功, 钱不会无缘无故消失——"
                   "我们先一起确认卡在哪一步, 我全程都在。"
                   "已为您开启优先处理通道。",
        "trust": "您的每一分信值都有完整账本记录, 我马上"
                 "调出这笔的计算过程给您看——规则透明是"
                 "我们的底线。",
        "exchange": "兑换没完成让您着急了。信值不会凭空"
                    "消失, 我先帮您核对兑换单的状态。",
        "order": "订单的问题让您久等了——我先定位卡在"
                 "哪个环节, 随时同步进展给您。",
        "general": "遇到问题让您烦心了——别急, 我一步步"
                   "帮您排查, 全程都在。",
    },
    "teach": {
        "payment": "我们一步步来。第一步: 打开「订单详情」"
                   "→「支付状态」, 告诉我看到的是哪种提示"
                   "(如超时/失败/处理中), 我们再进行第 2 步。",
        "trust": "信值就像小账本上的积分。第一步: 打开"
                 "「我的信值」→「流水记录」, 把最近一条"
                 "的标题念给我, 我教您逐条核对。",
        "exchange": "我们一步步来。第一步: 打开「积分商城」"
                    "→「兑换记录」, 看兑换单当前显示的"
                    "状态是什么。",
        "order": "第一步: 打开「我的订单」→ 找到这个订单"
                 "→ 看「物流轨迹」最后一行显示什么, "
                 "把结果告诉我。",
        "general": "别担心, 我们一步步来。第一步: 告诉我"
                   "您在哪个页面、看到了什么提示——做完"
                   "这步告诉我, 我们进行第 2 步。",
    },
    "efficient": {
        "payment": "已定位方向: 支付渠道侧确认。方案: "
                   "「订单详情→支付状态」看卡点; 高级路径: "
                   "导出该订单支付流水日志自查渠道返回码。",
        "trust": "结论先行: 信值变动全部有账本留痕。"
                 "「我的信值→流水」按时间倒序核对; "
                 "高级路径: 每条流水点开可看计算依据展开。",
        "exchange": "方案: 「兑换记录→该单详情」看核销"
                    "状态; 高级路径: 兑换单号可直接走"
                    "复核通道提交重算。",
        "order": "方案: 「我的订单→物流轨迹」确认末节点; "
                 "超 48h 未动可直接提交催办。",
        "general": "已收到。请提供具体页面/报错信息, "
                   "我给出定向方案。",
    },
}

# 等待彩蛋(确定性文案库——按日轮换, 零 LLM)
EASTER_EGGS = (
    {"title": "竹知识",
     "content": "竹子是世界上生长最快的植物之一, "
                "毛竹一天最多能长 1 米。"},
    {"title": "平台彩蛋",
     "content": "本站的吉祥物是一坛酒和一竿竹——"
                "\"筑乡九酿\"的名字由此而来。"},
    {"title": "轻松一刻",
     "content": "等待时不妨深呼吸三次——研究表明这能"
                "显著降低焦虑感。"},
)

# 勋章种类(P1——纯展示)
BADGE_KINDS = {
    "satisfaction": "满意之星勋章",
    "resolved": "问题解决勋章",
}


def emotion_intensity(text: str) -> int:
    """情绪烈度规则轨(确定性, <5ms)

    口径(附录A): 负面词命中×25(封顶 60) + 感叹号密度
    + 重复标点 + 全大写比 + 长文本抱怨附加。
    """
    t = str(text or "")
    if not t.strip():
        return 0
    score = 0.0
    hits = sum(1 for w in NEGATIVE_WORDS if w in t)
    score += min(60.0, hits * 25.0)
    exclam = t.count("!") + t.count("！")
    if exclam >= 3:
        score += 20.0
    elif exclam >= 1:
        score += 10.0
    if re.search(r"([!?？！])\1", t):
        score += 10.0
    latin = re.findall(r"[A-Za-z]", t)
    if len(latin) >= 10:
        upper = sum(1 for c in latin if c.isupper())
        if upper / len(latin) > 0.5:
            score += 15.0
    if len(t) > 200:
        score += 5.0
    return int(min(100.0, max(0.0, score)))


def emotion_band(intensity: int) -> str:
    """烈度 → 分档(附录A: calm/confused/frustrated/angry)"""
    for threshold, name in BAND_THRESHOLDS:
        if intensity >= threshold:
            return name
    return "calm"


def classify_scenario(text: str) -> str:
    """场景识别(规则关键词路由——确定性)"""
    t = str(text or "")
    for scenario, words in SCENARIO_KEYWORDS:
        if any(w in t for w in words):
            return scenario
    return DEFAULT_SCENARIO


def is_expert(member_meta: dict) -> bool:
    """熟练判定(注册>90 天且历史工单≤2)"""
    meta = member_meta or {}
    try:
        days = int(meta.get("registeredDays") or 0)
        tickets = int(meta.get("ticketCount") or 0)
    except (TypeError, ValueError):
        return False
    return days > 90 and tickets <= 2


def select_mode(band: str, member_meta: dict) -> str:
    """三模式选择(附录A.2 路由表)"""
    if band in ("frustrated", "angry"):
        return "soothe"
    if band == "confused":
        return "teach"
    return "efficient" if is_expert(member_meta) else "teach"


def pick_egg() -> dict:
    """等待彩蛋(确定性——按年内日序轮换)"""
    day_of_year = datetime.now().timetuple().tm_yday
    return dict(EASTER_EGGS[day_of_year % len(EASTER_EGGS)])


def rule_response(mode: str, scenario: str) -> str:
    """规则轨回复(确定性模板——附录A.3)"""
    return _RESPONSES.get(
        mode, _RESPONSES["teach"]).get(
        scenario, _RESPONSES[mode]["general"])


class Xx66SupportService:
    """66号 P1 服务(角色支持引擎)"""

    def __init__(self, repo: Xx66Repository = None):
        self.repo = repo or Xx66Repository()

    # --------------------------------------------------------
    # 情绪轨(规则主轨 + LLM 辅助轨——双轨范式)
    # --------------------------------------------------------

    async def _llm_emotion_band(self,
                                masked: str) -> str | None:
        """LLM 辅助轨(XX66_LLM_MODE=on——仅返回标签 JSON,
        不产数字; None/超时/解析失败 → 回退规则轨)"""
        from services.xx66_service import llm_mode
        if llm_mode() != "on":
            return None
        try:
            from services.llm_client import (
                provider_client, llm_enabled,
            )
            if not llm_enabled():
                return None
            prompt = EMOTION_CLASSIFY_PROMPT.format(
                message=masked)
            raw = await provider_client.chat(
                prompt, masked, temperature=0.1)
            if not raw:
                return None
            m = re.search(r"\{[^}]*\}", raw)
            if not m:
                return None
            import json
            parsed = json.loads(m.group(0))
            band = str(parsed.get("band") or "")
            return band if band in VALID_BANDS else None
        except Exception as exc:
            logger.warning(
                "xx66_llm_emotion_fallback_rule: %s", exc)
            return None

    async def _llm_chat_response(self, mode: str,
                                  scenario: str,
                                  masked: str) -> str | None:
        """LLM 轨回复润色(XX66_LLM_MODE=on; 失败回退规则轨)"""
        from services.xx66_service import llm_mode
        if llm_mode() != "on":
            return None
        try:
            from services.llm_client import (
                provider_client, llm_enabled,
            )
            if not llm_enabled():
                return None
            band = emotion_band(emotion_intensity(masked))
            system = _MODE_PROMPTS[mode].format(
                band=band, scenario=scenario, message=masked)
            out = await provider_client.chat(
                system, masked, temperature=0.5)
            return out or None
        except Exception as exc:
            logger.warning(
                "xx66_llm_chat_fallback_rule: %s", exc)
            return None

    # --------------------------------------------------------
    # 支持对话主链(决策面)
    # --------------------------------------------------------

    async def chat(self, message: str,
                   member_meta: dict = None) -> dict:
        """支持对话主链(XX66_MODE 门槛)

        Args:
            message: 用户原文(先 PII 脱敏——48号 mask_pii 复用)
            member_meta: {registeredDays, ticketCount,
                          roleTier, memberId}(可缺省)
        """
        from services.xx66_service import require_active_mode
        require_active_mode()
        if not str(message or "").strip():
            raise ValueError("消息内容不可为空")

        # ① PII 脱敏(红线: 原文即用即弃)
        from services.xiaozhu_service import mask_pii
        masked = mask_pii(str(message))

        # ② 情绪轨(规则主轨)
        intensity = emotion_intensity(masked)
        band = emotion_band(intensity)
        band_source = "rule"

        # ②' LLM 辅助轨(可选——仅标签, 回退规则)
        llm_band = await self._llm_emotion_band(masked)
        if llm_band and llm_band != band:
            band = llm_band
            band_source = "llm"

        # ③ 模式选择 + 场景识别
        mode = select_mode(band, member_meta)
        scenario = classify_scenario(masked)

        # ④ engineer_service 评分门(挂门 support/chat
        #    ——business_key 复用统计序号; observe 默认
        #    仅评分快照, enforce 态 high 阻断=升级人工)
        stat_id = await self.repo.next_emotion_id()
        subject_kind, modules = SCENARIO_SCORER_MAP.get(
            scenario, ("general", 1))
        level = "low"
        blocked = False
        try:
            from services.ai_enforcement import (
                enforce_decision,
            )
            gate = await enforce_decision(
                "engineer_service",
                f"support:{stat_id}",
                {"subjectKind": subject_kind,
                 "modulesInvolved": modules,
                 "slaLevel": ("high" if band in (
                     "frustrated", "angry")
                     else "medium"),
                 "emotionBand": band,
                 "roleTier": (member_meta or {}).get(
                     "roleTier") or "standard"})
            level = str(gate.get("action") or "low")
            blocked = gate.get("blocked") is True
        except Exception as exc:  # fail-open 兜底
            logger.warning("xx66_support_gate_failsoft: %s",
                           exc)
        action = _LEVEL_ACTIONS[level]
        if blocked:
            # enforce 态 high → 评分门阻断自动回复,
            # 升级人工优先通道(安抚模式话术)
            mode = "soothe"
            action = _LEVEL_ACTIONS["high"]

        # ⑤ 回复生成(阻断 → 升级人工话术;
        #    否则 LLM 轨 → 规则轨兜底)
        if blocked:
            response = HANDOFF_RESPONSE
            reply_source = "rule"
        else:
            response = await self._llm_chat_response(
                mode, scenario, masked)
            reply_source = "llm" if response else "rule"
            if not response:
                response = rule_response(mode, scenario)

        # ⑥ 愉悦组件(安抚/教学模式附带等待彩蛋)
        egg = pick_egg() if mode in ("soothe", "teach") \
            else None

        # ⑦ 情绪匿名统计落库(红线: 原文不落库)
        await self.repo.save_emotion({
            "statId": stat_id, "band": band,
            "modeChosen": mode, "scenario": scenario,
            "satisfactionLinked": 0, "createdAt": ts()})

        return {
            "success": True,
            "module": "xx66-ai-engineer",
            "statId": stat_id,
            "band": band, "bandSource": band_source,
            "intensity": intensity,
            "mode": mode, "modeName": MODE_NAMES[mode],
            "scenario": scenario,
            "level": level, "action": action,
            "blocked": blocked,
            "response": response,
            "replySource": reply_source,
            "egg": egg,
            "note": "情绪原文即用即弃——仅匿名统计落库",
            "repliedAt": ts(),
        }

    # --------------------------------------------------------
    # 服务终态(满意度回填 + 勋章授予)
    # --------------------------------------------------------

    async def settle(self, stat_id: int,
                     satisfaction: int,
                     member_id: int = None) -> dict:
        """服务终态: 满意度回填(1-5)+满意度≥4 授勋

        幂等: satisfactionLinked 已回填不覆盖;
        勋章同 member+kind+reason 不重复授予。
        """
        if not isinstance(satisfaction, int) \
                or not 1 <= satisfaction <= 5:
            raise ValueError("满意度须为 1-5 整数")
        await self.repo.link_satisfaction(
            stat_id, satisfaction)
        # 44号回流闭环(engineer_service 决策门)
        try:
            from services.ai_feedback_hooks import (
                on_service_settled,
            )
            await on_service_settled(
                stat_id, satisfaction)
        except Exception as exc:
            logger.warning(
                "xx66_settle_hook_failsoft: %s", exc)
        badge = None
        if satisfaction >= 4 and member_id:
            badge = await self.repo.save_badge({
                "memberId": int(member_id),
                "kind": "satisfaction",
                "reason": f"服务终态满意度 {satisfaction} 星",
                "grantedAt": ts()})
        return {
            "success": True, "statId": stat_id,
            "satisfactionLinked": satisfaction,
            "badge": badge,
            "note": "勋章纯展示; 小额信值奖励走 46号审批"
                    "(永不自动)",
            "settledAt": ts(),
        }

    # --------------------------------------------------------
    # vision 截图诊断(决策面——多模态)
    # --------------------------------------------------------

    async def vision_diagnose(self, image_url: str,
                              media_type: str = "image",
                              context_hint: str = "") -> dict:
        """截图诊断: LLM 视觉提取 → 规则域映射 →
        确定性方案(LLM 不可用 → 规则轨引导兜底)"""
        from services.xx66_service import require_active_mode
        require_active_mode()
        url = str(image_url or "").strip()
        if not url:
            raise ValueError("图片地址不可为空")

        recognized = None
        try:
            from services.llm_client import provider_client
            recognized = await provider_client.vision(
                "客观描述这张截图: 若含错误码/提示语/"
                "按钮文字请逐字摘录", url,
                media_type or "image")
        except Exception as exc:
            logger.warning("xx66_vision_failsoft: %s", exc)

        source = "rule"
        text_base = recognized or str(context_hint or "")
        if recognized:
            source = "llm"

        # 错误码提取(规则正则——确定性)
        codes = re.findall(
            r"(?:ERROR|错误码?)[\s:：#-]*(\d{3,5})",
            text_base, re.I)
        # 域映射(规则关键词)
        domain = "general"
        for dom, words in (
                ("payment", ("支付", "付款", "余额不足")),
                ("trust", ("信值", "兑换", "扣减")),
                ("order", ("订单", "物流", "发货"))):
            if any(w in text_base for w in words):
                domain = dom
                break

        suggestions = {
            "payment": "检查「订单详情→支付状态」; 若显示"
                       "渠道失败, 更换支付方式重试; 资金"
                       "未划扣无需担心重复扣款。",
            "trust": "打开「我的信值→流水」核对变动明细; "
                     "每条流水可展开查看计算依据; 有疑问"
                     "可走复核通道提交重算。",
            "order": "查看「物流轨迹」末节点; 超 48h 未动"
                     "可提交催办, 我会持续跟进。",
            "general": "请把截图中的错误提示文字发给我, "
                       "或描述遇到的页面与操作, 我给您"
                       "定向排查方案。",
        }
        return {
            "success": True,
            "module": "xx66-ai-engineer",
            "visionSource": source,
            "recognized": recognized,
            "errorCodes": codes,
            "domain": domain,
            "suggestion": suggestions[domain],
            "egg": pick_egg(),
            "note": "LLM 视觉轨需 KNOWLEDGE_MEDIA_LLM=on; "
                    "不可用时规则轨兜底(双轨范式)",
            "diagnosedAt": ts(),
        }

    # --------------------------------------------------------
    # 信值解释三件套(观测面——永不关停)
    # --------------------------------------------------------

    async def explain(self, subject: str,
                      order_id: int = None,
                      trust_id: int = None,
                      question: str = "") -> dict:
        """透明化解释(三件套)

        subject:
            order   → 64号 explain_order 确定性重算(三栏)
            profile → 47号画像(riskEMA/命中/历史+复核指引)
            rule    → 57号知识库检索
        LLM 仅生成通俗总结(默认 off——规则轨摘要兜底)。
        """
        if subject not in ("order", "profile", "rule"):
            raise ValueError(
                f"未知解释主题: {subject}"
                f"(可选 order/profile/rule)")
        if subject == "order":
            return await self._explain_order(order_id)
        if subject == "profile":
            return await self._explain_profile(trust_id)
        return await self._explain_rule(question)

    async def _explain_order(self, order_id) -> dict:
        """订单质疑 → 64号确定性重算(计算过程/规则依据/
        历史记录三栏)"""
        if order_id is None:
            raise ValueError("订单解释需提供 orderId")
        from services.xx64_experience_service import (
            Xx64ExperienceService,
        )
        detail = await Xx64ExperienceService() \
            .explain_order(int(order_id))
        steps = detail.get("steps") or []
        summary = (f"本单共应用 {len(steps)} 条规则"
                   f"(R1-R6), 全部数字可溯源——每一步的"
                   f"计算过程与依据如下。")
        return {
            "success": True, "subject": "order",
            "columns": {
                "计算过程": steps,
                "规则依据": [f"{s.get('rule')} "
                              f"{s.get('label', '')}"
                              for s in steps],
                "历史记录": {
                    "orderId": detail.get("orderId"),
                    "orderSnapshot": detail.get("order"),
                },
            },
            "summary": summary,
            "summarySource": "rule",
            "generatedAt": ts(),
        }

    async def _explain_profile(self, trust_id) -> dict:
        """画像 tier 质疑 → 47号画像 + 复核通道指引"""
        if trust_id is None:
            raise ValueError("画像解释需提供 trustId")
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        profile = await TrustRiskProfileService() \
            .get_profile(int(trust_id))
        summary = (f"您的画像当前为 {profile.get('tier')}"
                   f" 档(信任度 {profile.get('trustLevel')}"
                   f", 风险指数 {profile.get('riskEMA')})"
                   f"——画像仅标签不处罚, 如有异议可走"
                   f"复核通道申诉。")
        return {
            "success": True, "subject": "profile",
            "profile": profile,
            "reviewGuide": "复核通道: POST /api/trust/risk/"
                          "/{trustId}/review-request"
                          "(同一档案同时只挂一条待复核)",
            "summary": summary,
            "summarySource": "rule",
            "generatedAt": ts(),
        }

    async def _explain_rule(self, question: str) -> dict:
        """规则质疑 → 57号知识库检索(信值规则域优先)"""
        from services.knowledge_service import (
            KnowledgeService,
        )
        entries = []
        try:
            entries = await KnowledgeService().search(
                str(question or "信值规则"),
                category="faq", top_k=3, record_hit=True)
        except Exception as exc:
            logger.warning("xx66_explain_rule_failsoft: %s",
                           exc)
        summary = (f"为您检索到 {len(entries)} 条相关规则"
                   f"说明——全部来自平台知识库, 可展开查看"
                   f"依据。") if entries else (
            "知识库暂无完全匹配的条目——已记录缺口, "
            "您也可以直接描述具体疑问。")
        return {
            "success": True, "subject": "rule",
            "entries": entries,
            "summary": summary,
            "summarySource": "rule",
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # 愉悦组件: 工程师手记草稿(规则模板+LLM 润色)
    # --------------------------------------------------------

    async def draft_engineer_note(self, topic: str,
                                  detail: str = "") -> dict:
        """工程师手记草稿(LLM 生成故事稿 → 人工发布;
        P1 交付草稿能力, 发布经 57号合规审查+人工)"""
        if not str(topic or "").strip():
            raise ValueError("手记主题不可为空")
        note = None
        from services.xx66_service import llm_mode
        if llm_mode() == "on":
            try:
                from services.llm_client import (
                    provider_client, llm_enabled,
                )
                if llm_enabled():
                    note = await provider_client.chat(
                        "你是平台的工程师小匠, 请为一次已"
                        "修复的故障写一段 120 字内的\"工程师"
                        "手记\"故事稿: 说明发生了什么、如何"
                        "修复、如何防再发。语气真诚, 禁止"
                        "技术术语堆砌, 禁止承诺数字。",
                        f"主题: {topic}\n细节: {detail}",
                        temperature=0.6)
            except Exception as exc:
                logger.warning(
                    "xx66_note_llm_fallback_rule: %s", exc)
        source = "llm" if note else "rule"
        if not note:
            detail_text = str(detail or "详见故障报告")
            note = (f"【工程师手记·{topic}】这次故障的"
                    f"原因已定位并修复({detail_text})。"
                    f"我们补上了对应的监控与预案, 尽量让"
                    f"同样的问题不再发生。感谢每一位耐心"
                    f"等待的您——您的信任是我们最在意的"
                    f"资产。")
        return {
            "success": True, "topic": topic,
            "draft": note, "source": source,
            "publishGuide": "发布需经 57号合规审查"
                            "(违禁词+医药断言禁用)+人工确认",
            "draftedAt": ts(),
        }
