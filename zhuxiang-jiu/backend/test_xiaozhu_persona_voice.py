"""P2·小竹蓝图研判两件套测试: 动态 Persona 引擎最小版 + 情绪→音色路由

覆盖(《小竹AgentAI升级蓝图与现有实现对照分析》第五节 P2 第 1/2 条):
    ① _persona_line 三因子组装(L5 全形态 / L1 最简 / 全空 / fail-soft)
    ② persona 行注入 LLM 轨 context_desc(mock classify 捕获断言)
    ③ turn 留痕 personaLine 落盘(全链 handle_text 规则轨兜底组装)
    ④ XIAOZHU_PERSONA_MODE=off 不注入不留痕(保留关闭路径)
    ⑤ MOOD_VOICE 三档映射(档名都在六档档案内) + 模式闸默认 observe
    ⑥ observe 态 wouldVoice 留痕(care→映射音色名) 且实际合成
       voice 参数未变(打桩 synthesize 对比)
    ⑦ enforce 态合成链 voice 参数实切映射音色(缓存键随音色同步)
    ⑧ XIAOZHU_JOYVOICE_VOICE_MODE=off 无 wouldVoice 零痕迹

pytest 形态(pytest.ini asyncio_mode=auto); env 口径对齐
test_xiaozhu_joyvoice.py 脚本族(XIAOZHU_MODE=assist / LLM 双
off / JOYVOICE on), 在 import services 之前设置。
"""
import asyncio
import os
import re
from unittest.mock import MagicMock, patch

# 与 xiaozhu 测试族同口径(conftest 已设 LOCK_MODE/STORE_MODE=asyncio)
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_JOYVOICE_MODE"] = "on"
# P2 两开关显式回默认(防外部环境污染断言口径)
os.environ.pop("XIAOZHU_PERSONA_MODE", None)
os.environ.pop("XIAOZHU_JOYVOICE_VOICE_MODE", None)

from services import joyvoice_service as jv  # noqa: E402
from services.xiaozhu_service import (  # noqa: E402
    XiaozhuService, _persona_line,
)


class _FakeRedis:
    """对齐 test_xiaozhu_joyvoice.py [14] 的预合成打桩桩体"""

    def __init__(self):
        self.store, self.set_calls = {}, []

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v, ex=None):
        self.set_calls.append(k)
        self.store[k] = v


async def _preheat_capture(svc, turn):
    """打桩预合成链跑一轮, 返回 (fake_redis, synthesize_mock)

    mock 形态与既有 joyvoice [14] 矩阵一致(is_redis_mode 强开
    + FakeRedis + synthesize_mp3 打桩)。
    """
    fake = _FakeRedis()
    ms = MagicMock(return_value=b"ID3FAKE")
    with patch("repositories.backend.is_redis_mode",
               return_value=True), \
         patch("repositories.backend.get_redis_client",
               return_value=fake), \
         patch("services.llm_client.provider_client"
               ".synthesize_mp3", ms):
        await svc._preheat_tts_first_chunk(dict(turn))
    return fake, ms


# ============================================================
# 目标一: 动态 Persona 引擎最小版
# ============================================================

def test_persona_line_full_form():
    """① L5+偏好+negative → 三段俱全(蓝图示例逐字形态)"""
    ctx = {"level": 5, "levelTitle": "竹海贵宾",
           "preferenceTags": ["竹筒酒", "礼盒", "酱香"]}
    assert _persona_line(ctx, "negative") == (
        "对话对象:竹海贵宾(L5),偏好:竹筒酒/礼盒/酱香,"
        "情绪:负面(先关怀再办事)")


def test_persona_line_minimal_l1():
    """① L1 无称号降级「会员」+空偏好省略+neutral → 最简形态"""
    ctx = {"level": 1, "levelTitle": "",
           "preferenceTags": []}
    assert _persona_line(ctx, "neutral") == (
        "对话对象:会员(L1),情绪:中性(温和引导)")


def test_persona_line_all_empty():
    """① ctx 全空+mood 空 → 空串(空串不注入 context_desc)"""
    assert _persona_line({}, "") == ""
    assert _persona_line(None, "") == ""
    assert _persona_line(None, None) == ""


def test_persona_line_segments_and_failsoft():
    """① 犹豫/积极段文案; 偏好截断前 3; fail-soft 缺字段逐段降级"""
    ctx = {"level": 3, "levelTitle": "竹林会员",
           "preferenceTags": ["a", "b", "c", "d"]}
    assert "情绪:犹豫(主动帮挑降低犹豫)" \
        in _persona_line(ctx, "hesitant")
    assert "情绪:积极(轻快互动)" \
        in _persona_line(ctx, "positive")
    # 偏好只取前 3(与 build_context most_common(3) 同上界)
    _l = _persona_line(ctx, "neutral")
    assert "偏好:a/b/c" in _l and ",d" not in _l
    # fail-soft: ctx 缺 level 字段 → 等级段省略只余情绪段
    assert _persona_line({"preferenceTags": []},
                         "negative") == "情绪:负面(先关怀再办事)"
    # fail-soft: level 越界(非 1-5 整数)省略等级段不崩
    assert _persona_line({"level": 9, "levelTitle": "x"},
                         "neutral") == "情绪:中性(温和引导)"


async def test_persona_injected_into_llm_context(monkeypatch):
    """② persona 行注入 LLM 轨 context_desc

    XIAOZHU_LLM_MODE 默认 off 时 context_desc 无任何消费方
    (规则轨零行为变化)——本用例开 LLM 轨 + mock classify 捕获
    第二参, 实证注入逻辑真实生效。
    """
    monkeypatch.setenv("XIAOZHU_LLM_MODE", "on")
    svc = XiaozhuService()
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    session = await svc._require_open(sid)
    cap = {}

    def _fake_classify(text, context_desc, actions,
                       model=None):
        cap["ctx"] = context_desc
        return {"intent": "chat", "reply": None}

    with patch("services.llm_client.provider_client"
               ".classify_dialog_intent",
               side_effect=_fake_classify):
        await svc._llm_dialog_intent(
            session, "你们这系统真垃圾")
    await asyncio.sleep(0.05)   # 消化 fire-and-forget 残留 task
    await svc.delete_session(sid)
    # 等级段形态(称号或会员降级 + L 括注) + 负面情绪段注入
    assert re.search(r"对话对象:.+\(L[1-5]\)",
                     cap["ctx"]), cap["ctx"]
    assert "情绪:负面(先关怀再办事)" in cap["ctx"]
    # 组装结果已暂存(_save_turn 复用防双查——LLM 轨 on 时
    # 本轮仅组装一次)
    assert "对话对象:" in (svc._turn_persona_cached or "")


async def test_turn_persona_line_persisted():
    """③ 全链轮次 personaLine 落盘(规则轨 _save_turn 兜底组装)"""
    svc = XiaozhuService()
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid, "小竹，看新品")
    await asyncio.sleep(0.05)   # 消化 fire-and-forget 残留 task
    await svc.delete_session(sid)
    line = (r.get("turn") or {}).get("personaLine")
    # member 1 测试库无等级/订单数据 → L1 降级+空偏好; 话语
    # 中性 → 情绪中性段(等级段宽松断言防种子数据差异)
    assert isinstance(line, str) and line
    assert line.startswith("对话对象:")
    assert "情绪:中性(温和引导)" in line


async def test_persona_mode_off(monkeypatch):
    """④ PERSONA_MODE=off: context_desc 不注入且 personaLine 空"""
    monkeypatch.setenv("XIAOZHU_PERSONA_MODE", "off")
    # a. 全链留痕为空串
    svc = XiaozhuService()
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid, "小竹，看新品")
    await asyncio.sleep(0.05)
    await svc.delete_session(sid)
    assert (r.get("turn") or {}).get("personaLine") == ""
    # b. LLM 轨注入路径 off 不注入(mock classify 捕获)
    monkeypatch.setenv("XIAOZHU_LLM_MODE", "on")
    svc2 = XiaozhuService()
    sid2 = (await svc2.open_session(1, "voice"))["sessionId"]
    session = await svc2._require_open(sid2)
    cap = {}

    def _fake_classify(text, context_desc, actions,
                       model=None):
        cap["ctx"] = context_desc
        return {"intent": "chat", "reply": None}

    with patch("services.llm_client.provider_client"
               ".classify_dialog_intent",
               side_effect=_fake_classify):
        await svc2._llm_dialog_intent(session, "看新品")
    await asyncio.sleep(0.05)
    await svc2.delete_session(sid2)
    assert "对话对象:" not in cap["ctx"]
    assert "情绪:" not in cap["ctx"]


# ============================================================
# 目标二: 情绪→音色路由(observe 留痕先行)
# ============================================================

def test_mood_voice_mapping():
    """⑤ MOOD_VOICE 三档映射: 值都在六档档案档名内; 边界不映射"""
    assert jv.MOOD_VOICE == {"care": "chuichui",
                             "steady": "tongtong",
                             "cheerful": "luodo"}
    ids = {p["id"] for p in jv.JOYVOICE_PROFILES}
    assert set(jv.MOOD_VOICE.values()) <= ids
    # 键域与语速档 MOOD_SPEED 对齐(同一套播报 mood 标签)
    assert set(jv.MOOD_VOICE) == set(jv.MOOD_SPEED)
    assert jv.mood_voice("care") == "chuichui"
    assert jv.mood_voice("steady") == "tongtong"
    assert jv.mood_voice("cheerful") == "luodo"
    assert jv.mood_voice("") == ""       # 执行轮(mood="")不映射
    assert jv.mood_voice("xxx") == ""    # 未知 mood 不映射


def test_voice_route_mode_default(monkeypatch):
    """⑤ 模式闸默认 observe(零行为上线); 显式三态透传"""
    monkeypatch.delenv("XIAOZHU_JOYVOICE_VOICE_MODE",
                       raising=False)
    assert jv.voice_route_mode() == "observe"
    monkeypatch.setenv("XIAOZHU_JOYVOICE_VOICE_MODE",
                       "enforce")
    assert jv.voice_route_mode() == "enforce"
    monkeypatch.setenv("XIAOZHU_JOYVOICE_VOICE_MODE", "off")
    assert jv.voice_route_mode() == "off"


async def test_observe_wouldvoice_and_voice_unchanged(
        monkeypatch):
    """⑥ observe 态: wouldVoice 留痕且实际合成音色不变

    a/b 全链: 负面轮 mood=care → wouldVoice=映射音色名(响应体
    与 turn 双落); 执行轮 mood="" → wouldVoice 空串(字段在);
    c 打桩: observe 态预合成 voice 维持 TTS_VOICE 默认未实切。
    """
    monkeypatch.delenv("XIAOZHU_JOYVOICE_VOICE_MODE",
                       raising=False)   # 默认 observe
    # a. 全链负面轮: mood=care → wouldVoice 留痕
    svc = XiaozhuService()
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid, "小竹，你们这系统真垃圾")
    await asyncio.sleep(0.05)
    await svc.delete_session(sid)
    assert r.get("mood") == "care"
    assert r.get("wouldVoice") == "chuichui"
    assert (r.get("turn") or {}).get("wouldVoice") \
        == "chuichui"
    # b. 执行轮 mood="" → wouldVoice 空串(字段在, 值空)
    svc2 = XiaozhuService()
    sid2 = (await svc2.open_session(1, "voice"))["sessionId"]
    await svc2.handle_text(sid2, "小竹，看新品")
    r2 = await svc2.handle_text(sid2, "需要")
    await asyncio.sleep(0.05)
    await svc2.delete_session(sid2)
    assert r2.get("mood") == ""
    assert r2.get("wouldVoice") == ""
    # c. observe 态预合成: voice 参数未实切(维持 TTS_VOICE)
    monkeypatch.setenv("TTS_VOICE", "xiaochen")
    fake, ms = await _preheat_capture(
        svc, {"intent": "general", "mood": "care",
              "reply": "您别着急，我记下了。"})
    assert ms.called
    # synthesize_mp3(text, speed, voice) 第三参=默认音色
    assert ms.call_args[0][2] == "xiaochen"
    # 缓存键也按默认音色写(observe 与前端默认请求同键)
    assert jv.tts_cache_key("您别着急，我记下了。",
                            "xiaochen", 0.92) in fake.set_calls


async def test_enforce_voice_applied(monkeypatch):
    """⑦ enforce 态: 预合成链 voice 实切映射音色

    TTS_VOICE 设 xiaochen 与映射值区分——care→chuichui /
    steady→tongtong 证明映射命中(非回落), mood="" 执行轮
    回落 env 默认; 缓存键随音色同步(tts_cache_key 已含
    voice 维度, 无需改键构造)。
    """
    monkeypatch.setenv("XIAOZHU_JOYVOICE_VOICE_MODE",
                       "enforce")
    monkeypatch.setenv("TTS_VOICE", "xiaochen")
    svc = XiaozhuService()
    # a. care → chuichui 实切 + 键同步
    fake, ms = await _preheat_capture(
        svc, {"intent": "general", "mood": "care",
              "reply": "您别着急，我记下了。"})
    assert ms.call_args[0][2] == "chuichui"
    assert jv.tts_cache_key("您别着急，我记下了。",
                            "chuichui", 0.92) in fake.set_calls
    # b. steady → tongtong(映射默认档; env 默认 xiaochen
    #    区分出这是映射命中而非回落)
    fake, ms = await _preheat_capture(
        svc, {"intent": "wine.verify", "mood": "steady",
              "reply": "这款酒的故事说来话长。"})
    assert ms.call_args[0][2] == "tongtong"
    assert jv.tts_cache_key("这款酒的故事说来话长。",
                            "tongtong", 0.95) in fake.set_calls
    # c. 执行轮 mood="" → 映射空回落 TTS_VOICE 默认
    fake, ms = await _preheat_capture(
        svc, {"intent": "cart.add", "mood": "",
              "reply": "已加购物车，还需要别的吗。"})
    assert ms.call_args[0][2] == "xiaochen"
    assert jv.tts_cache_key("已加购物车，还需要别的吗。",
                            "xiaochen", 1.0) in fake.set_calls


async def test_voice_mode_off_no_wouldvoice(monkeypatch):
    """⑧ VOICE_MODE=off: 响应体与 turn 均无 wouldVoice(零痕迹)"""
    monkeypatch.setenv("XIAOZHU_JOYVOICE_VOICE_MODE", "off")
    svc = XiaozhuService()
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid, "小竹，你们这系统真垃圾")
    await asyncio.sleep(0.05)
    await svc.delete_session(sid)
    assert r.get("mood") == "care"   # 情绪标签体系不受影响
    assert "wouldVoice" not in r
    assert "wouldVoice" not in (r.get("turn") or {})
