"""36号·AI智能推广模块·P1 受众匹配引擎服务

核心职责(设计文档 §3.3):
    - 受众画像库 CRUD(平台画像 admin 可配, 种子幂等初始化)
    - 三维匹配: 内容角度 × 平台画像 × 产品调性 → 匹配分与建议
    - 站内画像回传: 聚合站内会员等级分布校准画像权重(P1)

对接:
    - repositories.promo_repository: 画像表 + 亲和度矩阵
    - member_repository: 站内会员数据(best-effort, 失败不阻断)
    - promo_agent_service Step2: 消费画像库(profiles 参数注入)

异常约定:
    - KeyError → 404(画像不存在)
    - ValueError → 409(平台/角度/调性非法)
"""

import logging
from datetime import datetime, UTC

from repositories.promo_repository import (
    PromoRepository,
    PROMO_PLATFORMS,
    DEFAULT_AUDIENCE_PROFILES,
    ANGLE_PLATFORM_AFFINITY,
    PRODUCT_TONES,
    MATCH_PASS_SCORE,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PromoAudienceService:
    """受众画像库 + 三维匹配 + 站内画像回传"""

    def __init__(self, repo: PromoRepository = PromoRepository()):
        self.repo = repo

    # ============================================================
    # 画像库 CRUD
    # ============================================================

    async def ensure_profiles(self) -> int:
        """初始化平台画像种子(幂等, 返回新增数)"""
        count = 0
        for platform, seed in DEFAULT_AUDIENCE_PROFILES.items():
            if await self.repo.get_audience_profile(platform) is None:
                profile = {**seed, "createdAt": _now_iso(),
                           "updatedAt": ""}
                await self.repo.save_audience_profile(profile)
                count += 1
        return count

    async def list_profiles(self) -> list[dict]:
        await self.ensure_profiles()
        return await self.repo.list_audience_profiles()

    async def get_profile(self, platform: str) -> dict:
        """获取平台画像(不存在时用种子兜底)

        Raises:
            ValueError: 平台无效
        """
        if platform not in PROMO_PLATFORMS:
            raise ValueError(
                f"平台无效({platform}, 须为{'/'.join(PROMO_PLATFORMS)})")
        await self.ensure_profiles()
        profile = await self.repo.get_audience_profile(platform)
        if profile is not None:
            return profile
        return {**DEFAULT_AUDIENCE_PROFILES[platform],
                "createdAt": _now_iso(), "updatedAt": ""}

    async def update_profile(self, platform: str, updates: dict) -> dict:
        """更新平台画像(部分字段, 仅允许画像字段)

        Raises:
            ValueError: 平台无效 / 字段非法
        """
        profile = await self.get_profile(platform)
        allowed = ("audience", "tone", "format", "scenes", "productTones")
        for key, value in (updates or {}).items():
            if key not in allowed:
                raise ValueError(f"画像字段无效({key}, 允许: {'/'.join(allowed)})")
            if not (value or "").strip() if isinstance(value, str) else not value:
                raise ValueError(f"画像字段不能为空({key})")
            profile[key] = tuple(value) if isinstance(value, list) else value
        profile["updatedAt"] = _now_iso()
        return await self.repo.save_audience_profile(profile)

    # ============================================================
    # 三维匹配: 内容角度 × 平台画像 × 产品调性
    # ============================================================

    @staticmethod
    def _angle_affinity(platform: str, angle: str) -> float:
        """第一维: 内容角度 → 平台亲和度(关键词最大命中)"""
        matrix = ANGLE_PLATFORM_AFFINITY.get(platform, {})
        hits = [weight for key, weight in matrix.items() if key in angle]
        return max(hits) if hits else 0.3   # 未命中给保守基础分

    @staticmethod
    def _tone_affinity(profile: dict, product_tone: str) -> float:
        """第二维: 产品调性 → 画像亲和产品调性命中"""
        tones = profile.get("productTones") or ()
        if isinstance(tones, str):
            tones = (tones,)
        hits = [t for t in tones if t and t in product_tone
                or product_tone in t]
        return 1.0 if hits else 0.4

    @staticmethod
    def _scene_affinity(profile: dict, angle: str) -> float:
        """第三维: 擅长场景 × 内容角度(画像场景与角度互含)"""
        scenes = profile.get("scenes") or ()
        if isinstance(scenes, str):
            scenes = (scenes,)
        hits = [s for s in scenes if s and (s in angle or angle in s)]
        return 1.0 if hits else 0.5

    async def match(self, platform: str, angle: str,
                    product_tone: str = "口粮酒") -> dict:
        """三维匹配 → 匹配分(0-1) + 建议

        组合权重: 角度亲和 50% + 产品调性 30% + 场景命中 20%。

        Raises:
            ValueError: 平台/角度/调性非法
        """
        if not (angle or "").strip():
            raise ValueError("内容角度不能为空")
        if product_tone not in PRODUCT_TONES:
            raise ValueError(
                f"产品调性无效({product_tone}, 须为{'/'.join(PRODUCT_TONES)})")
        profile = await self.get_profile(platform)
        angle_affinity = self._angle_affinity(platform, angle.strip())
        tone_affinity = self._tone_affinity(profile, product_tone)
        scene_affinity = self._scene_affinity(profile, angle.strip())
        score = round(0.5 * angle_affinity + 0.3 * tone_affinity
                      + 0.2 * scene_affinity, 3)
        return {
            "platform": platform,
            "angle": angle.strip(),
            "productTone": product_tone,
            "score": score,
            "matched": score >= MATCH_PASS_SCORE,
            "components": {
                "angleAffinity": round(angle_affinity, 3),
                "toneAffinity": round(tone_affinity, 3),
                "sceneAffinity": round(scene_affinity, 3),
            },
            "recommendation": {
                "audience": profile.get("audience", ""),
                "tone": profile.get("tone", ""),
                "format": profile.get("format", ""),
            },
        }

    # ============================================================
    # 站内画像回传(校准画像权重)
    # ============================================================

    async def onsite_feedback(self, platform: str) -> dict:
        """站内画像回传: 聚合会员等级分布, 给画像校准建议(best-effort)

        Raises:
            ValueError: 平台无效
        """
        profile = await self.get_profile(platform)
        total = high_value = 0
        level_distribution: dict[int, int] = {}
        try:
            from repositories.member_repository import MemberRepository
            members = await MemberRepository().list_all()
            total = len(members)
            for member in members:
                level = int(member.get("level", 1) or 1)
                level_distribution[level] = (
                    level_distribution.get(level, 0) + 1)
                if level >= 3:
                    high_value += 1
        except Exception as exc:
            logger.warning("promo_onsite_feedback_failed: %s", exc)
        high_ratio = round(high_value / total, 3) if total else 0.0
        suggestion = ("站内高价值会员占比高, 话术可侧重品质与礼赠场景"
                      if high_ratio >= 0.3 else
                      "站内会员以基础等级为主, 话术可侧重性价比与日常场景")
        return {
            "platform": platform,
            "profile": {"audience": profile.get("audience", ""),
                        "tone": profile.get("tone", "")},
            "onsiteMembers": total,
            "highValueMembers": high_value,
            "highValueRatio": high_ratio,
            "levelDistribution": {
                str(k): v for k, v in sorted(level_distribution.items())},
            "calibrationSuggestion": suggestion,
        }
