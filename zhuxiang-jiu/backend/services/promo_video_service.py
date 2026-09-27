"""36号·视频号生产线 MVP(多页品牌卡→ffmpeg 轮播视频)

2026-09-27 立项: 视频号(wechat_channels)无官方视频发布 API, 走
channels.weixin.qq.com 网页版 RPA——上游内容形态需为视频。本服务
复用封面卡生产线的品牌资产(竹绿/金/米白色板 + Noto 字体 + IP
角标, promo_cover_service 同源), 按「封面→卖点→行动→合规」四页
渲染 1080×1440 竖版卡片, 经本机 ffmpeg(xfade 淡入淡出)合成
约 16s 无声轮播视频(视频号与抖音通用竖版形态)。

产线定位: 渲染+合成在**开发机**跑(Pillow+ffmpeg 本机依赖, 与
生成/审核解耦——36 号发布矩阵以 wechat_channels 入 RPA_PLATFORMS,
出队挂 rpa_pending, 触发式发布同小红书/抖音/微博范式)。
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path

from services.promo_cover_service import (
    C_BG, C_GREEN_DARK, C_GREEN, C_GOLD, C_TEXT, C_MUTED,
    _EMOJI_RE, _font, _wrap,
)

logger = logging.getLogger(__name__)

PAGE_W, PAGE_H = 1080, 1440
VIDEO_DIR = Path(os.environ.get(
    "PROMO_VIDEO_DIR",
    str(Path(__file__).resolve().parent.parent / "promo_videos")))
PER_PAGE_SECONDS = 4.5
FADE_SECONDS = 0.6

_IP_ASSET = (Path(__file__).resolve().parent.parent
             / "assets" / "ip" / "ip-square.png")


def _ffmpeg_bin() -> str:
    """ffmpeg 定位: FFMPEG_PATH env → PATH → 工作区便携版"""
    env = os.environ.get("FFMPEG_PATH", "").strip()
    if env and Path(env).exists():
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    portable = Path(r"d:\网站架构设计\ffmpeg\ffmpeg.exe")
    if portable.exists():
        return str(portable)
    raise RuntimeError("ffmpeg 不可用: 配置 FFMPEG_PATH 或安装 ffmpeg")


def _clean(text) -> str:
    """页面文案净化: 剥 emoji(渲染无彩色字形)+折叠换行"""
    return _EMOJI_RE.sub(
        "", str(text or "").replace("\n", " ")).strip()


class PromoVideoService:
    """四页品牌卡渲染 + ffmpeg 轮播合成(1080×1440, ~16s 无声)"""

    # ---------- 页面共用元素(与封面卡同风格) ----------

    def _new_page(self, img, draw):
        draw.rectangle([0, 0, PAGE_W, 240], fill=C_GREEN_DARK)
        draw.rectangle([0, 240, PAGE_W, 256], fill=C_GOLD)
        brand = _font(84)
        draw.text((72, 60), "竹香酒", font=brand,
                  fill=(255, 255, 255))
        tag = _font(34, bold=False)
        draw.text((340, 130), "ZXJIU · 竹香型白酒官方",
                  font=tag, fill=(230, 207, 159))
        draw.rectangle([0, PAGE_H - 8, PAGE_W, PAGE_H], fill=C_GOLD)
        return img, draw

    def _ip_badge(self, img, size=200):
        from PIL import Image
        if _IP_ASSET.exists():
            badge = (Image.open(_IP_ASSET).convert("RGBA")
                     .resize((size, size), Image.LANCZOS))
            img.paste(badge, (PAGE_W - 72 - size,
                              PAGE_H - 96 - size), badge)

    def _page_label(self, draw, text: str, y: int = 330):
        label = _font(40)
        draw.text((72, y), text, font=label, fill=C_GOLD)
        draw.rectangle([72, y + 62, PAGE_W - 72, y + 66],
                       fill=C_GREEN)

    # ---------- 四页渲染 ----------

    def _page_cover(self, content: dict):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (PAGE_W, PAGE_H), C_BG)
        draw = ImageDraw.Draw(img)
        self._new_page(img, draw)
        title = (str(content.get("title") or "")
                 .split("｜")[0].strip())[:20] or "竹香酒"
        title_font = _font(76)
        y = 430
        for ln in _wrap(draw, title, title_font,
                        PAGE_W - 144)[:3]:
            draw.text((72, y), ln, font=title_font, fill=C_GREEN_DARK)
            y += 106
        y += 24
        draw.rectangle([72, y, PAGE_W - 72, y + 6], fill=C_GOLD)
        hash_font = _font(40)
        draw.text((72, 1150),
                  _clean(content.get("hashtags")) or "#竹香型白酒",
                  font=hash_font, fill=C_GOLD)
        sub = _font(34, bold=False)
        draw.text((72, 1230), "滑动了解竹香酒",
                  font=sub, fill=C_MUTED)
        self._ip_badge(img)
        return img

    def _page_selling(self, content: dict):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (PAGE_W, PAGE_H), C_BG)
        draw = ImageDraw.Draw(img)
        self._new_page(img, draw)
        self._page_label(draw, "产品卖点")
        body = _clean(content.get("body"))[:100] or "竹香型白酒"
        body_font = _font(46, bold=False)
        y = 470
        for ln in _wrap(draw, body, body_font,
                        PAGE_W - 144)[:6]:
            draw.text((72, y), ln, font=body_font, fill=C_TEXT)
            y += 82
        self._ip_badge(img)
        return img

    def _page_action(self, content: dict):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (PAGE_W, PAGE_H), C_BG)
        draw = ImageDraw.Draw(img)
        self._new_page(img, draw)
        self._page_label(draw, "立即行动")
        cta_font = _font(72)
        draw.text((72, 520), "点击主页链接", font=cta_font,
                  fill=C_GREEN_DARK)
        draw.text((72, 640), "了解详情", font=cta_font,
                  fill=C_GREEN_DARK)
        btn_font = _font(40)
        draw.rounded_rectangle(
            [72, 800, PAGE_W - 72, 900], radius=50,
            fill=C_GREEN_DARK)
        draw.text((270, 830), "新客立减 · 详情见主页",
                  font=btn_font, fill=(255, 255, 255))
        from PIL import Image
        if _IP_ASSET.exists():
            badge = (Image.open(_IP_ASSET).convert("RGBA")
                     .resize((320, 320), Image.LANCZOS))
            img.paste(badge, (PAGE_W // 2 - 160, 1010), badge)
        return img

    def _page_compliance(self, content: dict):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (PAGE_W, PAGE_H), C_BG)
        draw = ImageDraw.Draw(img)
        self._new_page(img, draw)
        self._page_label(draw, "健康提示")
        lines = ("理性饮酒\n"
                 "未成年人禁止饮酒\n"
                 "过量饮酒有害健康")
        warn_font = _font(56)
        y = 520
        for ln in lines.split("\n"):
            draw.text((PAGE_W // 2 - len(ln) * 56 // 2, y),
                      ln, font=warn_font, fill=C_TEXT)
            y += 120
        brand_font = _font(40, bold=False)
        draw.text((72, 1160), "© 竹香酒 zxjiu.com · 竹香型白酒",
                  font=brand_font, fill=C_MUTED)
        self._ip_badge(img)
        return img

    # ---------- 渲染与合成 ----------

    def render_pages(self, content: dict) -> list[Path]:
        """渲染四页 PNG(封面→卖点→行动→合规), 返回路径列表"""
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        cid = int(content.get("contentId") or 0)
        pages = []
        for i, render in enumerate((
                self._page_cover, self._page_selling,
                self._page_action, self._page_compliance), 1):
            img = render(content)
            path = VIDEO_DIR / f"video_{cid}_p{i}.png"
            img.save(path, "PNG")
            pages.append(path)
        logger.info("promo_video_pages_rendered content=%s -> %s页",
                    cid, len(pages))
        return pages

    def compose(self, page_paths: list, out_mp4: Path,
                per_page: float = PER_PAGE_SECONDS,
                fade: float = FADE_SECONDS) -> Path:
        """ffmpeg xfade 淡入淡出合成轮播视频

        总时长 = n*per_page - (n-1)*fade(默认 4 页 16.2s)
        """
        if not page_paths:
            raise ValueError("无页面可合成")
        out_mp4 = Path(out_mp4)
        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        args = [_ffmpeg_bin(), "-y"]
        for p in page_paths:
            args += ["-loop", "1", "-t", str(per_page),
                     "-i", str(p)]
        parts = []
        prev = "[0]"
        offset = per_page - fade
        for i in range(1, len(page_paths)):
            out = f"[v{i}]"
            parts.append(
                f"{prev}[{i}]xfade=transition=fade:"
                f"duration={fade}:offset={offset:.2f}{out}")
            prev = out
            offset += per_page - fade
        parts.append(f"{prev}format=yuv420p")
        fc = ";".join(parts)
        args += ["-filter_complex", fc,
                 "-c:v", "libx264", "-r", "30",
                 "-movflags", "+faststart", str(out_mp4)]
        result = subprocess.run(
            args, capture_output=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 合成失败(exit={result.returncode}): "
                f"{result.stderr.decode('utf-8', 'replace')[-400:]}")
        logger.info("promo_video_composed -> %s (%s bytes)",
                    out_mp4, out_mp4.stat().st_size)
        return out_mp4

    def build(self, content: dict,
              per_page: float = PER_PAGE_SECONDS,
              fade: float = FADE_SECONDS) -> dict:
        """内容 → 四页卡 + 轮播视频(产线入口)"""
        pages = self.render_pages(content)
        cid = int(content.get("contentId") or 0)
        out = VIDEO_DIR / f"promo_video_{cid}.mp4"
        self.compose(pages, out, per_page=per_page, fade=fade)
        n = len(pages)
        return {
            "contentId": cid,
            "pages": [str(p) for p in pages],
            "video": str(out),
            "sizeBytes": out.stat().st_size,
            "durationSeconds": round(
                n * per_page - (n - 1) * fade, 1),
        }
