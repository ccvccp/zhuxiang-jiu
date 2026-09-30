"""73号(sv)·数字人 GPU 轨 P1 专项——dh_oral 模板+LivePortrait 封装

覆盖(P1 启动评估方案 §六 实施路径):
    T1 dh_oral 模板注册合规(注册表/单镜 15s/总时长界内/
       generate 真链 assert_schema 过)
    T2 dh_oral mock 口播合规(voiceover 尾部携带理性饮酒警示
       ——防 _clip 截尾导致 compliance hardFail 回归)
    T3 DH 服务守卫(SV73_DH_MODE=off/非 dh_oral 模板/音频缺失
       → ValueError, 生产铁律)
    T4 DH mock 模式确定性产物(engine=mock+结构对齐 render.build)
    T5 推理命令模板占位符防漂移({image}/{audio}/{outdir})
    T6 pipeline 指引(dh_oral content 的 nextSteps 含 GPU 轨口径)

运行: python -m pytest test_sv73_dh.py -q
(测试全程不依赖真实 GPU/LivePortrait/LLM——确定性)
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import services.sv73_script_service as sv73mod
from services.sv73_script_service import (
    Sv73ScriptService,
    TRACK_RULE,
    assert_schema,
)
import services.sv73_digital_human_service as dhmod
from services.sv73_digital_human_service import (
    Sv73DigitalHumanService,
    DEFAULT_DH_CMD,
    dh_mode,
)
from services.sv73_pipeline_service import Sv73PipelineService

HOTSPOT = {
    "fingerprint": "fp-sv73-dh-0001",
    "platform": "douyin",
    "title": "国庆家宴白酒怎么选",
    "score": 82,
}


@pytest.fixture(autouse=True)
def _tmp_dirs(tmp_path, monkeypatch):
    """落盘隔离(storyboards/videos 不污染 backend 真目录)"""
    monkeypatch.setattr(sv73mod, "_STORYBOARD_DIR", tmp_path)
    monkeypatch.setattr(dhmod, "SV73_VIDEO_DIR", tmp_path)
    yield


def _patch_rule(monkeypatch):
    """LLM 轨确定性关闭(类级 patch)"""
    monkeypatch.setattr(
        Sv73ScriptService, "_chat_json",
        lambda self, system, user: (None, TRACK_RULE))


def _gen(template="dh_oral", **kw):
    return asyncio.run(Sv73ScriptService().generate(
        HOTSPOT, template=template, **kw))


def test_t1_dh_oral_template_registered():
    """dh_oral 注册: 单镜 15s, 3:4 竖版口径, 总时长在验收界内"""
    tpl = sv73mod.TEMPLATES["dh_oral"]
    assert tpl["scenePlan"] == ("dh_oral",)
    assert tpl["sceneDuration"] == 15.0
    assert (tpl["pageW"], tpl["pageH"]) == (1080, 1440)
    n, d, fade = 1, 15.0, sv73mod.SCENE_FADE
    total = n * d - (n - 1) * fade
    assert sv73mod.TOTAL_DURATION_BOUNDS[0] <= total <= \
        sv73mod.TOTAL_DURATION_BOUNDS[1]


def test_t1_dh_oral_generate_schema(monkeypatch):
    """generate 真链(dh_oral) → assert_schema 出口封闭全过"""
    _patch_rule(monkeypatch)
    sb = _gen()
    assert sb["template"]["name"] == "dh_oral"
    assert len(sb["scenes"]) == 1
    assert sb["scenes"][0]["role"] == "dh_oral"
    assert sb["totalDuration"] == 15.0
    assert_schema(sb)   # fail-hard: 任何失配即 AssertionError


def test_t2_dh_oral_compliance_tail(monkeypatch):
    """mock 口播尾部合规: voiceover 携带理性饮酒警示, 无 hardFail
    (40 字 _clip 截尾防护回归——警示语在尾, 截尾即 hardFail)"""
    _patch_rule(monkeypatch)
    sb = _gen()
    vo = sb["scenes"][0]["voiceover"]
    assert "理性饮酒" in vo, f"口播缺警示尾: {vo}"
    assert not sb["compliance"]["hardFail"], (
        f"dh_oral mock 口播触发 hardFail: "
        f"{sb['compliance']['hardFail']}")


def test_t3_dh_mode_default_off(monkeypatch):
    """SV73_DH_MODE 默认 off(生产铁律——同 SV73_RENDER_MODE)"""
    monkeypatch.delenv("SV73_DH_MODE", raising=False)
    assert dh_mode() == "off"


def test_t3_dh_service_guards(tmp_path, monkeypatch):
    """服务守卫三连: off 拒用/非 dh_oral 拒入/音频缺失拒跑"""
    monkeypatch.setenv("SV73_DH_MODE", "mock")
    svc = Sv73DigitalHumanService()
    wav = tmp_path / "tts.wav"
    wav.write_bytes(b"RIFF")
    # 非 dh_oral 模板
    monkeypatch.setattr(sv73mod, "TEMPLATES", sv73mod.TEMPLATES)
    sb_vertical = _gen(template="vertical")
    with pytest.raises(ValueError, match="dh_oral"):
        svc.build(sb_vertical, wav)
    # 音频缺失
    sb = _gen()
    with pytest.raises(ValueError, match="音频"):
        svc.build(sb, tmp_path / "nonexistent.wav")
    # 模式 off
    monkeypatch.setenv("SV73_DH_MODE", "off")
    with pytest.raises(ValueError, match="off"):
        svc.build(sb, wav)


def test_t4_dh_mock_build(monkeypatch):
    """mock 模式确定性产物: engine=mock, 结构对齐 render.build"""
    _patch_rule(monkeypatch)
    monkeypatch.setenv("SV73_DH_MODE", "mock")
    monkeypatch.setattr(dhmod, "DH_IMAGE",
                         Path(sv73mod.__file__).parent.parent
                         / "assets" / "ip" / "ip-square.png")
    sb = _gen()
    wav = dhmod.SV73_VIDEO_DIR / f"{sb['scriptId']}_tts.wav"
    wav.write_bytes(b"RIFF")
    built = Sv73DigitalHumanService().build(sb, wav)
    assert built["engine"] == "mock"
    assert built["scriptId"] == sb["scriptId"]
    assert built["durationSeconds"] == sb["totalDuration"] == 15.0
    assert built["video"].endswith(f"{sb['scriptId']}_dh.mp4")
    assert set(built) == {
        "scriptId", "video", "pages", "audioTrack",
        "sizeBytes", "durationSeconds", "engine", "dhImage"}


def test_t5_dh_cmd_placeholders():
    """推理命令模板占位符防漂移(四占位齐全——build 注入契约)"""
    assert "{image}" in DEFAULT_DH_CMD
    assert "{audio}" in DEFAULT_DH_CMD
    assert "{outdir}" in DEFAULT_DH_CMD
    assert "{out}" in DEFAULT_DH_CMD


def test_t6_next_steps_dh_oral(monkeypatch):
    """pipeline 指引: dh_oral content 注入 GPU 轨口径(build_dh_dev
    +影子对照); xhs 指引对齐 21 轮闭环现状"""
    _patch_rule(monkeypatch)
    sb = _gen()
    content = {"contentId": 99, "status": "pending",
               "sv73": {"template": "dh_oral"}}
    steps = Sv73PipelineService._next_steps(content, "douyin")
    assert "build_dh_dev.py" in steps
    assert "影子对照" in steps
    # 零 GPU 模板不含 GPU 口径
    sb_v = _gen(template="vertical")
    content_v = {"contentId": 98, "status": "pending",
                 "sv73": {"template": "vertical"}}
    assert "build_dh_dev" not in Sv73PipelineService._next_steps(
        content_v, "douyin")
    # xhs 指引(21 轮闭环口径)
    steps_xhs = Sv73PipelineService._next_steps(
        dict(content, sv73={"template": "dh_oral"}), "xiaohongshu")
    assert "21 轮全自动闭环" in steps_xhs
