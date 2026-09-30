"""73号(sv)·数字人口播服务(SadTalker GPU 轨) v1.1

数字人 GPU 轨 P1(2026-09-30 立项, 见 docs/73号_数字人GPU轨_P1启动
评估方案.md): dh_oral 模板(单镜 15s)的视频主轨——SadTalker 音频驱动
静态 IP 基准图 → 口播口型 mp4。

方案修正(2026-09-30 GPU 机实证): 原评估文档首选 LivePortrait——
实测官方 main 无 audio-driven 口型(仅视频驱动, README 生态区指向
AVTR-1/ditto); 按 docx 方案 B 原文列名的 SadTalker(单图+音频→
口播)落地, AutoDL RTX 5090D 出片实证。

架构对齐(零新语义):
  · 分段铁律: SV73_DH_MODE(默认 off)同 SV73_RENDER_MODE——生产
    pipeline 只跑"剧本+登记", GPU 推理留在算力机(AutoDL 按量),
    产物经 build_dh_dev.py → /api/sv73/render/attach 回填
  · 音轨复用: 78号竹语 TTS(Sv73RenderService._tts_audio 产物
    {scriptId}_tts.wav), 本服务只做"图+音频→口播"一步
  · 合规前置: 口播文案的 compliance_gate 在剧本层已过

基准图(2026-10-01 定版): assets/ip/zhuxiaomei_front.jpg(竹小妹
正面照 853×1515——金鹿角标无人脸 landmark 失败实证后正式定版,
SV73_DH_IMAGE 可配; GPU 侧 landmark 实测随下次跑批首验)。

SadTalker 命令(未实机校准项全部可配——21 轮联调教训):
SV73_DH_CMD 覆盖默认命令模板, 占位符 {image}/{audio}/{outdir}/{out}
由本服务注入; 产物须对齐 {out}(={scriptId}_dh.mp4)——默认模板尾部
find 按修改时间取最新 mp4 对齐。环境就绪(GPU 机 2026-09-30 部署实录):
  · torch 2.7.1+cu128(Blackwell sm_120 必需——cu118/cu126 均报
    no kernel image; 上交镜像 pytorch-wheels/cu128 全 URL 方案)
  · numpy 2.5.3 + float() 严格化 ravel 补丁(face3d/util/preprocess.py
    三处 + utils/preprocess.py 一处——numpy 2.x 只许 0 维数组 float())
  · basicsr sed 补丁(functional_tensor→functional, torchvision 0.22 坑)
  · ffmpeg: imageio-ffmpeg 二进制软链 /usr/local/bin/ffmpeg(moviepy 收尾)
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

# 口播基准图(竹小妹正面照——2026-10-01 定版; SV73_DH_IMAGE 可配)
DH_IMAGE = Path(os.environ.get(
    "SV73_DH_IMAGE",
    str(Path(__file__).resolve().parent.parent
        / "assets" / "ip" / "zhuxiaomei_front.jpg")))

# SadTalker 仓库根(SV73_DH_MODE=real 的算力机必配)
SADTALKER_DIR = os.environ.get("SV73_SADTALKER_DIR", "")

# 推理命令模板(SadTalker 口径, 2026-09-30 5090D 出片实证;
# {image}/{audio}/{outdir}/{out} 注入)——产物落点两形态(result_dir
# 根级 {timestamp}.mp4 为主, 预处理时间戳目录内亦有同名族——real
# 实弹实证根级为最终版), 模板尾部 find -printf 按修改时间取最新
# mp4 cp 对齐 {out}(时间排序天然防 result_dir 复用时旧产物污染);
# 命令走 bash -c
DEFAULT_DH_CMD = (
    "/root/miniconda3/envs/py312/bin/python inference.py "
    "--driven_audio {audio} --source_image {image} "
    "--result_dir {outdir} --still --preprocess full && "
    "latest=$(find {outdir} -maxdepth 2 -name '*.mp4' "
    "-printf '%T@\\t%p\\n' | sort -rn | head -1 | cut -f2) && "
    "cp \"$latest\" {out}")

# GPU 推理超时(单条 15s 口播 5090D 实测约 1-3 分钟; 留裕量防慢实例)
DH_TIMEOUT_SECONDS = 900


def dh_mode(mode: str | None = None) -> str:
    """SV73_DH_MODE(off|mock|real, 默认 off——生产铁律)

    off  : 本服务不可用(生产/零 GPU 环境真实态)
    mock : 确定性演练——不调 GPU, 产出注册式元数据(链路/测试用)
    real : 真实 SadTalker 推理(算力机: SV73_SADTALKER_DIR
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
                        mode=real 未配 SV73_SADTALKER_DIR
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
        engine = "sadtalker"
        if mode == "mock":
            # 确定性演练: 登记式元数据, 不调 GPU(时长/引擎为真值口径)
            engine = "mock"
            logger.info("sv73_dh_mock script=%s image=%s", sid, DH_IMAGE)
            return self._result(storyboard, out_mp4, tts_wav,
                                engine=engine, size_bytes=0)
        # mode=real: SadTalker GPU 推理
        if not SADTALKER_DIR:
            raise ValueError(
                "SV73_DH_MODE=real 须配 SV73_SADTALKER_DIR"
                "(SadTalker 仓库根, 算力机)")
        cmd_tpl = os.environ.get("SV73_DH_CMD", DEFAULT_DH_CMD)
        out_dir = SV73_VIDEO_DIR
        cmd = (cmd_tpl
               .replace("{image}", shlex.quote(str(DH_IMAGE)))
               .replace("{audio}", shlex.quote(str(tts_wav)))
               .replace("{outdir}", shlex.quote(str(out_dir)))
               .replace("{out}", shlex.quote(str(out_mp4))))
        logger.info("sv73_dh_infer script=%s cmd=%s", sid, cmd)
        # bash -c: 模板含 $()/管道(产物时间戳目录对齐), shlex.split
        # 会拆坏 bash 语法
        result = subprocess.run(
            ["bash", "-c", cmd], cwd=SADTALKER_DIR,
            capture_output=True, timeout=DH_TIMEOUT_SECONDS)
        if result.returncode != 0 or not out_mp4.is_file():
            raise RuntimeError(
                f"SadTalker 推理失败(exit={result.returncode}): "
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
            "engine": engine,     # sadtalker|mock
            "dhImage": str(DH_IMAGE),
        }
