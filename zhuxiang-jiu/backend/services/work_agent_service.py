"""40号·平台流量DV博主模块·GLM-5.3 Agent 跟随内容工厂

四步 Agent 链(设计文档 §2.3, 复用 36号骨架: 每步独立降级 +
agentTrace 留痕, 步序重定义):
    Step1 作品理解Agent  : chat() 理解标题文案 + vision() 看封面图
                           (图片不可达 → 纯文本轨)
                           → {theme, mood, brandWords}
    Step2 粉丝画像Agent  : 博主领域标签 × 作品主题
                           → {audience, tone, tabooWords}
                           (回退博主池静态画像)
    Step3 跟随生成Agent  : 三段式合规跟随文案(转述+致敬+引荐)
                           + 平台适配 + KOL 短码挂链(链接由
                           blogger_service best-effort 创建注入)
    Step4 出处自查Agent  : @原作者 + 出处语义 + n-gram 搬运检测
                           (重合度>40% 自动重写→规则轨)

三级降级(产出永不中断):
    glm-5.3 → glm-4-flash 重试一次 → 规则模板轨(同样三段式结构)

对接:
    - llm_client.provider_client.chat/vision
    - blogger_service 编排调用(本模块只管"怎么写", 不管"能不能发")
"""

import json
import logging
from datetime import datetime, UTC

from repositories.blogger_repository import (
    PLATFORM_DOUYIN, PLATFORM_XHS, PLATFORM_WEIBO, PLATFORM_CHANNELS,
    DOMAIN_WINE, DOMAIN_FOOD, DOMAIN_GIFT, DOMAIN_LIFESTYLE,
    PLAGIARISM_OVERLAP_LIMIT,
)
from repositories.promo_repository import (
    PROMO_LLM_MODEL, PROMO_LLM_FALLBACK_MODEL,
    REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
)

logger = logging.getLogger(__name__)

TRACK_PRIMARY = PROMO_LLM_MODEL             # glm-5.3
TRACK_FALLBACK = PROMO_LLM_FALLBACK_MODEL   # glm-4-flash
TRACK_RULE = "rule"                         # 规则模板轨
TRACK_VISION = "vision"                     # 封面理解成功附加轨

# 领域标签 → 中文场景短语(三段式转述轨用)
DOMAIN_LABELS = {
    DOMAIN_WINE: "酒类品鉴",
    DOMAIN_FOOD: "美食搭配",
    DOMAIN_GIFT: "礼赠场景",
    DOMAIN_LIFESTYLE: "生活方式",
}

# 博主池静态画像(Step2 规则轨回退, 平台×领域)
_BLOGGER_PROFILES = {
    PLATFORM_DOUYIN: ("18-35 短视频种草人群", "快节奏、口语化、钩子前置"),
    PLATFORM_XHS: ("20-40 女性种草人群", "真实体验、生活美学、闺蜜分享"),
    PLATFORM_WEIBO: ("18-40 话题互动人群", "话题借势、互动感强"),
    PLATFORM_CHANNELS: ("30+ 熟龄信任消费人群", "信任、克制、真实"),
}

# 三段式结构标记(Step4 出处自查依据)
TRIBUTE_MARK = "灵感来自"
CITATION_WORDS = ("出处", "转自", "灵感来自", "致敬")

# 规则模板轨(三级降级兜底; 同样三段式结构: 转述/致敬/引荐,
# 确定性生成, 不搬运原句——转述段只引领域场景不引原题)
_RULE_TEMPLATES = {
    PLATFORM_DOUYIN: (
        "【转述】刚刷到 {nickname} 的最新作品, 围绕{domain}聊了个"
        "很有意思的话题, 数据已经起飞。\n"
        "【致敬】灵感来自 @{account} 的原创内容, 推荐去主页看完整版。\n"
        "【引荐】同场景我们准备了竹香型白酒: 入口绵甜、落口回甘, "
        "点击主页链接 {link} 了解。\n"
        "（{disclaimer}，{age}周岁以下请勿饮酒）"
    ),
    PLATFORM_XHS: (
        "【转述】{nickname}的新作又更新了, 这次聊的是{domain}, "
        "评论区都在催更。\n\n"
        "【致敬】灵感来自 @{account} 的原创笔记, 出处已注明, "
        "感兴趣去主页支持原作者。\n\n"
        "【引荐】同款{domain}场景, 我的私藏是竹香型白酒——"
        "竹香清雅、入口绵甜, 详情戳 {link}\n\n"
        "#竹香型白酒 #{domain_tag}\n"
        "（{disclaimer}，{age}周岁以下请勿饮酒）"
    ),
    PLATFORM_WEIBO: (
        "【转述】{nickname}更新了, 这期{domain}话题讨论度很高。\n"
        "【致敬】灵感来自 @{account} 的原创作品, 转发致敬原作者。\n"
        "【引荐】{domain}场景少不了竹香型白酒, 入口绵甜落口回甘, "
        "详情 {link}\n"
        "（{disclaimer}，{age}周岁以下请勿饮酒）"
    ),
    PLATFORM_CHANNELS: (
        "【转述】本期推荐关注 {nickname} 的最新作品, "
        "围绕{domain}的分享很有启发。\n\n"
        "【致敬】灵感来自 @{account} 的原创内容, 已注明出处。\n\n"
        "【引荐】{domain}场景里, 竹香型白酒是兼顾面子与口感的选择, "
        "入口绵甜、落口回甘。详情见 {link}\n\n"
        "（{disclaimer}，{age}周岁以下请勿饮酒）"
    ),
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _extract_json(text: str) -> dict | None:
    """从模型回复中提取 JSON 对象(容忍代码围栏/前后缀文本)"""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


# ============================================================
# n-gram 搬运检测(设计文档 §2.5 红线: 重合度 >40%)
# ============================================================

_NGRAM_SIZE = 5   # 字符 5-gram(中文短句侵权粒度)


def _char_ngrams(text: str, n: int = _NGRAM_SIZE) -> set[str]:
    cleaned = "".join((text or "").split())
    if len(cleaned) < n:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i + n] for i in range(len(cleaned) - n + 1)}


def plagiarism_overlap(generated: str, original: str) -> float:
    """搬运检测: 生成文案与原作文案的 n-gram 重合度

    口径: 原作 n-gram 集合中出现在生成文案里的比例
    (原作被"搬运"了多少; 引用标题片段属正常致敬, 阈值 40%)。
    """
    orig_grams = _char_ngrams(original)
    if not orig_grams:
        return 0.0
    gen_grams = _char_ngrams(generated)
    shared = orig_grams & gen_grams
    return round(len(shared) / len(orig_grams), 4)


class WorkAgentService:
    """GLM-5.3 Agent 四步跟随链(每步独立降级 + 轨迹留痕)"""

    # ============================================================
    # 单步 Agent 调用(带三级降级)
    # ============================================================

    def _chat_json(self, system: str, user: str) -> tuple[dict | None, str]:
        """单步 Agent 调用: 主档→备档→失败(调用方走规则轨)

        Returns:
            (解析后的 JSON dict 或 None, 实际走轨标签)
        """
        from services.llm_client import provider_client
        for model in (TRACK_PRIMARY, TRACK_FALLBACK):
            try:
                reply = provider_client.chat(system, user, model=model)
            except Exception as exc:   # 网络异常等 → 试下一档
                logger.warning("work_agent_chat_error model=%s: %s",
                               model, exc)
                continue
            data = _extract_json(reply)
            if data is not None:
                return data, model
        return None, TRACK_RULE

    # ============================================================
    # Step1 作品理解(chat + vision 双轨)
    # ============================================================

    def understand_work(self, work: dict,
                        blogger: dict) -> tuple[dict, str]:
        """Step1: 作品 → {主题, 情绪, 酒类相关词}

        vision() 看封面图(不可达/未配置 → 纯文本轨, 不阻断);
        chat() 理解标题文案; 双双失败 → 规则轨(领域+关键词确定性推导)。
        """
        cover_desc = ""
        vision_track = "skipped"
        cover_url = work.get("coverUrl", "")
        if cover_url:
            try:
                from services.llm_client import provider_client
                desc = provider_client.vision(
                    "客观描述这张作品封面图的内容与场景", cover_url)
                if desc:
                    cover_desc = desc[:200]
                    vision_track = TRACK_VISION
            except Exception as exc:
                logger.warning("work_agent_vision_failed: %s", exc)
        system = (
            "你是短视频内容分析师。理解给定博主作品, 提取主题与情绪。"
            "只输出 JSON, 格式: "
            '{"theme": "作品主题短语", "mood": "情绪基调", '
            '"brandWords": ["酒类/场景相关词"]}。'
            "不得编造作品中没有的信息。"
        )
        user = (f"博主: {blogger.get('nickname', '')}"
                f"({DOMAIN_LABELS.get(blogger.get('domain', ''), '生活')})\n"
                f"作品标题: {work.get('title', '')}\n"
                f"作品文案: {work.get('summary', '')}\n"
                f"互动: 赞{work.get('likes', 0)}/评{work.get('comments', 0)}"
                f"/转{work.get('shares', 0)}\n"
                f"封面描述: {cover_desc or '(不可达, 纯文本轨)'}")
        data, track = self._chat_json(system, user)
        if data is None:
            from services.ai_scoring_service import BloggerWorkScorer
            text = f"{work.get('title', '')} {work.get('summary', '')}"
            brand_words = [w for w in BloggerWorkScorer.BRAND_FIT_WORDS
                           if w in text]
            data = {
                "theme": DOMAIN_LABELS.get(
                    blogger.get("domain", ""), "生活方式") + "分享",
                "mood": "轻松种草",
                "brandWords": brand_words,
            }
        data["coverDesc"] = cover_desc
        return data, (f"{track}+{vision_track}"
                      if "+" not in track else track)

    # ============================================================
    # Step2 粉丝画像匹配(领域×平台)
    # ============================================================

    def match_audience(self, blogger: dict,
                       analysis: dict) -> tuple[dict, str]:
        """Step2: 博主领域标签 × 作品主题 → 受众/调性/禁忌

        规则轨兜底: 博主池静态画像(平台×领域)。
        """
        base_audience, base_tone = _BLOGGER_PROFILES.get(
            blogger.get("platform", ""), _BLOGGER_PROFILES[PLATFORM_DOUYIN])
        domain_label = DOMAIN_LABELS.get(
            blogger.get("domain", ""), "生活方式")
        base = {
            "audience": f"{base_audience}({domain_label}兴趣)",
            "tone": base_tone,
            "tabooWords": ["贬低原作者", "搬运原句", "夸大功效"],
        }
        system = (
            "你是社交媒体受众运营专家。基于博主画像与作品主题, "
            "给出跟随内容的受众与调性建议。只输出 JSON, 格式: "
            '{"audience": "目标人群", "tone": "跟随调性", '
            '"tabooWords": ["避免的表述"]}。'
        )
        user = (f"博主: {blogger.get('nickname', '')} "
                f"(领域:{domain_label}, 粉丝{blogger.get('fansWan', 0)}万)\n"
                f"平台: {blogger.get('platform', '')}\n"
                f"作品主题: {analysis.get('theme', '')}\n"
                f"作品情绪: {analysis.get('mood', '')}")
        data, track = self._chat_json(system, user)
        if data is None:
            data = base
        return data, track

    # ============================================================
    # Step3 跟随内容生成(三段式, 规则轨同样三段式结构)
    # ============================================================

    def generate_follow(self, work: dict, blogger: dict,
                        analysis: dict, audience: dict,
                        short_link: str = "") -> tuple[dict, str]:
        """Step3: 全上下文 → 三段式合规跟随文案

        三段式合规范式(避版权): 转述(自己的话)+ 致敬(@原作者+出处)
        + 引荐(自有产品+短码挂链)。规则轨兜底模板同样三段式,
        必含警示语/年龄提示, 不搬运原句。
        """
        domain_label = DOMAIN_LABELS.get(
            blogger.get("domain", ""), "生活方式")
        system = (
            "你是白酒品牌的新媒体编辑, 擅长'合规跟随'创作——借鉴热门"
            "作品的选题思路, 但绝不搬运原文。严格遵守: 1)三段式结构: "
            "【转述】用自己的话概括原作选题 / 【致敬】含 \"@原作者账号\""
            "与出处声明 / 【引荐】自然引出自家竹香型白酒; 2)不得照抄"
            "原作任何句子; 3)不得出现饮酒动作描写; 4)文案必须原样包含 "
            f"\"{REQUIRED_DISCLAIMER}\" 与 \"{REQUIRED_AGE_TIP}\" 字样; "
            f"5)引荐段必须包含短链 {short_link or '(无短链则引导主页)'}。"
            "只输出 JSON, 格式: "
            '{"title": "标题", "body": "正文(三段式)", '
            '"hashtags": "#标签", "cta": "行动号召", '
            '"imageChoice": "product|ai_generated"}。'
        )
        user = (f"原作品: {work.get('title', '')} "
                f"(@{blogger.get('account', '')} "
                f"{blogger.get('nickname', '')} 发布)\n"
                f"作品主题: {analysis.get('theme', '')}\n"
                f"作品情绪: {analysis.get('mood', '')}\n"
                f"平台: {blogger.get('platform', '')}\n"
                f"目标受众: {audience.get('audience', '')}\n"
                f"跟随调性: {audience.get('tone', '')}\n"
                f"挂链短码: {short_link}")
        data, track = self._chat_json(system, user)
        if data is None or not (data.get("body") or "").strip():
            platform = blogger.get("platform", "")
            body = _RULE_TEMPLATES.get(
                platform, _RULE_TEMPLATES[PLATFORM_DOUYIN]).format(
                nickname=blogger.get("nickname", "博主"),
                account=blogger.get("account", ""),
                domain=domain_label,
                domain_tag=domain_label,
                link=short_link or "主页链接",
                disclaimer=REQUIRED_DISCLAIMER,
                age=REQUIRED_AGE_TIP)
            data = {
                "title": (f"{blogger.get('nickname', '博主')}新作"
                          f"同款{domain_label}思路"),
                "body": body,
                "hashtags": f"#竹香型白酒 #{domain_label}",
                "cta": "点击链接了解详情",
                "imageChoice": "product",
            }
        return data, track

    # ============================================================
    # Step4 出处自查(@原作者 + 出处语义 + 搬运检测)
    # ============================================================

    def source_check(self, draft: dict, work: dict,
                     blogger: dict) -> tuple[dict, str]:
        """Step4: 出处合规自查, 缺项自动补齐, 搬运自动重写

        检查项: ①含 @原作者账号; ②含出处语义词; ③与原作文案
        n-gram 重合度 ≤40%(超限 → 规则轨模板重写, 确定性安全)。
        """
        body = draft.get("body", "")
        account = blogger.get("account", "")
        at_ok = f"@{account}" in body
        citation_ok = any(w in body for w in CITATION_WORDS)
        overlap = plagiarism_overlap(
            body, f"{work.get('title', '')} {work.get('summary', '')}")
        rewritten = False
        # ① 缺 @原作者 → 追加致敬行
        if not at_ok or not citation_ok:
            tribute = (f"【致敬】{TRIBUTE_MARK} @{account} 的原创作品, "
                       "出处已注明。")
            body = f"{body}\n{tribute}"
            at_ok = f"@{account}" in body
            citation_ok = any(w in body for w in CITATION_WORDS)
            rewritten = True
        # ② 搬运超限 → 规则轨模板整体重写(不再引用原句)
        if overlap > PLAGIARISM_OVERLAP_LIMIT:
            domain_label = DOMAIN_LABELS.get(
                blogger.get("domain", ""), "生活方式")
            body = _RULE_TEMPLATES.get(
                blogger.get("platform", ""),
                _RULE_TEMPLATES[PLATFORM_DOUYIN]).format(
                nickname=blogger.get("nickname", "博主"),
                account=account,
                domain=domain_label,
                domain_tag=domain_label,
                link="主页链接",
                disclaimer=REQUIRED_DISCLAIMER,
                age=REQUIRED_AGE_TIP)
            overlap = plagiarism_overlap(
                body, f"{work.get('title', '')} {work.get('summary', '')}")
            rewritten = True
        data = {
            "revisedBody": body,
            "overlapRatio": overlap,
            "selfCheck": {
                "atAuthorOk": at_ok,
                "citationOk": citation_ok,
                "overlapOk": overlap <= PLAGIARISM_OVERLAP_LIMIT,
                "rewritten": rewritten,
                "notes": "出处自查完成" + ("(已自动修订)" if rewritten
                                          else "(一次通过)"),
            },
        }
        return data, TRACK_RULE if rewritten else "llm"

    # ============================================================
    # 四步链编排入口
    # ============================================================

    async def generate_follow_content(
            self, work: dict, blogger: dict,
            short_link: str = "") -> dict:
        """对单件侦测作品生成三段式跟随内容(四步链全流程)

        Args:
            work: 侦测作品记录(含 title/summary/coverUrl/互动数)
            blogger: 博主池记录(含 platform/domain/account/nickname)
            short_link: KOL 短码链接(blogger_service best-effort
                创建后注入, 空则引导主页)

        Returns:
            {"title", "body", "hashtags", "cta", "imageChoice",
             "analysis", "audience", "selfCheck", "overlapRatio",
             "agentTrace"}
        """
        analysis, step1_track = self.understand_work(work, blogger)
        audience, step2_track = self.match_audience(blogger, analysis)
        draft, step3_track = self.generate_follow(
            work, blogger, analysis, audience, short_link=short_link)
        checked, step4_track = self.source_check(draft, work, blogger)
        body = checked.get("revisedBody") or draft.get("body", "")
        return {
            "title": (draft.get("title") or "").strip(),
            "body": body,
            "hashtags": draft.get("hashtags", ""),
            "cta": draft.get("cta", ""),
            "imageChoice": draft.get("imageChoice", "product"),
            "analysis": analysis,
            "audience": audience,
            "selfCheck": checked.get("selfCheck", {}),
            "overlapRatio": checked.get("overlapRatio", 0.0),
            "agentTrace": {
                "step1Understand": step1_track,
                "step2Audience": step2_track,
                "step3Generate": step3_track,
                "step4SourceCheck": step4_track,
            },
        }
