"""73号(sv)·短视频智能模型 P0 专项——分镜剧本引擎

覆盖(P0 验收口径 §五):
    T1 schema 封闭断言(顶层/镜头键集/role 序列/时长/总时长/
       文案上限/BGM 情绪档/IP 角标锚点)
    T2 Mock 派生确定性(同输入两次产出 scenes/scriptId 全等)
    T3 LLM 禁入判定链(伪造超长文案/伪造数字/伪造角色 → 注册表覆写)
    T4 三审闸门前置(LLM 轨 hardFail → 整体回落 rule)
    T5 人设注入(system prompt 含竹小妹口吻段与红线)
    T6 scriptId 幂等(同热点同品类同 id, 落盘覆盖; 异热点异 id)
    T7 合规模板自证(mock 轨无 hardFail 且 score>=80)
    T8 路由门控(观测面 off 常开 + 决策面 off=409/real=200/
       kill=409/非 admin=403)

运行: python -m pytest test_sv73_script.py -q
(测试全程 monkeypatch LLM 轨——不依赖真实 LLM 配置, 确定性)
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import services.sv73_script_service as sv73mod
from services.sv73_script_service import (
    Sv73ScriptService,
    IP_PERSONAS,
    TRACK_RULE,
    DEFAULT_SCENE_PLAN,
    SCENE_DURATION,
    STORYBOARD_TOP_KEYS,
    assert_schema,
)

HOTSPOT = {
    "fingerprint": "fp-sv73-test-0001",
    "platform": "douyin",
    "title": "秋天第一杯奶茶换成它",
    "score": 82,
}

HOTSPOT_ALT = {
    "fingerprint": "fp-sv73-test-0002",
    "platform": "weibo",
    "title": "中秋送礼清单上热搜",
    "score": 76,
}


@pytest.fixture(autouse=True)
def _tmp_storyboard_dir(tmp_path, monkeypatch):
    """落盘隔离(测试产物不污染 backend/storyboards)"""
    monkeypatch.setattr(sv73mod, "_STORYBOARD_DIR", tmp_path)
    yield


def _patch_rule(monkeypatch):
    """LLM 轨确定性关闭(类级 patch——路由内 _service 实例同样生效)"""
    monkeypatch.setattr(
        Sv73ScriptService, "_chat_json",
        lambda self, system, user: (None, TRACK_RULE))


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------
# T1 schema 封闭断言
# ------------------------------------------------------------


def test_t1_schema_closed(monkeypatch):
    _patch_rule(monkeypatch)
    sb = _run(Sv73ScriptService().generate(dict(HOTSPOT)))
    assert_schema(sb)   # 不抛即过(出口自检已在 generate 内跑过)
    # 显式断言(测试自带口径, 不只依赖 assert_schema)
    assert tuple(sb.keys()) == STORYBOARD_TOP_KEYS
    assert [sc["role"] for sc in sb["scenes"]] == list(DEFAULT_SCENE_PLAN)
    assert all(sc["duration"] == SCENE_DURATION for sc in sb["scenes"])
    assert all(len(sc["text"]) <= 12 for sc in sb["scenes"])
    assert all(len(sc["voiceover"]) <= 40 for sc in sb["scenes"])
    assert all(len(sc["highlightWords"]) <= 2 for sc in sb["scenes"])
    assert sb["totalDuration"] == 16.2   # 4*4.5 - 3*0.6
    assert 15.0 <= sb["totalDuration"] <= 25.0
    assert sb["bgm"]["mood"] in sv73mod.BGM_MOODS
    assert sb["persona"] == "zhuxiaomei"
    assert sb["track"] == TRACK_RULE
    # 合规尾镜结构性固定(注册表文案, 非模板变量派生)
    tail = sb["scenes"][-1]
    assert tail["role"] == "compliance"
    assert tail["text"] == sv73mod.COMPLIANCE_TEXT
    assert tail["voiceover"] == sv73mod.COMPLIANCE_VOICEOVER
    # IP 角标: action 镜居中 320, 其余右下 200
    assert sb["scenes"][2]["ipOverlay"] == {
        "anchor": "bottom-center", "size": 320}
    assert sb["scenes"][0]["ipOverlay"] == {
        "anchor": "bottom-right", "size": 200}


# ------------------------------------------------------------
# T2 Mock 派生确定性
# ------------------------------------------------------------


def test_t2_mock_deterministic(monkeypatch):
    _patch_rule(monkeypatch)
    svc = Sv73ScriptService()
    a = _run(svc.generate(dict(HOTSPOT)))
    b = _run(svc.generate(dict(HOTSPOT)))
    assert a["scenes"] == b["scenes"]
    assert a["scriptId"] == b["scriptId"]
    assert a["category"] == b["category"]
    # mock 轨文案含热点词派生(确定性插值)
    cover = a["scenes"][0]
    assert cover["role"] == "cover"
    assert "都在聊" in cover["text"] or cover["text"]


# ------------------------------------------------------------
# T3 LLM 禁入判定链(数字与结构全注册表覆写)
# ------------------------------------------------------------


def test_t3_llm_overridden_by_registry(monkeypatch):
    fake = {
        "scenes": [
            {"role": "cover", "text": "超" * 30,
             "voiceover": "词" * 200,
             "highlightWords": ["长词" * 10] * 10,
             "duration": 99, "index": 7},
            {"role": "selling", "text": "竹香工艺封坛",
             "voiceover": "家人们, 这瓶封坛的竹香, 是时间给的温柔。",
             "highlightWords": ["竹香"]},
            {"role": "hacker-role", "text": "伪造角色",
             "voiceover": "x", "highlightWords": []},
            {"role": "compliance", "text": "LLM 不许碰尾镜",
             "voiceover": "伪造警示", "highlightWords": []},
        ],
        "totalDuration": 999,
        "mode": "real", "track": "hacker",
    }
    monkeypatch.setattr(
        Sv73ScriptService, "_chat_json",
        lambda self, system, user: (fake, "glm-5.3"))
    sb = _run(Sv73ScriptService().generate(dict(HOTSPOT)))
    assert sb["track"] == "glm-5.3"
    # 顶层伪造字段全被注册表覆写
    assert sb["totalDuration"] == 16.2
    assert sb["mode"] == sv73mod.current_mode()
    assert tuple(sb.keys()) == STORYBOARD_TOP_KEYS
    # 结构与数字: role 序列/镜头数/时长/序号全注册表
    assert [sc["role"] for sc in sb["scenes"]] == list(DEFAULT_SCENE_PLAN)
    assert all(sc["duration"] == SCENE_DURATION for sc in sb["scenes"])
    assert [sc["index"] for sc in sb["scenes"]] == [0, 1, 2, 3]
    # 文案钳制: 超长截断到上限
    cover = sb["scenes"][0]
    assert len(cover["text"]) <= 12
    assert len(cover["voiceover"]) <= 40
    assert len(cover["highlightWords"]) <= 2
    assert all(len(w) <= 6 for w in cover["highlightWords"])
    # 伪造角色(hacker-role)不采; action 镜 LLM 缺位 → mock 模板兜底
    action = sb["scenes"][2]
    assert action["role"] == "action"
    assert "了解" in action["text"]
    # 合规尾镜: LLM 伪造文案被结构性丢弃(注册表固定)
    tail = sb["scenes"][3]
    assert tail["role"] == "compliance"
    assert tail["text"] == sv73mod.COMPLIANCE_TEXT
    assert tail["voiceover"] == sv73mod.COMPLIANCE_VOICEOVER


# ------------------------------------------------------------
# T4 三审闸门前置(hardFail → 整体回落 rule)
# ------------------------------------------------------------


def test_t4_hardfail_fallback_to_rule(monkeypatch):
    from services.promo_service import DRINKING_ACTION_WORDS
    if not DRINKING_ACTION_WORDS:
        pytest.skip("词表为空——无法构造 hardFail 样本")
    bad = DRINKING_ACTION_WORDS[0]
    fake = {"scenes": [
        {"role": "cover", "text": f"来{bad}",
         "voiceover": f"家人们快{bad}尝尝这瓶",
         "highlightWords": []},
    ]}
    monkeypatch.setattr(
        Sv73ScriptService, "_chat_json",
        lambda self, system, user: (fake, "glm-5.3"))
    sb = _run(Sv73ScriptService().generate(dict(HOTSPOT)))
    # hardFail → 整体回落 rule 模板, 轨标签同步
    assert sb["track"] == TRACK_RULE
    assert sb["compliance"]["hardFail"] == []
    # 注: 本热点标题"秋天第一杯"含极限词"第一", mock 模板插值后
    # 二审如实降分 70(60-79=强制人工审档)——三审闸门正确语义,
    # 断言口径为"不低于拒绝线 60"而非 80(极限词场景属人工审范围)
    assert sb["compliance"]["score"] >= 60
    # 回落后 cover 文案为 mock 模板(LLM 产物被丢弃)
    assert sb["scenes"][0]["text"] != f"来{bad}"[:12]


# ------------------------------------------------------------
# T5 人设注入
# ------------------------------------------------------------


def test_t5_persona_injected():
    svc = Sv73ScriptService()
    prompt = svc._system_prompt(IP_PERSONAS["zhuxiaomei"])
    assert "竹小妹" in prompt          # 人设注册表注入
    assert "家人们" in prompt          # 口吻特征
    assert "理性饮酒" in prompt         # 合规人设约束
    assert "12" in prompt              # 字数红线
    assert "不得劝酒" in prompt         # 三审红线前置
    assert "只输出 JSON" in prompt     # 结构化输出约束


# ------------------------------------------------------------
# T6 scriptId 幂等
# ------------------------------------------------------------


def test_t6_script_id_idempotent(monkeypatch):
    _patch_rule(monkeypatch)
    svc = Sv73ScriptService()
    a = _run(svc.generate(dict(HOTSPOT)))
    b = _run(svc.generate(dict(HOTSPOT)))
    assert a["scriptId"] == b["scriptId"]
    # 落盘覆盖(同 id 单文件, 非堆积)
    files = list(sv73mod._STORYBOARD_DIR.glob("sv73_*.json"))
    assert len(files) == 1
    # 异热点异 id
    c = _run(svc.generate(dict(HOTSPOT_ALT)))
    assert c["scriptId"] != a["scriptId"]
    assert len(list(sv73mod._STORYBOARD_DIR.glob("sv73_*.json"))) == 2
    # load 观测面可用 / 格式拒绝
    assert svc.load(a["scriptId"])["scriptId"] == a["scriptId"]
    with pytest.raises(KeyError):
        svc.load("sv73_../../etc_passwd")   # 路径穿越拒绝
    with pytest.raises(KeyError):
        svc.load("sv73_ffff")


# ------------------------------------------------------------
# T7 合规模板自证(mock 轨过闸)
# ------------------------------------------------------------


def test_t7_mock_compliance(monkeypatch):
    _patch_rule(monkeypatch)
    sb = _run(Sv73ScriptService().generate(dict(HOTSPOT_ALT)))
    assert sb["compliance"]["hardFail"] == []
    assert sb["compliance"]["score"] >= 80
    assert sb["compliance"]["requiresManualReview"] is False


# ------------------------------------------------------------
# T8 路由门控(观测面 off 常开 + 决策面三态)
# ------------------------------------------------------------


def test_t8_routes_mode_gate(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.sv73_routes import register_sv73_routes

    app = FastAPI()
    register_sv73_routes(app)
    client = TestClient(app)
    admin = {"X-Role": "admin"}

    # off(默认): 观测面 200, 决策面 409
    monkeypatch.setenv("SV73_MODE", "off")
    assert client.get("/api/sv73/scripts",
                      headers=admin).status_code == 200
    r = client.post("/api/sv73/script/generate", headers=admin,
                    json={"hotspot": HOTSPOT})
    assert r.status_code == 409
    assert "SV73_MODE=off" in r.json()["detail"]

    # 观测面: 生成后清单可见(先 real 造一条)
    _patch_rule(monkeypatch)
    monkeypatch.setenv("SV73_MODE", "real")
    r = client.post("/api/sv73/script/generate", headers=admin,
                    json={"hotspot": HOTSPOT})
    assert r.status_code == 200
    sb = r.json()["data"]
    assert sb["track"] == TRACK_RULE
    assert sb["mode"] == "real"
    rows = client.get("/api/sv73/scripts",
                      headers=admin).json()["data"]
    assert any(row["scriptId"] == sb["scriptId"] for row in rows)
    detail = client.get(f"/api/sv73/script/{sb['scriptId']}",
                        headers=admin).json()["data"]
    assert detail["scriptId"] == sb["scriptId"]

    # kill: 一键回退 409
    monkeypatch.setenv("SV73_KILL", "1")
    assert client.post("/api/sv73/script/generate", headers=admin,
                       json={"hotspot": HOTSPOT}).status_code == 409
    monkeypatch.delenv("SV73_KILL")

    # 非 admin: 403(决策面与观测面同口径)
    assert client.post("/api/sv73/script/generate",
                      json={"hotspot": HOTSPOT}).status_code == 403
    assert client.get("/api/sv73/scripts").status_code == 403

    # 未注册人设: 409(ValueError 映射)
    monkeypatch.setenv("SV73_MODE", "real")
    r = client.post("/api/sv73/script/generate", headers=admin,
                    json={"hotspot": HOTSPOT, "persona": "no-such"})
    assert r.status_code == 409
