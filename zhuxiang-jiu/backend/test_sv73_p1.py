"""73号(sv)·短视频智能模型 P1 专项——匹配引擎+视频模板库+TTS 情感档

覆盖(规划 §三 P1):
    M1 匹配引擎: 中秋热点→礼盒(关键词命中) / 无关热点→兜底经典 /
       同输入确定性 / ranked 7 系列全 / 排序合法
    T2 模板库: landscape(1920×1080/4镜/16.2s) / fast(5镜含proof/
       3.6s/15.6s) / 未注册模板 409 / scriptId 模板维度 /
       schema 封闭断言过
    R3 渲染实机: landscape PNG 尺寸+mp4 时长 / fast 5 页+mp4 时长
    S4 TTS 情感档: BGM warm → speed=0.92 实参断言(键实一致,
       防 48号预合成键实不符同款坑)
    P5 pipeline auto 档: category=auto 匹配引擎接入+幂等

运行: python -m pytest test_sv73_p1.py -q
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

sys.path.insert(0, str(Path(__file__).resolve().parent))

import services.sv73_script_service as sv73mod
import services.sv73_render_service as sv73ren
from services.sv73_script_service import (
    Sv73ScriptService, TRACK_RULE, assert_schema,
)
from services.sv73_render_service import (
    Sv73RenderService, BGM_MOOD_SPEED,
)
from services.sv73_match_service import (
    Sv73MatchService, SERIES_KEYWORDS, FALLBACK_SERIES,
)
from services.sv73_pipeline_service import Sv73PipelineService

HOTSPOT_MIDAUTUMN = {
    "fingerprint": "fp-sv73-p1-midautumn",
    "platform": "douyin",
    "title": "中秋家宴用酒怎么选",
    "score": 82,
}
HOTSPOT_IRRELEVANT = {
    "fingerprint": "fp-sv73-p1-irrelevant",
    "platform": "weibo",
    "title": "量子计算机新突破",
    "score": 55,
}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(sv73mod, "_STORYBOARD_DIR", tmp_path / "sb")
    monkeypatch.setattr(sv73ren, "SV73_VIDEO_DIR", tmp_path / "vd")
    monkeypatch.setattr(
        Sv73ScriptService, "_chat_json",
        lambda self, s, u: (None, TRACK_RULE))
    monkeypatch.setenv("SV73_MODE", "real")
    monkeypatch.setenv("SV73_TTS_MODE", "off")
    yield


# ------------------------------------------------------------
# M1 匹配引擎(确定性规则)
# ------------------------------------------------------------


def test_m1_match_midautumn_giftbox():
    r = _run(Sv73MatchService().match(dict(HOTSPOT_MIDAUTUMN)))
    assert r["fallback"] is False
    assert r["topSeries"] == "礼盒"   # 中秋+家宴 关键词命中
    top = r["ranked"][0]
    assert top["series"] == "礼盒"
    assert "中秋" in top["hitKeywords"]
    # ranked 覆盖 7 系列全
    assert {row["series"] for row in r["ranked"]} == set(
        SERIES_KEYWORDS)
    # 排序合法: score 降序
    scores = [row["score"] for row in r["ranked"]]
    assert scores == sorted(scores, reverse=True)


def test_m1_match_fallback():
    r = _run(Sv73MatchService().match(dict(HOTSPOT_IRRELEVANT)))
    assert r["fallback"] is True
    assert r["topSeries"] == FALLBACK_SERIES


def test_m1_match_deterministic():
    a = _run(Sv73MatchService().match(dict(HOTSPOT_MIDAUTUMN)))
    b = _run(Sv73MatchService().match(dict(HOTSPOT_MIDAUTUMN)))
    assert a == b   # 同输入同输出(零 LLM 零随机)


# ------------------------------------------------------------
# T2 模板库(schema+参数)
# ------------------------------------------------------------


def test_t2_template_landscape():
    sb = _run(Sv73ScriptService().generate(
        dict(HOTSPOT_MIDAUTUMN), template="landscape"))
    assert_schema(sb)
    assert sb["template"]["name"] == "landscape"
    assert sb["template"]["pageW"] == 1920
    assert sb["template"]["pageH"] == 1080
    assert len(sb["scenes"]) == 4
    assert sb["totalDuration"] == 16.2   # 4*4.5-3*0.6


def test_t2_template_fast():
    sb = _run(Sv73ScriptService().generate(
        dict(HOTSPOT_MIDAUTUMN), template="fast"))
    assert_schema(sb)
    assert sb["template"]["name"] == "fast"
    roles = [sc["role"] for sc in sb["scenes"]]
    assert roles == ["cover", "selling", "proof", "action",
                     "compliance"]
    assert all(sc["duration"] == 3.6 for sc in sb["scenes"])
    assert sb["totalDuration"] == 15.6   # 5*3.6-4*0.6


def test_t2_template_reject_and_id_dimension():
    with pytest.raises(ValueError):
        _run(Sv73ScriptService().generate(
            dict(HOTSPOT_MIDAUTUMN), template="imax"))
    v = _run(Sv73ScriptService().generate(
        dict(HOTSPOT_MIDAUTUMN)))   # 默认 vertical
    f = _run(Sv73ScriptService().generate(
        dict(HOTSPOT_MIDAUTUMN), template="fast"))
    assert v["scriptId"] != f["scriptId"]   # 模板维度入 id


# ------------------------------------------------------------
# R3 渲染模板实机(横版/快节奏)
# ------------------------------------------------------------


def test_r3_render_landscape():
    sb = _run(Sv73ScriptService().generate(
        dict(HOTSPOT_MIDAUTUMN), template="landscape"))
    built = Sv73RenderService().build(sb)
    from PIL import Image
    for p in built["pages"]:
        assert Image.open(p).size == (1920, 1080)
    assert Path(built["video"]).exists()
    assert built["durationSeconds"] == 16.2


def test_r3_render_fast():
    sb = _run(Sv73ScriptService().generate(
        dict(HOTSPOT_MIDAUTUMN), template="fast"))
    built = Sv73RenderService().build(sb)
    assert len(built["pages"]) == 5   # 含 proof 背书镜
    from PIL import Image
    for p in built["pages"]:
        assert Image.open(p).size == (1080, 1440)
    assert Path(built["video"]).exists()
    assert built["durationSeconds"] == 15.6


# ------------------------------------------------------------
# S4 TTS 情感档(BGM mood → speed 实参)
# ------------------------------------------------------------


def test_s4_tts_speed_matches_bgm_mood(monkeypatch):
    import services.llm_client as llmmod

    captured = {}

    def fake_synthesize(text, speed=None, voice=None, _retry=True):
        captured["speed"] = speed
        captured["text"] = text
        # 最小合法静音 WAV
        import struct
        data = b"\x00\x00" * 16000
        return (b"RIFF" + struct.pack("<I", 36 + len(data))
                + b"WAVEfmt " + struct.pack(
                    "<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
                + b"data" + struct.pack("<I", len(data)) + data)

    monkeypatch.setenv("SV73_TTS_MODE", "on")
    monkeypatch.setattr(llmmod.provider_client, "synthesize",
                        fake_synthesize)
    sb = _run(Sv73ScriptService().generate(
        dict(HOTSPOT_MIDAUTUMN)))   # 默认 BGM warm
    Sv73RenderService().build(sb)
    # 键实一致: warm → 0.92(48号 MOOD_SPEED care 同值锚定)
    assert sb["bgm"]["mood"] == "warm"
    assert captured["speed"] == BGM_MOOD_SPEED["warm"]
    assert captured["speed"] == 0.92


# ------------------------------------------------------------
# P5 pipeline auto 档(匹配引擎接入)
# ------------------------------------------------------------


def test_p5_pipeline_auto_category():
    result = _run(Sv73PipelineService().run(
        dict(HOTSPOT_MIDAUTUMN), category="auto"))
    # 匹配引擎接入: match 段回传 + 品类落到 storyboard
    assert result["match"] is not None
    assert result["match"]["topSeries"] == "礼盒"
    assert result["storyboard"]["category"] == "礼盒"
    # auto 幂等: 二次 run 复用 content
    again = _run(Sv73PipelineService().run(
        dict(HOTSPOT_MIDAUTUMN), category="auto"))
    assert again["reused"] is True
    assert (again["content"]["contentId"]
            == result["content"]["contentId"])
