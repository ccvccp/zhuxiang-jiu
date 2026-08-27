"""网站图标智能管理模块业务逻辑层

核心规则:
    权限管控: 仅管理端(X-Role: admin 鉴权后)可变更主题/图标, 全程审计留痕
    AI 健康度评估(激活把关, 0-100):
        无障碍对比度(35) + 前景背景区分度(20) + 色彩和谐度(15)
        + 图标完整性(15) + 品牌关联度(15)
        score >= aiScoreThreshold(默认60) 才允许激活
    状态机: draft(可编辑) → active(锁定编辑, C 端生效) → archived(可重新激活)
    审计回滚: activate/update/rollback 均落 before/after 快照, 支持一键回滚

异常约定(遵循项目约定):
    - KeyError(message)  → 路由层映射为 404
    - ValueError(message) → 路由层映射为 409
"""

import colorsys
import logging
from datetime import datetime, UTC

from core.locks import get_lock
from repositories.site_theme_repository import (
    SiteThemeRepository, PRESET_THEMES,
)

logger = logging.getLogger(__name__)

# 必填颜色字段
REQUIRED_COLORS = ("primary", "primaryLight", "navBar", "tabSelected",
                   "tabColor", "tabBg", "textOnPrimary")
# tabBar 必填图标
REQUIRED_TAB_ICONS = ("tabHome", "tabProducts", "tabMine")

MODEL_VERSION = "v1"


class SiteThemeService:
    """网站图标智能管理模块业务逻辑层"""

    AI_SCORE_THRESHOLD = 60  # 激活所需的最低 AI 健康度评分

    def __init__(self, store: dict = None):
        self.repo = SiteThemeRepository(store)

    # ============================================================
    # 管理端: 主题方案 CRUD
    # ============================================================

    async def create_theme(self, admin_id: int, name: str, colors: dict,
                           icons: dict = None,
                           description: str = "") -> dict:
        """创建主题方案(初始 draft)

        Raises:
            ValueError: 名称/配色非法
        """
        self._validate_theme(name, colors)
        async with get_lock("theme:write"):
            theme_id = await self.repo.next_theme_id()
            theme = {
                "themeId": theme_id,
                "name": name.strip(),
                "description": (description or "").strip(),
                "colors": colors,
                "icons": icons or {},
                "aiScoreLatest": 0,
                "status": "draft",
                "createdAdminId": admin_id,
                "activatedAt": "",
                "createdAt": self._now(),
            }
            await self.repo.save_theme(theme)
            await self._log(theme_id, admin_id, "create", None, theme)
            return theme

    async def update_theme(self, admin_id: int, theme_id: int,
                           name: str = None, colors: dict = None,
                           icons: dict = None,
                           description: str = None) -> dict:
        """编辑主题(仅 draft 可编辑, active 锁定防误改)

        Raises:
            KeyError: 主题不存在
            ValueError: active 锁定/字段非法
        """
        async with get_lock(f"theme:write:{theme_id}"):
            theme = await self.repo.get_theme(theme_id)
            if not theme:
                raise KeyError(f"主题 {theme_id} 不存在")
            if theme.get("status") == "active":
                raise ValueError(
                    "激活中的主题已锁定编辑, 请复制为新草稿后修改")
            before = dict(theme)
            if name is not None:
                new_name = name.strip()
                if not new_name:
                    raise ValueError("主题名称不能为空")
                theme["name"] = new_name
            if description is not None:
                theme["description"] = description.strip()
            if colors is not None:
                merged = dict(theme.get("colors") or {})
                merged.update(colors)
                self._validate_theme(theme["name"], merged)
                theme["colors"] = merged
            if icons is not None:
                merged_icons = dict(theme.get("icons") or {})
                merged_icons.update(icons)
                theme["icons"] = merged_icons
            theme["aiScoreLatest"] = 0  # 编辑后需重新体检
            await self.repo.update_theme(theme_id, {
                "name": theme["name"], "description": theme["description"],
                "colors": theme["colors"], "icons": theme["icons"],
                "aiScoreLatest": 0})
            await self._log(theme_id, admin_id, "update", before, theme)
            return theme

    async def list_themes(self) -> list[dict]:
        """主题方案列表(管理端)"""
        return await self.repo.list_themes()

    async def archive_theme(self, admin_id: int, theme_id: int) -> dict:
        """归档主题(active 归档前先解除激活)

        Raises:
            KeyError: 主题不存在
        """
        async with get_lock(f"theme:write:{theme_id}"):
            theme = await self.repo.get_theme(theme_id)
            if not theme:
                raise KeyError(f"主题 {theme_id} 不存在")
            before = dict(theme)
            await self.repo.update_theme(theme_id, {"status": "archived"})
            theme["status"] = "archived"
            await self._log(theme_id, admin_id, "archive", before, theme)
            return theme

    # ============================================================
    # AI 健康度评估(激活把关)
    # ============================================================

    async def ai_check(self, theme_id: int) -> dict:
        """AI 健康度评估(B级规则引擎, 纯函数不落库)

        因子: 无障碍对比度(35) + 前景背景区分度(20) + 色彩和谐度(15)
              + 图标完整性(15) + 品牌关联度(15)

        Raises:
            KeyError: 主题不存在
        """
        theme = await self.repo.get_theme(theme_id)
        if not theme:
            raise KeyError(f"主题 {theme_id} 不存在")
        colors = theme.get("colors") or {}
        icons = theme.get("icons") or {}
        factors = []

        # 1. 无障碍对比度(35): 主色 vs 主色上文本色 WCAG 对比度
        ratio = self._contrast_ratio(colors.get("primary", "#000000"),
                                     colors.get("textOnPrimary", "#ffffff"))
        # 4.5:1 满分(AA), 3.0:1 得一半(AA大字), 低于 1.5 得 0
        contrast_score = 35.0
        if ratio < 4.5:
            contrast_score = max(0.0, min(35.0,
                (ratio - 1.5) / 3.0 * 35.0)) if ratio > 1.5 else 0.0
        factors.append({
            "name": "contrast", "label": "无障碍对比度",
            "score": round(contrast_score, 1), "maxScore": 35,
            "detail": f"主色vs文本色 WCAG 对比度 {ratio:.2f}:1"
                      f"(AA 标准 ≥4.5:1)",
            "value": round(ratio, 2)})

        # 2. 前景背景区分度(20): 主色 vs 页面背景 HSL 明度差
        h1, s1, l1 = self._hex_to_hsl(colors.get("primary", "#000000"))
        _, _, l2 = self._hex_to_hsl(colors.get("tabBg", "#ffffff"))
        light_gap = abs(l1 - l2)
        distinct_score = min(20.0, light_gap / 40.0 * 20.0)
        factors.append({
            "name": "distinct", "label": "前景背景区分度",
            "score": round(distinct_score, 1), "maxScore": 20,
            "detail": f"主色与背景明度差 {light_gap:.0f}%"
                      f"(≥40% 满分)",
            "value": round(light_gap, 1)})

        # 3. 色彩和谐度(15): 主色与浅主色色相距离(过近层次不清, 过远违和)
        h2, _, _ = self._hex_to_hsl(colors.get("primaryLight", "#000000"))
        hue_dist = abs(h1 - h2)
        hue_dist = min(hue_dist, 360 - hue_dist)
        harmony_score = 15.0 if hue_dist <= 30 else max(
            0.0, 15.0 - (hue_dist - 30) / 90 * 15.0)
        factors.append({
            "name": "harmony", "label": "色彩和谐度",
            "score": round(harmony_score, 1), "maxScore": 15,
            "detail": f"主色与浅主色色相差 {hue_dist:.0f}°(≤30° 满分)",
            "value": round(hue_dist, 1)})

        # 4. 图标完整性(15): tabBar 3 图标 + 金刚区配置合法
        missing = [k for k in REQUIRED_TAB_ICONS if not icons.get(k)]
        icon_score = 15.0 * (len(REQUIRED_TAB_ICONS) - len(missing)) \
            / len(REQUIRED_TAB_ICONS)
        factors.append({
            "name": "icons", "label": "图标完整性",
            "score": round(icon_score, 1), "maxScore": 15,
            "detail": "tabBar 图标齐全" if not missing
                      else f"缺失图标: {', '.join(missing)}",
            "value": len(REQUIRED_TAB_ICONS) - len(missing)})

        # 5. 品牌关联度(15): 主色落在绿色系(色相 60°-180°)竹香品牌基因
        brand_score = 15.0 if 60 <= h1 <= 180 else max(
            0.0, 15.0 - min(abs(h1 - 120), abs(h1 - 480)) / 60 * 15.0)
        factors.append({
            "name": "brand", "label": "品牌关联度",
            "score": round(brand_score, 1), "maxScore": 15,
            "detail": f"主色色相 {h1:.0f}°(竹绿品牌基因 60°-180°)",
            "value": round(h1, 1)})

        total = round(sum(f["score"] for f in factors), 1)
        passed = total >= self.AI_SCORE_THRESHOLD
        # 更新最近评分
        await self.repo.update_theme(theme_id, {"aiScoreLatest": int(total)})
        result = {
            "success": True,
            "scorer": "site_theme_health",
            "modelVersion": MODEL_VERSION,
            "themeId": theme_id,
            "themeName": theme.get("name", ""),
            "score": total,
            "level": "good" if total >= 85 else
                     "pass" if total >= self.AI_SCORE_THRESHOLD else "fail",
            "passed": passed,
            "threshold": self.AI_SCORE_THRESHOLD,
            "factors": factors,
            "scoredAt": self._now(),
        }
        logger.info("site_theme_ai_check theme=%s score=%.1f passed=%s",
                    theme_id, total, passed)
        return result

    # ============================================================
    # 激活(AI 把关 + 审计)
    # ============================================================

    async def activate_theme(self, admin_id: int, theme_id: int) -> dict:
        """激活主题(C 端运行时生效)

        AI 健康度 < 60 拒绝激活; 激活后原 active 主题转为 archived。

        Raises:
            KeyError: 主题不存在
            ValueError: AI 评估未通过
        """
        async with get_lock("theme:activate"):
            theme = await self.repo.get_theme(theme_id)
            if not theme:
                raise KeyError(f"主题 {theme_id} 不存在")
            if theme.get("status") == "active":
                return {"success": True, "themeId": theme_id,
                        "note": "该主题已是激活状态", "theme": theme}

            # AI 健康度把关(未达标拒绝)
            check = await self.ai_check(theme_id)
            if not check["passed"]:
                worst = min(check["factors"], key=lambda f: f["score"])
                raise ValueError(
                    f"AI 健康度评分 {check['score']} 未达 "
                    f"{self.AI_SCORE_THRESHOLD}, 最弱因子「{worst['label']}」"
                    f"仅 {worst['score']}/{worst['maxScore']}, 请修正后再激活")

            before = dict(theme)
            # 原 active 主题转为 archived
            current_active = await self.repo.get_active_theme()
            if current_active and \
                    current_active["themeId"] != theme_id:
                await self.repo.update_theme(
                    current_active["themeId"], {"status": "archived"})
                await self._log(current_active["themeId"], admin_id,
                                "deactivate", current_active,
                                {**current_active, "status": "archived"})
            # 激活新主题
            now = self._now()
            await self.repo.update_theme(theme_id, {
                "status": "active", "activatedAt": now})
            theme["status"] = "active"
            theme["activatedAt"] = now
            await self._log(theme_id, admin_id, "activate", before, theme)
            logger.info("site_theme_activated theme=%s by=%s",
                        theme_id, admin_id)
            return {
                "success": True,
                "themeId": theme_id,
                "theme": theme,
                "aiScore": check["score"],
                "note": "主题已激活, C 端小程序即时生效(导航栏/tabBar)",
            }

    # ============================================================
    # 审计日志 + 一键回滚
    # ============================================================

    async def list_logs(self, theme_id: int = None,
                        limit: int = 50) -> list[dict]:
        """变更审计日志列表"""
        return await self.repo.list_logs(theme_id=theme_id, limit=limit)

    async def rollback(self, admin_id: int, log_id: int) -> dict:
        """一键回滚到指定变更点(恢复 before 快照)

        回滚本身也落审计日志(可再回滚)。

        Raises:
            KeyError: 日志不存在
            ValueError: 该日志无 before 快照
        """
        async with get_lock("theme:activate"):
            log = await self.repo.get_log(log_id)
            if not log:
                raise KeyError(f"变更日志 {log_id} 不存在")
            before = log.get("beforeSnapshot")
            if not before:
                raise ValueError("该日志为创建操作, 无回滚前快照")
            theme_id = log.get("themeId")
            current = await self.repo.get_theme(theme_id)
            if not current:
                raise KeyError(f"主题 {theme_id} 不存在")
            # 若回滚的是激活操作, 需恢复前一个 active 主题
            if log.get("action") == "activate":
                await self.repo.update_theme(theme_id, {
                    "status": before.get("status", "draft"),
                    "activatedAt": before.get("activatedAt", "")})
            else:
                await self.repo.update_theme(theme_id, {
                    "name": before.get("name", current["name"]),
                    "description": before.get("description", ""),
                    "colors": before.get("colors", current["colors"]),
                    "icons": before.get("icons", current["icons"])})
            restored = await self.repo.get_theme(theme_id)
            await self._log(theme_id, admin_id, "rollback",
                            current, restored,
                            note=f"回滚自日志#{log_id}")
            logger.info("site_theme_rollback theme=%s from_log=%s by=%s",
                        theme_id, log_id, admin_id)
            return {"success": True, "themeId": theme_id,
                    "rolledBackFrom": log_id, "theme": restored}

    # ============================================================
    # AI 季节智能推荐
    # ============================================================

    async def recommend(self, month: int = None) -> dict:
        """AI 季节智能推荐(节日匹配 + 季节匹配 + 品牌基因保持度)"""
        now = datetime.now(UTC)
        m = month or now.month
        # 季节判定(北半球)
        season = ("winter" if m in (12, 1, 2) else
                  "spring" if m in (3, 4, 5) else
                  "summer" if m in (6, 7, 8) else "autumn")
        # 节日匹配
        festival = None
        if m == 1 or m == 2:
            festival = "spring_festival"   # 春节(1-2月)
        elif m == 9 or m == 10:
            festival = "autumn_festival"   # 中秋/国庆(9-10月)
        if m == 10:
            festival = "national_day"

        results = []
        for preset in PRESET_THEMES:
            score = 0.0
            reasons = []
            # 1. 节日匹配(权重 40)
            if festival and preset["season"] == festival:
                score += 40
                reasons.append(f"匹配当前节日档期({preset['name']})")
            # 2. 季节匹配(权重 30)
            if preset["season"] == season:
                score += 30
                reasons.append(f"匹配当前季节({season})")
            elif preset["season"] == "all":
                score += 20
                reasons.append("全年通用方案")
            # 3. 品牌基因保持度(权重 30)
            score += preset["brandFit"] * 30
            if preset["brandFit"] >= 0.9:
                reasons.append("竹绿品牌基因高度保持")
            if not reasons:
                reasons.append("备用方案(当前非适用档期)")
            results.append({
                "name": preset["name"],
                "season": preset["season"],
                "colors": preset["colors"],
                "recommendScore": round(score, 1),
                "reasons": reasons,
            })
        results.sort(key=lambda x: -x["recommendScore"])
        return {
            "success": True,
            "month": m,
            "season": season,
            "festival": festival,
            "recommendations": results[:3],
            "best": results[0],
            "modelVersion": MODEL_VERSION,
        }

    # ============================================================
    # 公开端(C 端运行时拉取)
    # ============================================================

    async def get_active_theme(self) -> dict:
        """当前激活主题(C 端导航栏/tabBar/图标运行时应用)"""
        theme = await self.repo.get_active_theme()
        if not theme:
            # 兜底: 默认竹绿经典
            from repositories.site_theme_repository import DEFAULT_THEME
            theme = dict(DEFAULT_THEME)
        return {
            "success": True,
            "themeId": theme.get("themeId"),
            "name": theme.get("name", ""),
            "colors": theme.get("colors") or {},
            "icons": theme.get("icons") or {},
        }

    async def list_icons(self, category: str = None) -> list[dict]:
        """图标资源库(公开只读)"""
        return await self.repo.list_icons(category=category)

    async def create_icon(self, admin_id: int, emoji: str = None,
                          image: str = None, name: str = "") -> dict:
        """新增图标到资源库(emoji 或上传图片 data URL)

        Raises:
            ValueError: 参数缺失/格式非法/超限
        """
        if not emoji and not image:
            raise ValueError("须提供 emoji 或 image 二者之一")
        if emoji and image:
            raise ValueError("emoji 与 image 不能同时提供")
        icon_id = await self.repo.next_icon_id()
        if emoji:
            icon = {
                "iconId": icon_id,
                "name": (name or f"emoji_{icon_id}").strip()[:30],
                "emoji": emoji.strip()[:8],
                "url": "",
                "category": "grid",
                "createdBy": admin_id,
            }
        else:
            img = (image or "").strip()
            # data URL 校验: data:image/png|jpeg|jpg|webp|gif;base64,xxx
            if not img.startswith("data:image/"):
                raise ValueError("图片须为 data:image/* base64 格式")
            mime = img[5:img.index(";")] if ";" in img else ""
            if mime.split("/")[-1] not in ("png", "jpeg", "jpg", "webp", "gif"):
                raise ValueError("仅支持 png/jpeg/webp/gif 图片")
            if len(img) > 300_000:
                raise ValueError("图片过大(须 ≤200KB)")
            icon = {
                "iconId": icon_id,
                "name": (name or f"upload_{icon_id}").strip()[:30],
                "emoji": "",
                "url": img,
                "category": "grid",
                "createdBy": admin_id,
            }
        await self.repo.save_icon(icon)
        logger.info("site_theme_icon_created iconId=%s by=%s type=%s",
                    icon_id, admin_id, "emoji" if emoji else "image")
        return icon

    # ============================================================
    # 内部辅助
    # ============================================================

    async def _log(self, theme_id: int, admin_id: int, action: str,
                   before: dict | None, after: dict | None,
                   note: str = ""):
        """落审计日志(before/after 快照)"""
        log_id = await self.repo.next_log_id()
        await self.repo.save_log({
            "logId": log_id,
            "themeId": theme_id,
            "adminId": admin_id,
            "action": action,
            "note": note,
            "beforeSnapshot": self._snapshot(before),
            "afterSnapshot": self._snapshot(after),
            "createdAt": self._now(),
        })

    @staticmethod
    def _snapshot(theme: dict | None) -> dict:
        """变更快照(保留核心字段, 防日志膨胀)"""
        if not theme:
            return {}
        return {
            "themeId": theme.get("themeId"),
            "name": theme.get("name"),
            "status": theme.get("status"),
            "colors": theme.get("colors") or {},
            "icons": theme.get("icons") or {},
            "aiScoreLatest": theme.get("aiScoreLatest", 0),
        }

    def _validate_theme(self, name: str, colors: dict):
        """主题字段校验

        Raises:
            ValueError: 名称/配色非法
        """
        if not name or not name.strip():
            raise ValueError("主题名称不能为空")
        if not colors:
            raise ValueError("主题配色不能为空")
        missing = [k for k in REQUIRED_COLORS if not colors.get(k)]
        if missing:
            raise ValueError(f"缺少必填颜色字段: {', '.join(missing)}")
        for key in REQUIRED_COLORS:
            hexv = str(colors.get(key, ""))
            if not (hexv.startswith("#") and len(hexv) == 7
                    and self._is_hex(hexv[1:])):
                raise ValueError(f"颜色字段 {key} 须为 #RRGGBB 格式, "
                                 f"当前: {hexv}")

    @staticmethod
    def _is_hex(s: str) -> bool:
        try:
            int(s, 16)
            return True
        except ValueError:
            return False

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
        """#RRGGBB → (r, g, b)"""
        h = hex_color.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    @classmethod
    def _hex_to_hsl(cls, hex_color: str) -> tuple[float, float, float]:
        """#RRGGBB → (h∈[0,360), s∈[0,100], l∈[0,100])"""
        r, g, b = cls._hex_to_rgb(hex_color)
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        return (h * 360, s * 100, l * 100)

    @classmethod
    def _relative_luminance(cls, hex_color: str) -> float:
        """WCAG 相对亮度"""
        r, g, b = cls._hex_to_rgb(hex_color)

        def lin(c: float) -> float:
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        return (lin(r / 255) * 0.2126 + lin(g / 255) * 0.7152
                + lin(b / 255) * 0.0722)

    @classmethod
    def _contrast_ratio(cls, hex1: str, hex2: str) -> float:
        """WCAG 对比度(1.0-21.0)"""
        l1 = cls._relative_luminance(hex1)
        l2 = cls._relative_luminance(hex2)
        lighter, darker = max(l1, l2), min(l1, l2)
        return (lighter + 0.05) / (darker + 0.05)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
