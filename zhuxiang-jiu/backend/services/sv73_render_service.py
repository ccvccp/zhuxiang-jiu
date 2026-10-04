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
    _IP_ASSET, _ffmpeg_bin, _clean, PromoVideoService,
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
    "dh_oral": "竹小妹口播",   # 数字人轨 P1: PNG 主题卡标签
    "dh_hook": "竹小妹口播",   # dh_mix 多镜口播钩子(A1)
}

# P1-2 BGM 情绪档 → TTS 语速(48号 joyvoice MOOD_SPEED 同值锚定:
# care 0.92 / steady 0.95 / cheerful 1.02——跨端常量互指)
BGM_MOOD_SPEED = {"warm": 0.92, "steady": 0.95, "cheerful": 1.02}

# cogtts 前导嘟嘟指纹(2026-10-02 实证: synthesize 返回 wav 前
# 1.78s 为固定 beep 脉冲序列——恒定振幅帧串(RMS≈1264)+孤立静音
# 间隔, 与文本/语速/开头词无关(b_noopen 变体逐帧一致); 数字人
# 出片以全段 wav 驱动口型 → 「嘴动无配音/嘟嘟后才出配音」缺陷
# 的根因。指纹法精确匹配(8 样本 mean_diff=0), 不匹配(智谱改
# 前导/正常音频)安全跳过零损伤。20ms 帧 RMS 序列(帧 0-88):
_BEEP_FP = (
    1252, 1264, 1264, 1264, 1264, 1264, 108, 0, 0, 0, 0, 0,
    0, 0, 878, 1264, 1264, 1264, 1264, 1264, 1264, 1264,
    1264, 1264, 1264, 1264, 1264, 1264, 1264, 1264, 1264,
    1264, 900, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 878,
    1264, 1264, 1264, 1264, 1264, 900, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 1252, 1264, 1264, 1264, 1264, 1264, 108,
)


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

    def _render_scene(self, sc: dict, tpl: dict):
        """单镜 → PNG 页(品牌头+大字+高亮金+IP 角标)

        布局全按 storyboard.template 尺寸参数化(P1 三模板);
        品牌头为 36号 _new_page 同款样式自绘(36号方法写死竖版
        常量, 横版由 73号 自绘——色板/字体资产仍同源复用)
        """
        from PIL import Image, ImageDraw
        w, h = tpl["pageW"], tpl["pageH"]
        img = Image.new("RGB", (w, h), C_BG)
        draw = ImageDraw.Draw(img)
        # 品牌头(36号 _new_page 同款: 竹绿带+金线+品牌字+底金条)
        draw.rectangle([0, 0, w, 240], fill=C_GREEN_DARK)
        draw.rectangle([0, 240, w, 256], fill=C_GOLD)
        draw.text((72, 60), "竹香酒", font=_font(84),
                  fill=(255, 255, 255))
        draw.text((340, 130), "ZXJIU · 竹香型白酒官方",
                  font=_font(34, bold=False),
                  fill=(230, 207, 159))
        draw.rectangle([0, h - 8, w, h], fill=C_GOLD)
        # 页角色标签(36号 _page_label 同款)
        draw.text((72, 330), ROLE_LABELS.get(sc["role"], "竹香酒"),
                  font=_font(40), fill=C_GOLD)
        draw.rectangle([72, 392, w - 72, 396], fill=C_GREEN)

        role = sc["role"]
        if role == "compliance":
            # 合规尾镜: 三行警示居中(36号 _page_compliance 同款)
            warn_font = _font(56)
            lines = ("理性饮酒", "未成年人禁止饮酒", "过量饮酒有害健康")
            y = int(h * 0.36)
            for ln in lines:
                draw.text((w // 2 - len(ln) * 56 // 2, y),
                          ln, font=warn_font, fill=C_TEXT)
                y += 120
        else:
            # 主字幕大字(≤12 字): 高亮词金色/其余墨绿, 逐字配色
            text = _clean(sc["text"]) or "竹香酒"
            highlight = "".join(sc.get("highlightWords") or [])
            big_font = _font(88)
            x, y = 72, int(h * 0.36)
            for ch in text[:12]:
                fill = C_GOLD if (highlight and ch in highlight
                                  ) else C_GREEN_DARK
                draw.text((x, y), ch, font=big_font, fill=fill)
                x += 88
                if x > w - 88 - 72:   # 行宽自适应(横版一行更多字)
                    x, y = 72, y + 118
        if role == "action":
            # 行动页: CTA 按钮同款(36号 _page_action)
            y_btn = int(h * 0.55)
            draw.rounded_rectangle(
                [72, y_btn, w - 72, y_btn + 100], radius=50,
                fill=C_GREEN_DARK)
            draw.text((270, y_btn + 30), "新客立减 · 详情见主页",
                      font=_font(40), fill=(255, 255, 255))

        # 配音词小字(底部提示):
        # - y/h≈0.76 避开 IP 角标带; 宽度限左区 2/3 防压右下角标
        # - compliance(与三行警示重复)/action(与 CTA 按钮重复)跳过
        if role not in ("compliance", "action"):
            vo_font = _font(34, bold=False)
            vo = _clean(sc.get("voiceover"))[:26]
            for ln in _wrap(draw, vo, vo_font, int(w * 0.66))[:1]:
                draw.text((72, int(h * 0.76)), ln,
                          font=vo_font, fill=C_MUTED)

        # IP 角标: storyboard ipOverlay 驱动(锚点注册表口径)
        overlay = sc.get("ipOverlay") or {}
        self._paste_ip(img, w, h,
                       overlay.get("anchor", "bottom-right"),
                       int(overlay.get("size", 200)))
        return img

    def _paste_ip(self, img, w: int, h: int, anchor: str, size: int):
        """IP 角标贴图(bottom-right 复用 36号 _ip_badge 口径;
        bottom-center 对齐 36号行动页手动口径)"""
        from PIL import Image
        if not _IP_ASSET.exists():
            return
        badge = (Image.open(_IP_ASSET).convert("RGBA")
                 .resize((size, size), Image.LANCZOS))
        if anchor == "bottom-center":
            img.paste(badge, (w // 2 - size // 2,
                              int(h * 0.70)), badge)
        else:
            img.paste(badge, (w - 72 - size, h - 96 - size), badge)

    def render_pages(self, storyboard: dict) -> list[Path]:
        """storyboard.scenes → PNG 页×N(封面→…→合规, 模板驱动)"""
        SV73_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        sid = storyboard["scriptId"]
        tpl = storyboard["template"]
        pages = []
        for i, sc in enumerate(storyboard["scenes"], 1):
            img = self._render_scene(sc, tpl)
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
        tpl = storyboard["template"]
        page_w, page_h = tpl["pageW"], tpl["pageH"]
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
                f":d=1:s={page_w}x{page_h}:fps={INPUT_FPS}[z{i}]")
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

    # ---------- dh_mix 合成(口播 GPU 镜 + 卡片 Ken Burns) ----------

    @staticmethod
    def mix_plan(storyboard: dict,
                 oral_duration: float | None = None) -> dict:
        """dh_mix xfade 编排参数(纯函数——注册表逐镜时长口径)

        oral_duration: 口播镜实际时长(ffprobe 物理真值, 缺省取
        注册表计划值)——SadTalker 出片时长由 TTS 音频物理决定,
        偏移须贴实际防 xfade 截断口播(计划值仅验收口径)。
        """
        durs = [float(sc["duration"])
                for sc in storyboard["scenes"]]
        if oral_duration and oral_duration > 0:
            durs[0] = float(oral_duration)
        fade = storyboard["template"]["fade"]
        offsets: list[float] = []
        total = durs[0]
        for d in durs[1:]:
            offsets.append(round(total - fade, 2))
            total += d - fade
        return {"durations": durs, "offsets": offsets,
                "fade": fade, "total": round(total, 2)}

    @staticmethod
    def _probe_duration(mp4: Path) -> float | None:
        """容器时长秒(ffprobe 优先; 便携版仅 ffmpeg.exe 时解析
        `ffmpeg -i` stderr 的 Duration 行兜底; 双失败返回 None
        ——回落注册表计划值)"""
        import re
        try:
            ff = Path(_ffmpeg_bin())
            probe = ff.with_name(
                ff.name.replace("ffmpeg", "ffprobe"))
            r = subprocess.run(
                [str(probe), "-v", "error", "-show_entries",
                 "format=duration", "-of",
                 "default=nw=1:nk=1", str(mp4)],
                capture_output=True, timeout=60)
            return float(r.stdout.decode().strip())
        except (ValueError, OSError, subprocess.TimeoutExpired):
            pass
        try:
            r = subprocess.run(
                [_ffmpeg_bin(), "-i", str(mp4)],
                capture_output=True, timeout=60)
            m = re.search(
                r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)",
                r.stderr.decode("utf-8", "replace"))
            if m:
                h, mnt, sec = m.groups()
                return int(h) * 3600 + int(mnt) * 60 + float(sec)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
        return None

    def compose_mix(self, oral_mp4: Path, card_pages: list,
                    storyboard: dict, out_mp4: Path) -> Path:
        """dh_mix 成片合成: 口播镜(SadTalker 实拍含音轨)+卡片镜
        (PNG 大字卡 zoompan 缓推) xfade 渐变链

        - 口播镜尺寸/编码由 dh_batch._h264ize 预规整(1080×1920
          avc1), 此处防御性再规整; 音轨=口播原声 apad 至总时长
          (卡片段静音——对齐零 GPU 轨无声卡片同态)
        - 卡片镜时长/过渡全注册表(sceneDurations 逐镜口径);
          口播偏移贴 ffprobe 实际时长
        """
        tpl = storyboard["template"]
        if tpl["name"] != "dh_mix":
            raise ValueError(
                f"compose_mix 仅服务 dh_mix 模板(当前 {tpl['name']})")
        if not card_pages:
            raise ValueError("dh_mix 缺卡片镜页面")
        if len(card_pages) != len(storyboard["scenes"]) - 1:
            raise ValueError(
                f"卡片镜页数失配: {len(card_pages)} 页 / "
                f"{len(storyboard['scenes']) - 1} 镜")
        out_mp4 = Path(out_mp4)
        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        w, h = tpl["pageW"], tpl["pageH"]
        plan = self.mix_plan(
            storyboard, self._probe_duration(Path(oral_mp4)))
        fade = plan["fade"]

        args = [_ffmpeg_bin(), "-y", "-i", str(oral_mp4)]
        for p, d in zip(card_pages, plan["durations"][1:]):
            args += ["-loop", "1", "-t", f"{d:.2f}",
                     "-r", str(INPUT_FPS), "-i", str(p)]

        parts = [
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1,fps={INPUT_FPS},settb=AVTB[v0]"]
        for i, d in enumerate(plan["durations"][1:], 1):
            frames = max(2, int(d * INPUT_FPS))
            parts.append(
                f"[{i}:v]scale={w}:{h},setsar=1,"
                f"zoompan=z='1+{ZOOM_MAX - 1:.2f}*on/{frames - 1}'"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d=1:s={w}x{h}:fps={INPUT_FPS},"
                f"settb=AVTB[v{i}]")
        prev = "[v0]"
        for i, off in enumerate(plan["offsets"], 1):
            nxt = f"[x{i}]"
            parts.append(
                f"{prev}[v{i}]xfade=transition=fade:"
                f"duration={fade:.2f}:offset={off:.2f}{nxt}")
            prev = nxt
        parts.append(
            f"{prev}format=yuv420p[v];"
            f"[0:a]apad=whole_dur={plan['total']:.2f},"
            f"atrim=0:{plan['total']:.2f}[a]")
        fc = ";".join(parts)
        args += ["-filter_complex", fc,
                 "-map", "[v]", "-map", "[a]",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                 "-r", str(INPUT_FPS), "-c:a", "aac",
                 "-movflags", "+faststart", str(out_mp4)]
        result = subprocess.run(args, capture_output=True, timeout=600)
        if result.returncode != 0 or not out_mp4.is_file():
            raise RuntimeError(
                f"dh_mix 合成失败(exit={result.returncode}): "
                f"{result.stderr.decode('utf-8', 'replace')[-400:]}")
        logger.info("sv73_dh_mix_composed -> %s (%s bytes, total %.2fs)",
                    out_mp4, out_mp4.stat().st_size, plan["total"])
        return out_mp4

    # ---------- TTS 配音轨(可选, 48号小竹语音) ----------

    @staticmethod
    def _strip_beep_lead(wav_bytes: bytes) -> bytes:
        """剥除 cogtts 固定前导嘟嘟指纹段(2026-10-02 三片+旧片+
        复现 8 样本实证; 指纹不匹配安全跳过零损伤)

        嘟嘟=恒定振幅帧串+孤立静音(频谱: 200-400Hz 窄带脉冲),
        SadTalker 全段驱动 → 嘴动无配音缺陷; 剥除点=指纹尾
        1.78s(88 帧), 其后过渡微弱段一并带过。
        """
        import array as _array
        import struct as _struct
        if len(wav_bytes) < 200 or wav_bytes[:4] != b"RIFF":
            return wav_bytes
        try:
            sr = _struct.unpack(
                "<I", wav_bytes[24:28])[0]
            pos = 12
            data_off = data_sz = None
            while pos + 8 <= len(wav_bytes):
                cid = wav_bytes[pos:pos + 4]
                sz = _struct.unpack(
                    "<I", wav_bytes[pos + 4:pos + 8])[0]
                if cid == b"data":
                    data_off, data_sz = pos + 8, sz
                    break
                pos += 8 + sz + (sz & 1)
            if data_off is None or sr <= 0:
                return wav_bytes
            frame = int(sr * 0.02)
            need = len(_BEEP_FP)
            raw = wav_bytes[data_off:
                            data_off + frame * (need + 2) * 2]
            samples = _array.array("h", raw)
            if len(samples) < frame * need:
                return wav_bytes
            rms = []
            for i in range(need + 2):
                chunk = samples[i * frame:(i + 1) * frame]
                acc = 0
                for s in chunk:
                    acc += s * s
                rms.append((acc / len(chunk)) ** 0.5)
            mean_diff = sum(
                abs(a - b) for a, b in zip(rms, _BEEP_FP)
            ) / need
            if mean_diff > 120:
                return wav_bytes     # 指纹不匹配: 零损伤跳过
            # (阈值 120: 8 病样本+短文本复现实测 57; 正常语音
            #  波动模式与固定 beep 序列 mean_diff 在数百级——
            #  实证长/短文本均命中同指纹, cogtts 整句接口全量行为)
            strip_bytes = int(1.78 * sr) * 2
            if data_sz <= strip_bytes:
                return wav_bytes
            new_data = wav_bytes[data_off + strip_bytes:
                                 data_off + data_sz]
            head = wav_bytes[:data_off]
            out = bytearray(head)
            out[4:8] = _struct.pack(
                "<I", len(head) + len(new_data) - 8)
            # data 块长度字段(块头 +4)
            out[data_off - 4:data_off] = _struct.pack(
                "<I", len(new_data))
            out += new_data
            logger.info("sv73_tts_beep_stripped bytes=%d "
                        "mean_diff=%.1f", strip_bytes,
                        mean_diff)
            return bytes(out)
        except Exception as exc:      # noqa: BLE001
            logger.warning("sv73_tts_strip_failed: %s", exc)
            return wav_bytes

    def _tts_audio(self, storyboard: dict) -> Path | None:
        """全量 voiceover → WAV(SV73_TTS_MODE=on 时; P1-2 语速
        随 BGM 情绪档——48号 MOOD_SPEED 同值锚定; 失败 fail-soft
        返回 None——无声同 36号; cogtts 前导嘟嘟指纹剥除)"""
        from services.llm_client import provider_client
        text = "。".join(sc["voiceover"]
                         for sc in storyboard["scenes"])
        speed = BGM_MOOD_SPEED.get(
            storyboard["bgm"]["mood"], 1.0)
        try:
            wav = provider_client.synthesize(
                text, speed=speed,
                voice=IP_VOICE.get(storyboard["persona"]))
        except Exception as exc:
            logger.warning("sv73_tts_failed: %s", exc)
            return None
        if not wav:
            return None
        wav = self._strip_beep_lead(wav)
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
