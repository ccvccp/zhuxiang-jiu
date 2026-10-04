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


# ------------------------------------------------------------
# T7 dh_mix 多镜混合模板(2026-10-04 A1: 口播 GPU 镜+卡片 Ken Burns)
# ------------------------------------------------------------

def test_t7_dh_mix_template_registered():
    """dh_mix 注册: 三镜逐镜时长(9/5.4/4.5), 9:16 出片口径,
    总时长在验收界内"""
    tpl = sv73mod.TEMPLATES["dh_mix"]
    assert tpl["scenePlan"] == ("dh_hook", "selling", "compliance")
    assert tpl["sceneDurations"] == (9.0, 5.4, 4.5)
    assert (tpl["pageW"], tpl["pageH"]) == (1080, 1920)
    total = sum(tpl["sceneDurations"]) - 2 * sv73mod.SCENE_FADE
    assert sv73mod.TOTAL_DURATION_BOUNDS[0] <= total <= \
        sv73mod.TOTAL_DURATION_BOUNDS[1]


def test_t7_dh_mix_generate_schema(monkeypatch):
    """generate 真链(dh_mix) → 逐镜时长/快照 sceneDurations/总时长"""
    _patch_rule(monkeypatch)
    sb = _gen(template="dh_mix")
    assert sb["template"]["name"] == "dh_mix"
    assert [sc["duration"] for sc in sb["scenes"]] == [9.0, 5.4, 4.5]
    assert [sc["role"] for sc in sb["scenes"]] == [
        "dh_hook", "selling", "compliance"]
    assert sb["template"]["sceneDurations"] == [9.0, 5.4, 4.5]
    assert sb["totalDuration"] == 17.7   # 18.9 - 2*0.6
    assert_schema(sb)   # fail-hard: 任何失配即 AssertionError


def test_t7_dh_mix_compliance_and_hook(monkeypatch):
    """口播钩子 9s 档文案上限 + 合规由尾镜结构承担(hardFail 空)"""
    _patch_rule(monkeypatch)
    sb = _gen(template="dh_mix")
    hook = sb["scenes"][0]
    assert 0 < len(hook["voiceover"]) <= sv73mod.DH_HOOK_VOICEOVER_MAX
    assert not sb["compliance"]["hardFail"]
    tail = sb["scenes"][-1]
    assert tail["role"] == "compliance"
    assert tail["voiceover"] == sv73mod.COMPLIANCE_VOICEOVER


def test_t7_dh_mix_llm_cover_maps_to_hook(monkeypatch):
    """LLM cover 文案映射口播钩子(钩子语义同位)+9s 档独立截断
    (2026-10-04 A2 起 LLM 轨过 lint——样本用规范长文案验证截断)"""
    fake = {"scenes": [
        {"role": "cover", "text": "国庆家宴火了",
         "voiceover": ("家人们, 国庆家宴快到了, "
                       "聊聊选酒这件事, 一次说明白。"),
         "highlightWords": ["国庆"]},
        {"role": "selling", "text": "竹香工艺封坛",
         "voiceover": "家人们, 这瓶封坛的竹香, 是时间给的温柔。",
         "highlightWords": ["竹香"]},
    ]}
    monkeypatch.setattr(
        Sv73ScriptService, "_chat_json",
        lambda self, system, user: (fake, "glm-4-flash"))
    sb = _gen(template="dh_mix")
    hook = sb["scenes"][0]
    assert hook["text"] == "国庆家宴火了"
    assert len(hook["voiceover"]) <= sv73mod.DH_HOOK_VOICEOVER_MAX
    assert hook["highlightWords"] == ["国庆"]
    selling = sb["scenes"][1]
    assert selling["text"] == "竹香工艺封坛"


def test_t7_dh_mix_service_mock(monkeypatch):
    """DH 服务接受 dh_mix: 只出口播镜(durationSeconds=9.0≠总 17.7)"""
    _patch_rule(monkeypatch)
    monkeypatch.setenv("SV73_DH_MODE", "mock")
    monkeypatch.setattr(dhmod, "DH_IMAGE",
                         Path(sv73mod.__file__).parent.parent
                         / "assets" / "ip" / "ip-square.png")
    sb = _gen(template="dh_mix")
    wav = dhmod.SV73_VIDEO_DIR / f"{sb['scriptId']}_tts.wav"
    wav.write_bytes(b"RIFF")
    built = Sv73DigitalHumanService().build(sb, wav)
    assert built["engine"] == "mock"
    assert built["durationSeconds"] == 9.0
    assert built["durationSeconds"] != sb["totalDuration"]


def test_t7_oral_scene_guard(monkeypatch):
    """口播镜守卫: 零口播镜(vertical 剧本)→ ValueError"""
    _patch_rule(monkeypatch)
    from services.sv73_digital_human_service import _oral_scene
    sb_vertical = _gen(template="vertical")
    with pytest.raises(ValueError, match="口播镜"):
        _oral_scene(sb_vertical)


def test_t7_mix_plan_math(monkeypatch):
    """compose_mix 编排数学: 注册表计划偏移 + 口播实际时长贴偏"""
    _patch_rule(monkeypatch)
    from services.sv73_render_service import Sv73RenderService
    sb = _gen(template="dh_mix")
    plan = Sv73RenderService.mix_plan(sb)
    assert plan["durations"] == [9.0, 5.4, 4.5]
    assert plan["offsets"] == [8.4, 13.2]
    assert plan["total"] == 17.7
    # 口播实际 9.8s(TTS 物理真值)→ 偏移整体后移防截断
    plan2 = Sv73RenderService.mix_plan(sb, oral_duration=9.8)
    assert plan2["offsets"] == [9.2, 14.0]
    assert plan2["total"] == 18.5


def test_t7_next_steps_dh_mix(monkeypatch):
    """pipeline 指引: dh_mix 同获 GPU 轨口径+compose_mix 提示"""
    _patch_rule(monkeypatch)
    content = {"contentId": 97, "status": "pending",
               "sv73": {"template": "dh_mix"}}
    steps = Sv73PipelineService._next_steps(content, "douyin")
    assert "build_dh_dev.py" in steps
    assert "compose_mix" in steps
