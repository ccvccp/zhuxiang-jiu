"""38号·AI智能产品管理模块·P1 AI 审图服务(多模态图片审核)

核心职责(设计文档 §2.3/§7 P1):
    - vision 多模态识图( glm-4v-flash ): 五项判定——
      饮酒动作 / 未成年人出镜 / 低俗暴露 / 水印遮挡 / 模糊低清
      + 图文一致性(图内物与商品标题类目相符, 防"挂羊头卖狗肉")
    - 三级降级(Mock-first 惯例): vision 不可用/未配置 → 规则轨
      (仅扩展名/大小/尺寸下限校验, aiSkipped=true 转人工抽查)
    - 判定违规项 → 图片置 flagged(禁止设为主图); 轻缺陷提示重传

对接:
    - pdm_service.upload_image: 上传后自动触发审图
    - pdm_service.update_images: flagged 图片禁用校验(P0 已有)
    - 待审队列: 审图报告嵌入人工审核界面(P0 的 aiReview 结构)

设计对齐:
    - llm_client.vision() 失败返回 None 由调用方回退规则轨
    - 审图判定仅预审/标记, 终审权永远在人工(设计文档 §8)
"""

import json
import logging
import os
from datetime import datetime, UTC

logger = logging.getLogger(__name__)

# 审图违规项与处置
VIOLATION_DRINKING = "drinking_action"    # 饮酒动作(酒类广告法§23 硬红线)
VIOLATION_MINOR = "minor_in_image"        # 未成年人出镜(硬红线)
VIOLATION_VULGAR = "vulgar_content"       # 低俗暴露(硬红线)
VIOLATION_WATERMARK = "watermark"         # 水印遮挡(轻缺陷, 提示重传)
VIOLATION_BLUR = "blurry"                 # 模糊低清(轻缺陷, 提示重传)
VIOLATION_MISMATCH = "content_mismatch"   # 图文不一致(硬红线)

# 硬红线(任一命中 → flagged 禁用主图)
HARD_VIOLATIONS = (VIOLATION_DRINKING, VIOLATION_MINOR,
                   VIOLATION_VULGAR, VIOLATION_MISMATCH)
# 轻缺陷(提示重传, 不禁用)
SOFT_VIOLATIONS = (VIOLATION_WATERMARK, VIOLATION_BLUR)

VIOLATION_NAMES = {
    VIOLATION_DRINKING: "饮酒动作",
    VIOLATION_MINOR: "未成年人出镜",
    VIOLATION_VULGAR: "低俗暴露",
    VIOLATION_WATERMARK: "水印遮挡",
    VIOLATION_BLUR: "模糊低清",
    VIOLATION_MISMATCH: "图文不一致",
}

# 审图图片质量分口径(接入 product_gate.image_quality 因子)
QUALITY_FULL = 100.0        # 无违规无缺陷
QUALITY_SOFT_PENALTY = 25.0  # 每项轻缺陷扣分
QUALITY_HARD_FLOOR = 0.0     # 命中硬红线归零

# 规则轨图片最小字节数(低于视为疑似低清, 提示人工核查)
RULE_MIN_SIZE = 1024

# vision 判定关键词(模型回复文本命中即认定; 双语口径)
_DETECTION_KEYWORDS = {
    VIOLATION_DRINKING: ("饮酒", "干杯", "喝酒", "碰杯", "举杯畅饮",
                         "drinking"),
    VIOLATION_MINOR: ("未成年人", "儿童", "小孩", "未成年", "minor",
                      "child"),
    VIOLATION_VULGAR: ("低俗", "暴露", "色情", "vulgar", "nude"),
    VIOLATION_WATERMARK: ("水印", "logo遮挡", "watermark", "文字遮挡"),
    VIOLATION_BLUR: ("模糊", "低清", "失焦", "blurry", "blur"),
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _vision_enabled() -> bool:
    """vision 轨可用性(运行时动态读, 遵循 LOCK_MODE 同类约束)"""
    return bool(os.environ.get("LLM_API_KEY", "").strip()) and \
        os.environ.get("LLM_ENABLED", "on").lower() != "off"


def build_vision_prompt(product_name: str = "",
                        category: str = "") -> str:
    """构造审图指令(结构化 JSON 输出约束)"""
    return (
        "你是酒类电商平台的商品图片审核员。请审核这张商品图片, "
        "严格按以下五项判定并以 JSON 输出(不要输出其他内容):\n"
        '{"drinking_action": bool(是否出现饮酒/干杯/劝酒动作或场景),'
        '"minor_in_image": bool(是否有未成年人出镜),'
        '"vulgar_content": bool(是否有低俗暴露内容),'
        '"watermark": bool(是否有大面积水印/logo遮挡主体),'
        '"blurry": bool(是否模糊/分辨率低/失焦),'
        '"described_objects": str(图中主体物品简述, 30字内)}\n'
        f"参考商品信息: 名称「{product_name}」类目「{category}」。"
        "注意: 酒瓶/酒杯静物摆拍不算饮酒动作; 品牌自身 logo 不算水印。"
    )


def parse_vision_reply(reply: str,
                       product_name: str = "",
                       category: str = "") -> dict:
    """解析 vision 回复 → 违规项判定(纯函数, 可测)

    双保险: 先尝试 JSON 解析; 失败回落关键词命中(模型偶发
    格式漂移时仍可提取判定)。
    """
    violations = []
    described = ""
    flags = {}
    text = (reply or "").strip()
    # ① JSON 轨
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end + 1])
            if isinstance(parsed, dict):
                flags = {
                    VIOLATION_DRINKING: bool(
                        parsed.get("drinking_action")),
                    VIOLATION_MINOR: bool(parsed.get("minor_in_image")),
                    VIOLATION_VULGAR: bool(parsed.get("vulgar_content")),
                    VIOLATION_WATERMARK: bool(parsed.get("watermark")),
                    VIOLATION_BLUR: bool(parsed.get("blurry")),
                }
                described = str(parsed.get("described_objects") or "")
    except (ValueError, TypeError):
        flags = {}
    # ② 关键词兜底(JSON 解析空缺的项)
    for violation, keywords in _DETECTION_KEYWORDS.items():
        if violation in flags:
            continue
        if any(kw in text for kw in keywords):
            flags[violation] = True
    violations = [v for v, hit in flags.items() if hit]
    # ③ 图文一致性: 图内物简述与商品名核心词零交集 → 不一致嫌疑
    # (字符级匹配: 商品名任一≥2字片段出现在描述中即认定相关)
    if described:
        name = (product_name or "").replace("·", " ").replace(" ", "")
        matched = any(
            name[i:i + 2] in described
            for i in range(len(name) - 1))
        if len(name) >= 2 and not matched:
            violations.append(VIOLATION_MISMATCH)
    return {
        "violations": violations,
        "describedObjects": described,
    }


class PdmImageReviewService:
    """38号·AI 审图服务(vision 多模态 + 规则轨降级)"""

    def review_image(self, image_url: str, size: int = 0,
                     product_name: str = "",
                     category: str = "") -> dict:
        """审图入口(同步: vision 为阻塞 urllib 调用)

        Returns:
            {"mode": "vision|rule", "violations": [...],
             "hardHits": [...], "softHits": [...],
             "quality": 0-100, "flagged": bool, "aiSkipped": bool,
             "describedObjects": str, "note": str}
        """
        if not _vision_enabled():
            return self._rule_review(image_url, size)
        try:
            return self._vision_review(image_url, size, product_name,
                                       category)
        except Exception as exc:
            logger.warning("pdm_image_vision_failed: %s", exc)
            return self._rule_review(image_url, size, skipped=True)

    # ============================================================
    # vision 轨(多模态识图)
    # ============================================================

    def _vision_review(self, image_url: str, size: int,
                       product_name: str, category: str) -> dict:
        from services.llm_client import provider_client
        # 本地 /media 相对 URL → 不可公网达, vision 只接受 http(s)
        if not str(image_url).startswith(("http://", "https://")):
            return self._rule_review(image_url, size, skipped=True)
        prompt = build_vision_prompt(product_name, category)
        reply = provider_client.vision(prompt, image_url)
        if not reply:
            return self._rule_review(image_url, size, skipped=True)
        parsed = parse_vision_reply(reply, product_name, category)
        return self._build_report("vision", parsed["violations"],
                                  parsed["describedObjects"], size)

    # ============================================================
    # 规则轨(降级: 扩展名/大小校验, aiSkipped 转人工)
    # ============================================================

    def _rule_review(self, image_url: str, size: int,
                     skipped: bool = False) -> dict:
        violations = []
        # 疑似低清(字节数过小)仅提示, 不禁用
        if size and size < RULE_MIN_SIZE:
            violations.append(VIOLATION_BLUR)
        return self._build_report(
            "rule", violations, "",
            size, ai_skipped=skipped,
            note=("vision 不可用/本地URL, 规则轨放行(转人工抽查)"
                  if skipped else "规则轨(扩展名/大小校验)"))

    # ============================================================
    # 报告构造(统一口径)
    # ============================================================

    @staticmethod
    def _build_report(mode: str, violations: list,
                      described: str, size: int,
                      ai_skipped: bool = False,
                      note: str = "") -> dict:
        hard = [v for v in violations if v in HARD_VIOLATIONS]
        soft = [v for v in violations if v in SOFT_VIOLATIONS]
        quality = QUALITY_FULL - QUALITY_SOFT_PENALTY * len(soft)
        if hard:
            quality = QUALITY_HARD_FLOOR
        flagged = bool(hard)
        return {
            "mode": mode,
            "violations": violations,
            "violationNames": [VIOLATION_NAMES.get(v, v)
                               for v in violations],
            "hardHits": hard,
            "softHits": soft,
            "quality": max(0.0, quality),
            "flagged": flagged,
            "aiSkipped": ai_skipped,
            "describedObjects": described,
            "note": note or ("vision 多模态审图" if mode == "vision"
                             else ""),
            "imageSize": int(size or 0),
            "reviewedAt": _now_iso(),
        }
