"""73号(sv)·数字人口播服务(LivePortrait GPU 轨) v1.0

数字人 GPU 轨 P1(2026-09-30 立项, 见 docs/73号_数字人GPU轨_P1启动
评估方案.md): dh_oral 模板(单镜 15s)的视频主轨——LivePortrait v1.5
audio-driven 驱动 IP 基准图 → 口播口型 mp4。

架构对齐(零新语义):
  · 分段铁律: SV73_DH_MODE(默认 off)同 SV73_RENDER_MODE——生产
    pipeline 只跑"剧本+登记", GPU 推理留在算力机(AutoDL 按量),
    产物经 build_dh_dev.py → /api/sv73/render/attach 回填
    (attach 幂等, renderSource=devmachine 语义复用)
  · 音轨复用: 78号竹语 TTS(Sv73RenderService._tts_audio 产物
    {scriptId}_tts.wav), 本服务只做"图+音频→口播"一步
  · 合规前置: 口播文案的 compliance_gate 在剧本层已过
    (MOCK dh_oral voiceover 尾部固定携带警示语)

基准图(P1 PoC 占位): assets/ip/ip-square.png(70号金鹿瑞兽角标
——链路先行验证; 正式竹小妹人物基准图待定版, SV73_DH_IMAGE 可配)。

LivePortrait 命令(v1.5 audio-driven 口径, 未实机校准项全部
可配——21 轮联调教训): SV73_DH_CMD 覆盖默认命令模板, 占位符
{image}/{audio}/{out} 由本服务注入。
"""

import os
import shlex
import subprocess
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# 落盘目录(与 sv73_render_service 同源)
SV73_VIDEO_DIR = Path(os.environ.get(
    "SV73_VIDEO_DIR",
    str(Path(__file__).resolve().parent.parent / "sv73_videos")))

# 口播基准图(P1 占位: 金鹿 IP 角标——正式竹小妹基准图待定版)
DH_IMAGE = Path(os.environ.get(
    "SV73_DH_IMAGE",
    str(Path(__file__).resolve().parent.parent
        / "assets" / "ip" / "ip-square.png")))

# LivePortrait 仓库根(SV73_DH_MODE=real 的算力机必配)
LIVEPORTRAIT_DIR = os.environ.get("SV73_LIVEPORTRAIT_DIR", "")

# 推理命令模板(v1.5 audio-driven 口径; {image}/{audio}/{out} 注入)
# 未实机校准——首次 GPU 机联调按留档校准(SV73_DH_CMD 全量可配)
DEFAULT_DH_CMD = (
    "python inference.py -s {image} -a {audio} "
    "--flag_lip_zeros --flag_pasteback --output_dir {outdir}")

# GPU 推理超时(A10 单条 15s 口播量级; 留裕量防慢实例)
DH_TIMEOUT_SECONDS = 900


def dh_mode(mode: str | None = None) -> str:
    """SV73_DH_MODE(off|mock|real, 默认 off——生产铁律)

    off  : 本服务不可用(生产/零 GPU 环境真实态)
    mock : 确定性演练——不调 GPU, 产出注册式元数据(链路/测试用)
    real : 真实 LivePortrait 推理(算力机: SV73_LIVEPORTRAIT_DIR
           必配, 否则 ValueError fail-hard)
    显式 mode 参数覆盖 env(单次控制; 同 render_mode_enabled 惯例)。
    """
    m = (mode if mode is not None
         else os.environ.get("SV73_DH_MODE", "off"))
    return str(m).strip().lower()


class Sv73DigitalHumanService:
    """dh_oral 模板视频主轨: IP 基准图 + TTS 音频 → 口播 mp4"""

    def build(self, storyboard: dict, tts_wav: Path) -> dict:
        """storyboard(dh_oral)+TTS 音频 → 口播产物清单

        Raises:
            ValueError: 模板非 dh_oral / 音频缺失 / mode=off /
                        mode=real 未配 SV73_LIVEPORTRAIT_DIR
        """
        tpl = (storyboard.get("template") or {}).get("name")
        if tpl != "dh_oral":
            raise ValueError(
                f"数字人轨仅服务 dh_oral 模板(当前 {tpl})——"
                "零 GPU 模板走 Sv73RenderService")
        if not (tts_wav and Path(tts_wav).is_file()):
            raise ValueError(
                f"TTS 音频缺失或不存在: {tts_wav}(先经 "
                "Sv73RenderService._tts_audio 产 wav)")
        sid = storyboard["scriptId"]
        mode = dh_mode()
        if mode == "off":
            raise ValueError(
                "SV73_DH_MODE=off(默认铁律)——GPU 推理留算力机, "
                "生产 pipeline 只跑剧本+登记(build_dh_dev.py 范式)")
        if not DH_IMAGE.is_file():
            raise ValueError(f"口播基准图缺失: {DH_IMAGE}")
        out_mp4 = SV73_VIDEO_DIR / f"{sid}_dh.mp4"
        engine = "liveportrait"
        if mode == "mock":
            # 确定性演练: 登记式元数据, 不调 GPU(时长/引擎为真值口径)
            engine = "mock"
            logger.info("sv73_dh_mock script=%s image=%s", sid, DH_IMAGE)
            return self._result(storyboard, out_mp4, tts_wav,
                                engine=engine, size_bytes=0)
        # mode=real: LivePortrait 推理
        if not LIVEPORTRAIT_DIR:
            raise ValueError(
                "SV73_DH_MODE=real 须配 SV73_LIVEPORTRAIT_DIR"
                "(LivePortrait 仓库根, 算力机)")
        cmd_tpl = os.environ.get("SV73_DH_CMD", DEFAULT_DH_CMD)
        out_dir = SV73_VIDEO_DIR
        cmd = (cmd_tpl
               .replace("{image}", shlex.quote(str(DH_IMAGE)))
               .replace("{audio}", shlex.quote(str(tts_wav)))
               .replace("{outdir}", shlex.quote(str(out_dir)))
               .replace("{out}", shlex.quote(str(out_mp4))))
        logger.info("sv73_dh_infer script=%s cmd=%s", sid, cmd)
        result = subprocess.run(
            shlex.split(cmd), cwd=LIVEPORTRAIT_DIR,
            capture_output=True, timeout=DH_TIMEOUT_SECONDS)
        if result.returncode != 0 or not out_mp4.is_file():
            raise RuntimeError(
                f"LivePortrait 推理失败(exit={result.returncode}): "
                f"{result.stderr.decode('utf-8', 'replace')[-400:]}")
        return self._result(storyboard, out_mp4, tts_wav,
                            engine=engine,
                            size_bytes=out_mp4.stat().st_size)

    @staticmethod
    def _result(storyboard: dict, video: Path, audio: Path,
                engine: str, size_bytes: int) -> dict:
        """产物清单(对齐 Sv73RenderService.build 返回结构)"""
        return {
            "scriptId": storyboard["scriptId"],
            "video": str(video),
            "pages": [],          # PNG 主题卡由 render_pages 另产
            "audioTrack": str(audio),
            "sizeBytes": int(size_bytes),
            "durationSeconds": storyboard["totalDuration"],
            "engine": engine,     # liveportrait|mock
            "dhImage": str(DH_IMAGE),
        }
