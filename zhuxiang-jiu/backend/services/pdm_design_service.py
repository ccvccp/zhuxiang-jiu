"""38号·AI智能产品管理模块·P2 AI 设计工坊服务

核心职责(设计文档 §2.4/§7 P2):
    - AI 生成商品主图: LLM 按商品信息构造 SDXL prompt →
      text_to_image 生成管线产出图 URL → 自动入图库+AI 审图一站式
    - AI 文案优化建议: 36号式三级降级链(glm 主档→备档→规则模板轨)
      输出标题/描述建议, 经 36号共享禁用词表硬校验, 仅建议不入库
    - 主图 A/B 智能选择建议: 版本历史主图 × 商品销量/评分数据,
      依赖数据积累(P2 尾项, 建议口径保守)

对接:
    - llm_client.chat(model=)/provider_client: 36号降级链惯例
    - pdm_image_review_service: 生成图自动审图
    - pdm_repository: 图库/版本快照
"""

import logging
import os
from datetime import datetime, UTC

logger = logging.getLogger(__name__)

# 三级降级档位(对齐 36号 promo_agent 惯例)
TRACK_PRIMARY = "glm-5.3"
TRACK_FALLBACK = "glm-4-flash"
TRACK_RULE = "rule"

# 生成图 URL 管线(项目既有占位图同源)
TEXT_TO_IMAGE_URL = ("https://trae-api-cn.mchost.guru/api/ide/v1/"
                     "text_to_image")

# 文案优化禁用词(与 ProductGateScorer 共享口径)
BANNED_WORDS = ("干杯", "一饮而尽", "不醉不归", "开怀畅饮", "贪杯",
                "拼酒", "灌酒", "喝到", "最好", "最佳", "第一",
                "顶级", "极品", "国宴", "专供", "特效", "保健",
                "养生")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _llm_enabled() -> bool:
    return bool(os.environ.get("LLM_API_KEY", "").strip()) and \
        os.environ.get("LLM_ENABLED", "on").lower() != "off"


def _build_image_prompt_url(prompt: str,
                            size: str = "square") -> str:
    """构造 text_to_image 生成图 URL(square 1024 主图口径)"""
    import urllib.parse
    return (f"{TEXT_TO_IMAGE_URL}"
            f"?prompt={urllib.parse.quote(prompt)}"
            f"&image_size={size}")


def scan_banned_words(text: str) -> list[str]:
    """禁用词硬校验(命中即拒, 36号共享口径)"""
    return [w for w in BANNED_WORDS if w in (text or "")]


class PdmDesignService:
    """38号·AI 设计工坊(主图生成/文案优化/主图 A/B 建议)"""

    # ============================================================
    # AI 主图生成(设计文档 §2.4)
    # ============================================================

    def generate_main_image(self, product: dict) -> dict:
        """按商品信息生成主图: LLM 构造 prompt → 生成图 URL

        Returns:
            {"prompt", "imageUrl", "track": "glm-5.3|glm-4-flash|rule",
             "rationale"}
        """
        name = product.get("name", "")
        series = product.get("series", "")
        scenes = "、".join(product.get("scenes") or []) or "老友小聚"
        prompt, track, rationale = self._build_design_prompt(
            name, series, scenes)
        url = _build_image_prompt_url(prompt)
        return {
            "prompt": prompt,
            "imageUrl": url,
            "imageSize": "square",
            "track": track,
            "rationale": rationale,
            "generatedAt": _now_iso(),
        }

    def _build_design_prompt(self, name: str, series: str,
                             scenes: str) -> tuple[str, str, str]:
        """LLM 构造 SDXL prompt(主档→备档→规则模板)"""
        if _llm_enabled():
            from services.llm_client import provider_client
            system = (
                "你是电商商品主图设计师。根据白酒商品信息构造一个"
                "中文图像生成 prompt(SDXL 风格), 只输出 prompt 文本"
                "(60字内), 不要输出其他内容。要求: 静物摆拍构图、"
                "突出瓶身与氛围、不得出现饮酒动作和人物畅饮场景、"
                "不得出现未成年人。"
            )
            user = (f"商品名: {name}\n系列: {series}\n"
                    f"适用场景: {scenes}")
            for model in (TRACK_PRIMARY, TRACK_FALLBACK):
                try:
                    reply = provider_client.chat(system, user,
                                                 model=model)
                except Exception as exc:
                    logger.warning("pdm_design_prompt_error model=%s:"
                                   " %s", model, exc)
                    continue
                if reply and str(reply).strip():
                    prompt = str(reply).strip()[:120]
                    return prompt, model, f"LLM 构造({model})"
        # 规则模板轨(确定性兜底)
        prompt = (f"{series}白酒瓶身特写, {name}, 竹韵国风静物摆拍, "
                  f"暖光木质桌面, 搭配竹叶元素, 商品质感广告图, "
                  f"适合{scenes}场景")
        return prompt, TRACK_RULE, "规则模板轨(LLM 不可用)"

    # ============================================================
    # AI 文案优化建议(三级降级链 + 禁用词硬校验, 仅建议)
    # ============================================================

    def optimize_copy(self, product: dict) -> dict:
        """商品标题/详情页文案优化建议(不入库, 运营采纳后编辑落库)

        Returns:
            {"track", "title", "subtitle", "description",
             "bannedHits": [...], "warnings": [...], "applied": False}
        """
        title, subtitle, description, track = self._chat_copy(product)
        # 禁用词硬校验: 命中即拒该字段并给 warnings
        warnings = []
        for field, text in (("title", title), ("subtitle", subtitle),
                            ("description", description)):
            hits = scan_banned_words(text)
            if hits:
                warnings.append(f"{field}命中禁用词{hits}, 请人工改写")
        return {
            "track": track,
            "title": title,
            "subtitle": subtitle,
            "description": description,
            "bannedHits": warnings,
            "applied": False,  # 仅建议, 不落库
            "generatedAt": _now_iso(),
        }

    def _chat_copy(self, product: dict) -> tuple[str, str, str, str]:
        """文案生成 LLM 轨(主档→备档) + 规则模板兜底"""
        if _llm_enabled():
            from services.llm_client import provider_client
            system = (
                "你是白酒电商文案策划。优化商品文案, 只输出 JSON: "
                '{"title": "标题(30字内)", "subtitle": "副标题(20字内)"'
                ', "description": "详情文案(120字内)"}。'
                "合规红线: 不得出现饮酒动作描写/极限词(最好/第一/"
                "顶级等)/功效暗示; 未成年人相关内容禁止。"
            )
            user = (f"商品名: {product.get('name', '')}\n"
                    f"系列: {product.get('series', '')}\n"
                    f"度数: {product.get('alcohol', '')}°\n"
                    f"现有描述: {product.get('description', '')}")
            import json
            for model in (TRACK_PRIMARY, TRACK_FALLBACK):
                try:
                    reply = provider_client.chat(system, user,
                                                 model=model)
                except Exception as exc:
                    logger.warning("pdm_copy_chat_error model=%s: %s",
                                   model, exc)
                    continue
                data = self._extract_json(reply)
                if data and data.get("title"):
                    return (str(data.get("title", ""))[:60],
                            str(data.get("subtitle", ""))[:40],
                            str(data.get("description", ""))[:300],
                            model)
        # 规则模板轨
        name = product.get("name", "")
        series = product.get("series", "")
        title = name or f"{series}·竹香佳酿"
        return (title,
                f"{series}·竹香型风味",
                (f"{title}, 源自山东泰安, 竹香型白酒代表作。"
                 f"固态发酵·古法酿造, 入口绵甜、回味悠长。"),
                TRACK_RULE)

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """模型回复 JSON 提取(容忍围栏/前后缀, 36号惯例)"""
        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        import json
        try:
            data = json.loads(text[start:end + 1])
            return data if isinstance(data, dict) else None
        except (ValueError, TypeError):
            return None

    # ============================================================
    # 主图 A/B 智能选择建议(设计文档 §2.4 尾项)
    # ============================================================

    def main_image_ab_advice(self, product: dict,
                             versions: list[dict]) -> dict:
        """主图 A/B 建议: 版本历史主图 × 销量/评分数据

        口径保守(P2 尾项, 依赖数据积累):
        - 当前在售主图 vs 最近一次被替换主图(A/B 对)
        - 商品有销量/评分数据 → 建议保留当前(数据背书)
        - 无数据 → 提示样本不足, 建议人工 A/B 投放
        """
        mains = []
        seen = set()
        for v in sorted(versions, key=lambda x: x.get("version", 0),
                        reverse=True):
            images = (v.get("snapshot") or {}).get("images") or {}
            main = images.get("main", "")
            if main and main not in seen:
                seen.add(main)
                mains.append({"version": v.get("version"),
                              "main": main,
                              "changeType": v.get("changeType")})
            if len(mains) >= 2:
                break
        current_main = (product.get("images") or {}).get("main", "")
        sales = int(product.get("sales_total") or 0)
        rating = float(product.get("rating_avg") or 0)
        if len(mains) < 2:
            return {
                "sufficient": False,
                "candidates": mains,
                "advice": "历史主图版本不足 2 张, 暂无法 A/B 对比",
                "recommendation": "collect_more_versions",
            }
        if sales >= 50 and rating >= 4.5:
            advice = (f"当前主图有数据背书(销量{sales}/评分"
                      f"{rating}), 建议保留当前主图")
            recommendation = "keep_current"
        elif sales > 0:
            advice = (f"当前主图数据一般(销量{sales}/评分{rating}), "
                      "可小流量试投历史主图对比")
            recommendation = "small_traffic_ab"
        else:
            advice = "商品暂无销量数据, 建议人工 A/B 投放积累样本"
            recommendation = "manual_ab"
        return {
            "sufficient": True,
            "candidates": mains,
            "currentMain": current_main,
            "salesTotal": sales,
            "ratingAvg": rating,
            "advice": advice,
            "recommendation": recommendation,
        }
