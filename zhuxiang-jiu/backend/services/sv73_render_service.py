"""73号(sv)·短视频智能模型 P0——分镜渲染引擎 v1.0

职责: storyboard JSON → 大字卡 PNG×4 → ffmpeg 合成(运镜/过渡/
      TTS 可选配音轨) → mp4
资产: 36号视频号生产线只读复用(色板/字体/IP 角标/品牌头/ffmpeg
      定位)——叠加式零改动, 永不修改源模块

对规划稿的落地优化(参照文档并优化):
    - 字幕动画: 规划稿 drawtext alpha 渐入 → 落地为 Pillow 预排
      (主字幕描边+高亮词金色, 中文排版与 36号封面同源实证) +
      zoompan 缓推运镜 + xfade 页间渐变——规避 drawtext 中文
      转义/跨平台 fontfile 路径风险, 动画语义等价(运镜+渐入)
    - TTS 配音轨: 48号小竹语音 synthesize(WAV)→ffmpeg mux,
      SV73_TTS_MODE 默认 off(铁律); 失败 fail-soft 无声
      (36号无声轮播同态, 产出不中断)

铁律: LLM 禁入判定链(TTS 文本=storyboard 注册表约束后的
      voiceover, 渲染层零自由文本); 叠加式零改动; 开关默认 off

变更链:
    - 2026-09-30 P0 立项(docs/73号_短视频智能模型_创新规划方案.md)
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from services.promo_cover_service import (
    C_BG, C_GREEN_DARK, C_GREEN, C_GOLD, C_TEXT, C_MUTED,
    _font, _wrap,
)
from services.promo_video_service import (
    PAGE_W, PAGE_H, _IP_ASSET, _ffmpeg_bin, _clean,
    PromoVideoService,
)
from services.sv73_script_service import IP_PERSONAS, SCENE_FADE

logger = logging.getLogger(__name__)

# 渲染参数注册表(数字禁区——不消费 storyboard 外的任何数字)
ZOOM_MAX = 1.08          # zoompan 缓推上限(Ken Burns)
INPUT_FPS = 30           # 输入帧率(显式 -r, 消除 ffmpeg 默认差异)
TTS_MODE_DEFAULT = "off"

# 产物目录(仿 36号 VIDEO_DIR 惯例)
SV73_VIDEO_DIR = Path(os.environ.get(
    "SV73_VIDEO_DIR",
    str(Path(__file__).resolve().parent.parent / "sv73_videos")))

# 镜头角色中文标签(渲染层固定, 与 36号四页口径同源)
ROLE_LABELS = {
    "cover": "热点开场",
    "selling": "产品卖点",
    "proof": "信任背书",
    "action": "立即行动",
    "compliance": "健康提示",
}


def tts_mode() -> bool:
    """SV73_TTS_MODE(默认 off——铁律)"""
    return os.environ.get("SV73_TTS_MODE", TTS_MODE_DEFAULT
    ).strip().lower() in ("on", "1", "true")


class Sv73RenderService:
    """storyboard → 大字卡 PNG → ffmpeg 运镜合成(+TTS 可选轨)

    36号生产线资产只读复用: 品牌头/_ip_badge/色板/字体经
    PromoVideoService 无状态实例调用(零侵入实证)。
    """

    def __init__(self):
        self._base = PromoVideoService()   # 36号资产载体(无状态)

    # ---------- 页面渲染(大字卡) ----------

    def _render_scene(self, sc: dict):
        """单镜 → 1080×1440 PNG 页(品牌头+大字+高亮金+IP 角标)"""
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (PAGE_W, PAGE_H), C_BG)
        draw = ImageDraw.Draw(img)
        self._base._new_page(img, draw)
        self._base._page_label(draw, ROLE_LABELS.get(
            sc["role"], "竹香酒"))

        role = sc["role"]
        if role == "compliance":
            # 合规尾镜: 三行警示居中(36号 _page_compliance 同款)
            warn_font = _font(56)
            lines = ("理性饮酒", "未成年人禁止饮酒", "过量饮酒有害健康")
            y = 520
            for ln in lines:
                draw.text((PAGE_W // 2 - len(ln) * 56 // 2, y),
                          ln, font=warn_font, fill=C_TEXT)
                y += 120
        else:
            # 主字幕大字(≤12 字): 高亮词金色/其余墨绿, 逐字配色
            text = _clean(sc["text"]) or "竹香酒"
            highlight = "".join(sc.get("highlightWords") or [])
            big_font = _font(88)
            x, y = 72, 520
            for ch in text[:12]:
                fill = C_GOLD if (highlight and ch in highlight
                                  ) else C_GREEN_DARK
                draw.text((x, y), ch, font=big_font, fill=fill)
                x += 88
                if x > PAGE_W - 88 - 72:   # 单行 11 字换行
                    x, y = 72, y + 118
        if role == "action":
            # 行动页: CTA 按钮同款(36号 _page_action)
            btn_font = _font(40)
            draw.rounded_rectangle(
                [72, 800, PAGE_W - 72, 900], radius=50,
                fill=C_GREEN_DARK)
            draw.text((270, 830), "新客立减 · 详情见主页",
                      font=btn_font, fill=(255, 255, 255))

        # 配音词小字(底部提示, 与画面字幕分层):
        # - y=1100 避开 IP 角标带(y 1144-1344); 宽度限左区
        #   718px(x 72-790)防压右下角标
        # - compliance(与三行警示重复)/action(与 CTA 按钮重复)跳过
        if role not in ("compliance", "action"):
            vo_font = _font(34, bold=False)
            vo = _clean(sc.get("voiceover"))[:26]
            for ln in _wrap(draw, vo, vo_font, 718)[:1]:
                draw.text((72, 1100), ln, font=vo_font, fill=C_MUTED)

        # IP 角标: storyboard ipOverlay 驱动(锚点注册表口径)
        overlay = sc.get("ipOverlay") or {}
        self._paste_ip(img, overlay.get("anchor", "bottom-right"),
                       int(overlay.get("size", 200)))
        return img

    def _paste_ip(self, img, anchor: str, size: int):
        """IP 角标贴图(bottom-right 复用 36号 _ip_badge 口径;
        bottom-center 对齐 36号行动页手动口径)"""
        from PIL import Image
        if not _IP_ASSET.exists():
            return
        badge = (Image.open(_IP_ASSET).convert("RGBA")
                 .resize((size, size), Image.LANCZOS))
        if anchor == "bottom-center":
            img.paste(badge, (PAGE_W // 2 - size // 2, 1010), badge)
        else:
            img.paste(badge, (PAGE_W - 72 - size,
                              PAGE_H - 96 - size), badge)

    def render_pages(self, storyboard: dict) -> list[Path]:
        """storyboard.scenes → 4 页 PNG(封面→卖点→行动→合规)"""
        SV73_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        sid = storyboard["scriptId"]
        pages = []
        for i, sc in enumerate(storyboard["scenes"], 1):
            img = self._render_scene(sc)
            path = SV73_VIDEO_DIR / f"{sid}_p{i}.png"
            img.save(path, "PNG")
            pages.append(path)
        logger.info("sv73_pages_rendered script=%s -> %s 页",
                    sid, len(pages))
        return pages

    # ---------- ffmpeg 合成(zoompan 运镜 + xfade) ----------

    def compose(self, page_paths: list, storyboard: dict,
                out_mp4: Path) -> Path:
        """ffmpeg 合成: 每镜 zoompan 缓推(1.0→1.08)+xfade 渐变

        帧率显式 30fps 输入(消除默认帧率差异, zoompan 帧数确定)
        总时长 = n*duration - (n-1)*fade(storyboard 注册表口径)
        """
        if not page_paths:
            raise ValueError("无页面可合成")
        duration = storyboard["scenes"][0]["duration"]
        fade = SCENE_FADE   # 注册表单一来源(totalDuration 公式同源)
        out_mp4 = Path(out_mp4)
        out_mp4.parent.mkdir(parents=True, exist_ok=True)

        args = [_ffmpeg_bin(), "-y"]
        frames = int(duration * INPUT_FPS)   # 每镜帧数(确定性)
        for p in page_paths:
            args += ["-loop", "1", "-t", str(duration),
                     "-r", str(INPUT_FPS), "-i", str(p)]

        parts = []
        for i in range(len(page_paths)):
            # zoompan 缓推: on∈[0, frames-1] → z∈[1.0, 1.08]
            # x/y 居中公式: zoom 放大中心不变(Ken Burns)
            parts.append(
                f"[{i}:v]zoompan="
                f"z='1+{ZOOM_MAX - 1:.2f}*on/{frames - 1}'"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d=1:s={PAGE_W}x{PAGE_H}:fps={INPUT_FPS}[z{i}]")
        prev = "[z0]"
        offset = duration - fade
        for i in range(1, len(page_paths)):
            out = f"[v{i}]"
            parts.append(
                f"{prev}[z{i}]xfade=transition=fade:"
                f"duration={fade}:offset={offset:.2f}{out}")
            prev = out
            offset += duration - fade
        parts.append(f"{prev}format=yuv420p")
        fc = ";".join(parts)
        args += ["-filter_complex", fc,
                 "-c:v", "libx264", "-r", str(INPUT_FPS),
                 "-movflags", "+faststart", str(out_mp4)]
        result = subprocess.run(args, capture_output=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 合成失败(exit={result.returncode}): "
                f"{result.stderr.decode('utf-8', 'replace')[-400:]}")
        logger.info("sv73_video_composed -> %s (%s bytes)",
                    out_mp4, out_mp4.stat().st_size)
        return out_mp4

    # ---------- TTS 配音轨(可选, 48号小竹语音) ----------

    def _tts_audio(self, storyboard: dict) -> Path | None:
        """全量 voiceover → WAV(SV73_TTS_MODE=on 时;
        失败 fail-soft 返回 None——无声同 36号)"""
        from services.llm_client import provider_client
        text = "。".join(sc["voiceover"]
                         for sc in storyboard["scenes"])
        try:
            wav = provider_client.synthesize(
                text, voice=IP_VOICE.get(storyboard["persona"]))
        except Exception as exc:
            logger.warning("sv73_tts_failed: %s", exc)
            return None
        if not wav:
            return None
        path = SV73_VIDEO_DIR / f"{storyboard['scriptId']}_tts.wav"
        path.write_bytes(wav)
        return path

    def _mux_audio(self, video: Path, audio: Path, out_mp4: Path,
                   duration: float) -> Path:
        """配音混流(视频流直拷, 音频 aac, 显式 -t 视频时长截齐)

        -shortest 的坑: 音频短于视频时视频流被切尾(丢画面)——
        以 storyboard 注册表时长为准截断, 视频流完整性优先
        """
        args = [_ffmpeg_bin(), "-y",
                "-i", str(video), "-i", str(audio),
                "-c:v", "copy", "-c:a", "aac",
                "-t", f"{duration:.2f}",
                "-movflags", "+faststart", str(out_mp4)]
        result = subprocess.run(args, capture_output=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 混流失败(exit={result.returncode}): "
                f"{result.stderr.decode('utf-8', 'replace')[-400:]}")
        return out_mp4

    # ---------- 产线入口 ----------

    def build(self, storyboard: dict) -> dict:
        """storyboard → 页×4 + 运镜视频(+TTS 可选轨) → 产物清单"""
        sid = storyboard["scriptId"]
        pages = self.render_pages(storyboard)
        silent = SV73_VIDEO_DIR / f"{sid}.mp4"
        self.compose(pages, storyboard, silent)
        video, audio_track = silent, ""
        if tts_mode():
            audio = self._tts_audio(storyboard)
            if audio is not None:
                voiced = SV73_VIDEO_DIR / f"{sid}_voiced.mp4"
                try:
                    video = self._mux_audio(
                        silent, audio, voiced,
                        storyboard["totalDuration"])
                    audio_track = str(audio)
                except RuntimeError as exc:
                    logger.warning("sv73_tts_mux_failed: %s", exc)
                    video = silent   # fail-soft 回落无声
        return {
            "scriptId": sid,
            "pages": [str(p) for p in pages],
            "video": str(video),
            "audioTrack": audio_track,
            "sizeBytes": Path(video).stat().st_size,
            "durationSeconds": storyboard["totalDuration"],
        }


# 人设 → TTS 音色(48号已验证枚举; 人设注册表只读消费)
IP_VOICE = {k: v.get("voice") for k, v in IP_PERSONAS.items()}
