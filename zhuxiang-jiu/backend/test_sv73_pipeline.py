"""73号(sv)·短视频智能模型 P0e 专项——渲染引擎 + 全链编排

覆盖(P0 验收口径 §五):
    R1 渲染实机: storyboard → 4 PNG(1080×1440) + mp4(sizeBytes>0)
    R2 运镜总时长实证: ffmpeg -i 元数据 Duration ≈ 16.2s(注册表)
    R3 TTS 可选轨: 默认 off 无声; on 轨(静音 WAV 桩)混流 voiced mp4
    R4 编排五步幂等: 两次 run 同 scriptId/video, content 复用
    R5 三态语义: shadow(留痕不占归因) / real(完整登记)
    R6 rpa_pending 全链: run→36号人工 review approve→publish→
       process 出队 receipt.mode=rpa_pending(三审闸门不动实证)
    R7 平台白名单: 非 RPA 平台 ValueError
    R8 页面差异化: action/cover 页 PNG 字节不同(防同图回归)

运行: python -m pytest test_sv73_pipeline.py -q
(实机 ffmpeg: 36号生产线便携版口径; LLM 轨全程确定性关闭)
"""

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# 内存模式 + LLM off(36号脚本式测试同款口径——须在 import 服务前设置)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

sys.path.insert(0, str(Path(__file__).resolve().parent))

import services.sv73_script_service as sv73mod
import services.sv73_render_service as sv73ren
from services.sv73_script_service import (
    Sv73ScriptService, TRACK_RULE,
)
from services.sv73_render_service import Sv73RenderService
from services.sv73_pipeline_service import Sv73PipelineService
from services.promo_service import PromoService
from services.promo_video_service import _ffmpeg_bin

HOTSPOT = {
    "hotspotId": 901,
    "fingerprint": "fp-sv73-pipe-0001",
    "platform": "douyin",
    "title": "中秋家宴用酒怎么选",
    "heat": 800,
    "score": 82,
}


def _run(coro):
    return asyncio.run(coro)


def _silence_wav(seconds: float = 15.0, rate: int = 16000) -> bytes:
    """最小合法静音 WAV(RIFF 头+零 PCM)——TTS 桩"""
    import struct
    data = b"\x00\x00" * int(seconds * rate)
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
           b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate,
                                 rate * 2, 2, 16)
           + b"data" + struct.pack("<I", len(data)))
    return hdr + data


def _duration_of(path: Path) -> float:
    """ffmpeg -i 读元数据时长(无输出参数报错但 stderr 已含)"""
    r = subprocess.run([_ffmpeg_bin(), "-i", str(path)],
                       capture_output=True, timeout=60)
    err = r.stderr.decode("utf-8", "replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", err)
    assert m, f"未解析到 Duration: {err[-300:]}"
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def _make_storyboard():
    """确定性剧本(rule 轨——不依赖 LLM)"""
    sb = asyncio.run(Sv73ScriptService().generate(
        dict(HOTSPOT)))
    assert sb["track"] == TRACK_RULE
    return sb


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """落盘隔离 + LLM 确定性 + real 态 + 全链渲染

    本套件验证全链实机(R1-R8 断言 mp4 产物)——渲染步显式
    on(分段语义默认 off 由 test_sv73_split.py 覆盖)。
    """
    monkeypatch.setattr(sv73mod, "_STORYBOARD_DIR", tmp_path / "sb")
    monkeypatch.setattr(sv73ren, "SV73_VIDEO_DIR", tmp_path / "vd")
    monkeypatch.setattr(
        Sv73ScriptService, "_chat_json",
        lambda self, s, u: (None, TRACK_RULE))
    monkeypatch.setenv("SV73_MODE", "real")
    monkeypatch.setenv("SV73_TTS_MODE", "off")
    monkeypatch.setenv("SV73_RENDER_MODE", "on")
    yield


# ------------------------------------------------------------
# R1 渲染实机(页面+视频)
# ------------------------------------------------------------


def test_r1_render_real_machine():
    sb = _make_storyboard()
    built = Sv73RenderService().build(sb)
    # 4 页 PNG, 1080×1440
    assert len(built["pages"]) == 4
    from PIL import Image
    for p in built["pages"]:
        img = Image.open(p)
        assert img.size == (1080, 1440)
    # mp4 实产出
    assert Path(built["video"]).exists()
    assert built["sizeBytes"] > 0
    assert built["durationSeconds"] == 16.2
    # 无声轨(TTS off)
    assert built["audioTrack"] == ""


# ------------------------------------------------------------
# R2 运镜总时长实证(ffmpeg 元数据)
# ------------------------------------------------------------


def test_r2_video_duration_registered():
    sb = _make_storyboard()
    built = Sv73RenderService().build(sb)
    dur = _duration_of(Path(built["video"]))
    assert abs(dur - 16.2) < 0.6, f"总时长失配: {dur}"


# ------------------------------------------------------------
# R3 TTS 可选轨(on 轨混流)
# ------------------------------------------------------------


def test_r3_tts_track(monkeypatch):
    import services.llm_client as llmmod
    monkeypatch.setenv("SV73_TTS_MODE", "on")
    # 静音桩故意短于视频(15s < 16.2s)——验证 -t 注册表时长
    # 截断(视频流完整性优先, 不因音频短而切尾)
    monkeypatch.setattr(
        llmmod.provider_client, "synthesize",
        lambda text, speed=None, voice=None, _retry=True:
        _silence_wav(seconds=15.0))
    sb = _make_storyboard()
    built = Sv73RenderService().build(sb)
    assert built["audioTrack"] != ""
    voiced = Path(built["video"])
    assert voiced.name.endswith("_voiced.mp4")
    assert voiced.exists() and voiced.stat().st_size > 0
    # 混流产物时长仍为注册表 16.2s(防 -shortest 截尾回归)
    assert abs(_duration_of(voiced) - 16.2) < 0.6, (
        f"voiced 时长失配: {_duration_of(voiced)}")


# ------------------------------------------------------------
# R4 编排五步幂等
# ------------------------------------------------------------


def test_r4_pipeline_idempotent():
    pipe = Sv73PipelineService()
    a = _run(pipe.run(dict(HOTSPOT)))
    b = _run(pipe.run(dict(HOTSPOT)))
    assert a["render"]["scriptId"] == b["render"]["scriptId"]
    assert a["render"]["video"] == b["render"]["video"]
    # content 复用(不重复登记)
    assert a["reused"] is False
    assert b["reused"] is True
    assert (a["content"]["contentId"]
            == b["content"]["contentId"])
    # 产物关联齐备(video 非空防呆——Path('')≡'.' 会静默放过)
    sv = b["content"]["sv73"]
    assert sv["storyboardId"] == b["render"]["scriptId"]
    assert sv["video"] and Path(sv["video"]).exists()
    assert sv["totalDuration"] == 16.2


# ------------------------------------------------------------
# R5 三态语义(shadow 留痕 / real 完整)
# ------------------------------------------------------------


def test_r5_shadow_vs_real(monkeypatch):
    monkeypatch.setenv("SV73_MODE", "shadow")
    # 独立热点(repo 模块级共享——避免与 R4 幂等复用撞 content)
    sh = _run(Sv73PipelineService().run(
        {**HOTSPOT, "fingerprint": "fp-sv73-pipe-shadow",
         "title": "春节团圆酒单"}))
    assert sh["mode"] == "shadow"
    assert sh["content"]["sv73"]["shadow"] is True
    assert sh["content"]["shortCode"] == ""   # 影子不占归因通道
    assert sh["content"]["status"] == "pending"  # 三审闸门不动

    monkeypatch.setenv("SV73_MODE", "real")
    # 换热点避免与 shadow 轮 content 幂等复用撞 id
    alt = {**HOTSPOT, "fingerprint": "fp-sv73-pipe-real",
           "title": "国庆送礼清单"}
    re_ = _run(Sv73PipelineService().run(alt))
    assert re_["mode"] == "real"
    assert re_["content"]["sv73"]["shadow"] is False
    assert re_["content"]["status"] == "pending"
    # nextSteps 指引 36号人工三审链
    assert "review" in re_["nextSteps"]


# ------------------------------------------------------------
# R6 rpa_pending 全链(36号三审闸门不动实证)
# ------------------------------------------------------------


def test_r6_rpa_pending_full_chain(monkeypatch):
    monkeypatch.setenv("PROMO_CHANNEL_MODE", "real")  # 无凭证→rpa_pending
    pipe = Sv73PipelineService()
    result = _run(pipe.run({**HOTSPOT,
                           "fingerprint": "fp-sv73-pipe-rpa",
                           "title": "重阳节敬老酒礼"}))
    cid = result["content"]["contentId"]
    promo = PromoService()

    # 三审人工位: approve(36号既有 review_content)
    _run(promo.review_content(cid, approved=True))
    content = _run(pipe.repo.get_content(cid))
    assert content["status"] == "approved"

    # 入队(过去时刻→立即可出队)
    _run(promo.publish_content(
        cid, publish_at="2020-01-01T00:00:00+00:00"))
    content = _run(pipe.repo.get_content(cid))
    assert content["status"] == "queued"

    # 出队 → rpa_pending 回执(RPA 待发清单挂载)
    published = _run(promo.process_publish_queue())
    hit = next(c for c in published
               if c["contentId"] == cid)
    assert hit["receipt"]["mode"] == "rpa_pending"
    assert hit["status"] == "published"
    # 73号 产物关联随 content 全链保留(mp4 供 RPA 发布消费)
    assert hit["sv73"]["video"] \
        and Path(hit["sv73"]["video"]).exists()
    assert hit["sv73"]["storyboardId"] == result["render"]["scriptId"]
    # list_pending 观测面可见(36号 RPA 待发清单惯例)
    from services.promo_rpa_channel_service import (
        PromoRpaChannelService,
    )
    rows = _run(PromoRpaChannelService().list_pending(
        platform="wechat_channels"))
    assert any(r["contentId"] == cid for r in rows)


# ------------------------------------------------------------
# R7 平台白名单
# ------------------------------------------------------------


def test_r7_platform_whitelist():
    # 平台扩展后白名单=四平台(wechat_channels/douyin/xhs/weibo);
    # 真未知平台仍拒(weibo 已合法——见 test_sv73_platform.py)
    with pytest.raises(ValueError):
        _run(Sv73PipelineService().run(
            dict(HOTSPOT), platform="bilibili"))


# ------------------------------------------------------------
# R8 页面差异化(防同图回归)
# ------------------------------------------------------------


def test_r8_pages_distinct():
    sb = _make_storyboard()
    pages = Sv73RenderService().render_pages(sb)
    blobs = [Path(p).read_bytes() for p in pages]
    assert len(set(blobs)) == len(blobs), "四页存在同图"


# ------------------------------------------------------------
# R9 分级路由 + 归因短链指引(2026-10-04)
# ------------------------------------------------------------


def test_r9_tier_routing(monkeypatch):
    """template=tier 按热点分值选 B/A/S 档; real 态短码→nextSteps
    带 /r/ 引流短链(归因冷启动载体指引)"""
    monkeypatch.setenv("SV73_MODE", "real")
    hi = _run(Sv73PipelineService().run(
        {**HOTSPOT, "fingerprint": "fp-sv73-pipe-tier-s",
         "title": "元旦跨年酒单", "score": 88},
        template="tier"))
    assert hi["tier"] == "S"
    assert hi["storyboard"]["template"]["name"] == "dh_mix"
    lo = _run(Sv73PipelineService().run(
        {**HOTSPOT, "fingerprint": "fp-sv73-pipe-tier-b",
         "title": "小雪节气谈酒", "score": 42},
        template="tier"))
    assert lo["tier"] == "B"
    assert lo["storyboard"]["template"]["name"] == "vertical"
    # 归因冷启动: real 态过闸建短码 → nextSteps 注入 rLink 指引
    assert "zxjiu.com/r/" in hi["nextSteps"]
