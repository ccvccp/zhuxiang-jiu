"""36号·AI智能推广模块·GLM-5.3 Agent 内容工厂

四步 Agent 链(设计文档 §3.4):
    Step1 热点分析Agent  : 热点 → {angle, brandRelevance, riskFlags, focus}
    Step2 受众匹配Agent  : 角度+平台 → {audience, tone, tabooWords}
    Step3 内容生成Agent  : 全上下文 → {title, body, hashtags, cta, coverHint}
    Step4 自查自纠Agent  : 草稿+合规红线 → {revisedBody, selfCheck}

三级降级(产出永不中断):
    glm-5.3(LLM_MODEL_PROMO) → glm-4-flash 重试一次 → 规则模板轨
    每步独立降级, agentTrace 记录各步走轨(glm-5.3/glm-4-flash/rule)。

对接:
    - llm_client.provider_client.chat(model=...) 36号专用模型档位
    - promo_service 编排调用(本模块只管"怎么写", 不管"能不能发")
"""

import json
import logging
from datetime import datetime, UTC

from repositories.promo_repository import (
    PROMO_LLM_MODEL, PROMO_LLM_FALLBACK_MODEL,
    PROMO_PLATFORM_DOUYIN, PROMO_PLATFORM_XHS, PROMO_PLATFORM_MOMENTS,
    PROMO_PLATFORM_WEIBO, PROMO_PLATFORM_CHANNELS,
    REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
)

logger = logging.getLogger(__name__)

TRACK_PRIMARY = PROMO_LLM_MODEL          # glm-5.3
TRACK_FALLBACK = PROMO_LLM_FALLBACK_MODEL   # glm-4-flash
TRACK_RULE = "rule"                      # 规则模板轨

# 平台受众画像(P0 内置兜底; P1 起生成时优先用画像库, 此处仅缺省回退)
PLATFORM_PROFILES = {
    PROMO_PLATFORM_DOUYIN: {
        "audience": "18-35 大众娱乐人群",
        "tone": "快节奏、剧情钩子、口语化",
        "format": "15-45s 短视频脚本(钩子-卖点-行动)",
    },
    PROMO_PLATFORM_XHS: {
        "audience": "20-40 女性种草人群",
        "tone": "真实体验、生活美学、闺蜜分享",
        "format": "标题≤20字 + 正文≤800字 + 标签",
    },
    PROMO_PLATFORM_MOMENTS: {
        "audience": "30+ 熟龄社交圈",
        "tone": "信任、情怀、简短",
        "format": "朋友圈文案(≤140字)",
    },
    PROMO_PLATFORM_WEIBO: {
        "audience": "18-40 话题互动人群",
        "tone": "热点话题借势、互动感强、会玩梗",
        "format": "#话题# + 正文≤140字 + 互动引导",
    },
    PROMO_PLATFORM_CHANNELS: {
        "audience": "30+ 熟龄信任消费人群",
        "tone": "信任、情怀、真实克制",
        "format": "图文短句 + 封面文案(公众号生态)",
    },
}

# 规则模板轨(三级降级兜底, 确定性与 attract GEN_TEMPLATES 同思路)
_RULE_TEMPLATES = {
    PROMO_PLATFORM_DOUYIN: (
        "【热点借势】{title}\n"
        "【0-3s 钩子】最近 \"{title}\" 刷屏了!\n"
        "【3-10s 卖点】团圆聚会怎么少得了竹香型白酒? 入口绵甜、落口回甘, "
        "国潮包装宴席倍有面。\n"
        "【10-15s 行动】点击主页链接, 新客立减!\n"
        "（{disclaimer}，未成年人禁止饮酒，满{age}周岁请适量）"
    ),
    PROMO_PLATFORM_XHS: (
        "{title}｜我的聚会用酒清单\n\n"
        "刷到 \"{title}\" 上热搜, 姐妹们讨论团圆宴安排了吗?\n\n"
        "🎋 竹香型白酒: 竹香清雅, 入口绵甜\n"
        "🎁 婚宴/家宴/送礼场景都在线\n"
        "📋 评论区蹲一个酒友交流~\n\n"
        "#竹香型白酒 #聚会好物 #热点\n"
        "（{disclaimer}，{age}周岁以下请勿饮酒）"
    ),
    PROMO_PLATFORM_MOMENTS: (
        "\"{title}\"冲上热搜, 聚会安排起来。\n"
        "竹香型白酒, 入口绵甜落口回甘, 家宴礼赠都合适。\n"
        "👉 详情见主页链接\n"
        "（{disclaimer}，{age}+ 请适量饮用）"
    ),
    PROMO_PLATFORM_WEIBO: (
        "#{title}# 话题冲上热搜, 聚会用酒安排上。\n"
        "竹香型白酒, 竹香清雅入口绵甜, 家宴小聚都合适。\n"
        "评论区聊聊你的聚会安排~ 转发抽一位酒友送品鉴装🎁\n"
        "（{disclaimer}，{age}周岁以下请勿饮酒）"
    ),
    PROMO_PLATFORM_CHANNELS: (
        "\"{title}\"引热议, 又到团圆聚会季。\n\n"
        "竹香型白酒, 入口绵甜、落口回甘。\n"
        "好酒不必贵, 家宴礼赠都拿得出手。\n\n"
        "详情见主页, 欢迎评论区交流。\n"
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


class PromoAgentService:
    """GLM-5.3 Agent 四步链(每步独立降级 + 轨迹留痕)"""

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
                logger.warning("promo_agent_chat_error model=%s: %s",
                               model, exc)
                continue
            data = _extract_json(reply)
            if data is not None:
                return data, model
        return None, TRACK_RULE

    # ============================================================
    # Step1 热点分析(同热点多平台共享一次)
    # ============================================================

    def analyze_hotspot(self, hotspot: dict) -> tuple[dict, str]:
        """Step1: 热点 → 切入角度/相关性/风险/产品聚焦

        规则轨兜底: 按品牌命中词确定性推导。
        """
        system = (
            "你是白酒品牌的热点营销分析师。分析给定网络热点, 判断竹香型"
            "白酒品牌如何自然借势。只输出 JSON, 格式: "
            '{"angle": "切入角度短语", "brandRelevance": "高/中/低", '
            '"riskFlags": ["风险点"], "focus": "建议聚焦的产品场景"}。'
            "不得编造热点中没有的信息。"
        )
        user = (f"热点标题: {hotspot.get('title', '')}\n"
                f"热点摘要: {hotspot.get('summary', '')}\n"
                f"热度: {hotspot.get('heat', 0)}万\n"
                f"品牌命中词: {hotspot.get('brandHits', [])}")
        data, track = self._chat_json(system, user)
        if data is None:
            hits = hotspot.get("brandHits") or []
            data = {
                "angle": "热点借势·场景种草",
                "brandRelevance": "高" if len(hits) >= 2 else "中",
                "riskFlags": hotspot.get("riskFlags", []),
                "focus": "聚会/宴席场景",
            }
        return data, track

    # ============================================================
    # Step2 受众匹配(按平台)
    # ============================================================

    def match_audience(self, platform: str, analysis: dict,
                       profile: dict = None) -> tuple[dict, str]:
        """Step2: 平台画像 + 分析结果 → 话术基调与禁忌

        P1: profile 来自受众画像库(admin 可配), 缺省回退内置画像。
        规则轨兜底: 直接使用平台画像。
        """
        base = profile or PLATFORM_PROFILES.get(platform, PLATFORM_PROFILES[
            PROMO_PLATFORM_DOUYIN])
        system = (
            "你是社交媒体受众运营专家。基于平台画像给出内容话术建议。"
            "只输出 JSON, 格式: "
            '{"audience": "目标人群描述", "tone": "话术基调", '
            '"tabooWords": ["该平台避免的词"]}。'
        )
        user = (f"平台: {platform}\n"
                f"画像: {json.dumps(base, ensure_ascii=False, default=str)}\n"
                f"内容角度: {analysis.get('angle', '')}\n"
                f"产品聚焦: {analysis.get('focus', '')}")
        data, track = self._chat_json(system, user)
        if data is None:
            data = {"audience": base.get("audience", ""),
                    "tone": base.get("tone", ""),
                    "tabooWords": []}
        return data, track

    # ============================================================
    # Step3 内容生成(按平台, P1: 权威引用池注入)
    # ============================================================

    def generate_draft(self, hotspot: dict, platform: str,
                       analysis: dict, audience: dict,
                       citations: list[dict] = None) -> tuple[dict, str]:
        """Step3: 全上下文 → 平台差异化内容草稿

        P1: citations 为权威信源引用池(RAG top-k), prompt 限定
        数字/标准编号只能出自引用池, 禁止编造数据。
        规则轨兜底: 平台模板填充(必含警示语/年龄提示, 保证合规可用)。
        """
        profile = PLATFORM_PROFILES.get(platform, {})
        citation_block = ""
        if citations:
            lines = [
                f"[{i}] {c.get('title', '')}: {c.get('content', '')}"
                f" (引用方式: {c.get('allowedUsage', '')})"
                for i, c in enumerate(citations, start=1)
            ]
            citation_block = ("权威引用池(正文引用的标准编号/数据必须且"
                              "只能出自以下条目):\n"
                              + "\n".join(lines) + "\n")
        system = (
            "你是白酒品牌的资深新媒体编辑, 擅长热点借势内容创作。"
            "严格遵守: 1)只依据给定热点信息, 不编造事实与数据; "
            "2)不得出现饮酒动作描写; 3)不得使用国家机关/权威机构名义作"
            "推荐证明; 4)不得暗示饮酒有消除紧张焦虑等功效; "
            f"5)文案必须原样包含 \"{REQUIRED_DISCLAIMER}\" 与 "
            f"\"{REQUIRED_AGE_TIP}\" 字样; "
            "6)正文引用的标准编号/百分比/数据必须出自权威引用池, "
            "不得自行编造数字。"
            "只输出 JSON, 格式: "
            '{"title": "标题", "body": "正文", "hashtags": "#标签 #标签", '
            '"cta": "行动号召", "coverHint": "封面建议"}。'
        )
        user = (f"热点: {hotspot.get('title', '')} (热度{hotspot.get('heat', 0)}万)\n"
                f"切入角度: {analysis.get('angle', '')}\n"
                f"产品聚焦: {analysis.get('focus', '')}\n"
                f"平台: {platform} ({profile.get('format', '')})\n"
                f"目标人群: {audience.get('audience', '')}\n"
                f"话术基调: {audience.get('tone', '')}\n"
                f"{citation_block}")
        data, track = self._chat_json(system, user)
        if data is None or not (data.get("body") or "").strip():
            title = hotspot.get("title", "热点")
            body = _RULE_TEMPLATES.get(
                platform, _RULE_TEMPLATES[PROMO_PLATFORM_DOUYIN]).format(
                title=title, disclaimer=REQUIRED_DISCLAIMER,
                age=REQUIRED_AGE_TIP)
            data = {
                "title": f"{title}｜竹香酒借势笔记",
                "body": body,
                "hashtags": "#竹香型白酒 #热点",
                "cta": "点击主页链接了解详情",
                "coverHint": "竹林+酒瓶国潮风封面",
            }
        return data, track

    # ============================================================
    # Step4 自查自纠(按平台)
    # ============================================================

    def self_check(self, draft: dict, platform: str) -> tuple[dict, str]:
        """Step4: 草稿合规自查, 缺强制项自动补齐

        规则兜底: 本地确定性补齐(警示语/年龄提示缺失则追加)。
        """
        system = (
            "你是广告合规审查员。检查文案是否满足硬性要求, 不满足则修订。"
            f"硬性要求: 1)含 \"{REQUIRED_DISCLAIMER}\"; 2)含 \"{REQUIRED_AGE_TIP}\""
            "周岁相关提示; 3)无饮酒动作/权威背书/功效暗示表述。"
            "只输出 JSON, 格式: "
            '{"revisedBody": "修订后正文", "selfCheck": '
            '{"disclaimerOk": true, "ageTipOk": true, "notes": "说明"}}。'
        )
        user = (f"平台: {platform}\n文案: {draft.get('body', '')}")
        data, track = self._chat_json(system, user)
        if data is None or not (data.get("revisedBody") or "").strip():
            body = draft.get("body", "")
            missing = (REQUIRED_DISCLAIMER not in body
                       or REQUIRED_AGE_TIP not in body)
            if missing:
                suffix = (f"（{REQUIRED_DISCLAIMER}，"
                          f"{REQUIRED_AGE_TIP}周岁以下请勿饮酒）")
                body = f"{body}\n{suffix}" if body else suffix
            # selfCheck 反映补齐后的最终状态
            data = {
                "revisedBody": body,
                "selfCheck": {
                    "disclaimerOk": REQUIRED_DISCLAIMER in body,
                    "ageTipOk": REQUIRED_AGE_TIP in body,
                    "notes": "规则轨自查(缺失项已自动补齐)" if missing
                             else "规则轨自查通过",
                },
            }
        return data, track

    # ============================================================
    # 四步链编排入口
    # ============================================================

    async def generate_platform_contents(
            self, hotspot: dict,
            platforms: tuple[str, ...] = (PROMO_PLATFORM_DOUYIN,),
            profiles: dict = None,
            citations: list[dict] = None
    ) -> list[dict]:
        """对同一热点生成 N 平台内容(一源多态, Step1/引用池共享)

        P1: profiles 为画像库注入(平台→画像); citations 为权威引用池
        (RAG top-k, Step3 注入 + 服务层溯源校验用)。

        Returns:
            [{"platform", "title", "body", "hashtags", "cta",
              "coverHint", "selfCheck", "agentTrace", "citations"}]
        """
        analysis, step1_track = self.analyze_hotspot(hotspot)
        results = []
        for platform in platforms:
            profile = (profiles or {}).get(platform)
            audience, step2_track = self.match_audience(
                platform, analysis, profile=profile)
            draft, step3_track = self.generate_draft(
                hotspot, platform, analysis, audience,
                citations=citations)
            checked, step4_track = self.self_check(draft, platform)
            body = checked.get("revisedBody") or draft.get("body", "")
            results.append({
                "platform": platform,
                "title": (draft.get("title") or "").strip(),
                "body": body,
                "hashtags": draft.get("hashtags", ""),
                "cta": draft.get("cta", ""),
                "coverHint": draft.get("coverHint", ""),
                "selfCheck": checked.get("selfCheck", {}),
                "agentTrace": {
                    "step1Analysis": step1_track,
                    "step2Audience": step2_track,
                    "step3Generate": step3_track,
                    "step4SelfCheck": step4_track,
                },
                # 引用池快照(溯源校验与 authorityRefs 由服务层落库)
                "citations": citations or [],
            })
        return results
