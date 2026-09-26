"""36号·小红书笔记封面生产(确定性品牌卡片·Pillow)

背景(2026-09-26 立项): 图文笔记强制要求至少 1 张图——RPA
发布依赖页面"文字配图"按钮曾需人工干预, 不可持续。本服务
在发布前确定性渲染竹绿主题品牌卡片(零外部 API/秒级/风格
固定可审), RPA 下载后自动上传, 全链无人干预。

设计口径(与站点主题一致):
    - 尺寸 1080×1440(小红书图文 3:4)
    - 竹绿 #355c44 × #4a7c59 + 金 #c9a961 + 米白底 #f6f4ee
    - 标题与发布版一致(小红书 20 字限口径), 超长换行
    - 底部健康警示 + "详情见主页"(合规尾巴)

字体: Noto Sans SC(OFL 开源, assets/fonts/ 随镜像分发)。
"""
import logging
import re
from pathlib import Path

from core.helpers import ts

logger = logging.getLogger(__name__)

# 封面渲染剥除 emoji(Noto Sans 无彩色 emoji 字形, 渲染为
# 方框——正文原样发布不受影响, 仅卡片净化)
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF]")

# 品牌色板(对齐站点主题)
C_BG = (246, 244, 238)        # 米白
C_GREEN_DARK = (53, 92, 68)   # 竹绿深
C_GREEN = (74, 124, 89)      # 竹绿
C_GOLD = (201, 169, 97)       # 金
C_TEXT = (44, 46, 51)         # 深灰正文
C_MUTED = (138, 151, 143)     # 浅灰辅助

COVER_DIR = Path("/tmp/promo_covers")
W, H = 1080, 1440

_FONT_DIR = (Path(__file__).resolve().parent.parent
             / "assets" / "fonts")


def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    name = ("NotoSansCJKsc-Bold.otf" if bold
            else "NotoSansCJKsc-Regular.otf")
    path = _FONT_DIR / name
    if path.exists():
        return ImageFont.truetype(str(path), size)
    # 字体缺失回退默认(英文/数字可渲染, 中文可能豆腐块
    # ——fail-soft: 封面仍产出, 日志留痕)
    logger.warning("promo_cover_font_missing: %s", path)
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    """中文逐字累加换行(无空格分词语言)"""
    lines, line = [], ""
    for ch in str(text or ""):
        if draw.textlength(line + ch, font=font) > max_width \
                and line:
            lines.append(line)
            line = ch
        else:
            line += ch
    if line:
        lines.append(line)
    return lines


class PromoCoverService:
    """笔记封面品牌卡片渲染(确定性)"""

    def cover_path(self, content_id: int) -> Path:
        COVER_DIR.mkdir(parents=True, exist_ok=True)
        return COVER_DIR / f"cover_{content_id}.png"

    def render(self, content: dict) -> Path:
        """渲染封面卡片, 返回 PNG 路径(同 id 覆盖)"""
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (W, H), C_BG)
        draw = ImageDraw.Draw(img)

        # 顶部竹绿渐变带(纯色块近似——渐变逐行成本高,
        # 双色带即可传达品牌感)
        draw.rectangle([0, 0, W, 240], fill=C_GREEN_DARK)
        draw.rectangle([0, 240, W, 256], fill=C_GOLD)

        # 品牌行
        brand = _font(84)
        draw.text((72, 76), "竹香酒",
                  font=brand, fill=(255, 255, 255))
        tag = _font(34, bold=False)
        draw.text((340, 148), "ZXJIU · 竹香型白酒官方",
                  font=tag, fill=(230, 207, 159))

        # 标题(与发布版一致截断 20 字口径, 自动换行)
        title = str(content.get("title") or "")[:20] \
            or "竹香酒借势笔记"
        title_font = _font(72)
        y = 360
        for ln in _wrap(draw, title, title_font, W - 144)[:3]:
            draw.text((72, y), ln, font=title_font,
                      fill=C_GREEN_DARK)
            y += 100

        # 金色分隔
        y += 24
        draw.rectangle([72, y, W - 72, y + 6], fill=C_GOLD)
        y += 56

        # 要点句(正文前 60 字, 剥除 emoji, 弱化为副文案;
        # 截断加省略号防突兀)
        body_raw = _EMOJI_RE.sub(
            "", str(content.get("body") or "")
            .replace("\n", " ")).strip()
        body = body_raw[:60]
        if len(body_raw) > 60:
            body += "…"
        sub_font = _font(40, bold=False)
        for ln in _wrap(draw, body, sub_font, W - 144)[:4]:
            draw.text((72, y), ln, font=sub_font, fill=C_TEXT)
            y += 62

        # 话题(金棕)
        y = max(y + 40, 1100)
        hash_font = _font(38)
        draw.text((72, y), str(content.get("hashtags")
                               or "#竹香型白酒"),
                  font=hash_font, fill=C_GOLD)

        # 底部合规条
        foot_font = _font(30, bold=False)
        draw.text(
            (72, H - 120),
            "过量饮酒有害健康 · 未成年人禁止饮酒 · 理性饮酒",
            font=foot_font, fill=C_MUTED)
        draw.text((72, H - 72), "点击主页链接了解详情",
                  font=foot_font, fill=C_MUTED)

        path = self.cover_path(int(content.get("contentId") or 0))
        img.save(path, "PNG")
        logger.info("promo_cover_rendered content=%s -> %s",
                    content.get("contentId"), path)
        return path

    def ensure_cover(self, content: dict) -> Path:
        """按需渲染+缓存(文件在则直接复用)"""
        path = self.cover_path(
            int(content.get("contentId") or 0))
        if not path.exists():
            return self.render(content)
        return path
