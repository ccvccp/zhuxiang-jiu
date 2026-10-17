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
             # (2026-10-04 A2 起 LLM 轨过 lint——超长截断样本改用
             # 规范长文案, 保留"LLM 文案被采"的原断言口径)
             "voiceover": ("家人们, 这瓶竹香酒是时间给的味道, "
                           "口感层次分明, 回味悠长。" * 2),
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


# ------------------------------------------------------------
# T9 分级生成路由(2026-10-04: 热点分值→B/A/S 模板)
# ------------------------------------------------------------

def test_t9_resolve_tier_template():
    """分值三档边界: ≥80→S(dh_mix) / ≥60→A(dh_oral) / <60→B(vertical)"""
    from services.sv73_script_service import resolve_tier_template
    assert resolve_tier_template({"score": 92}) == ("dh_mix", "S")
    assert resolve_tier_template({"score": 80}) == ("dh_mix", "S")
    assert resolve_tier_template({"score": 79.9}) == ("dh_oral", "A")
    assert resolve_tier_template({"score": 60}) == ("dh_oral", "A")
    assert resolve_tier_template({"score": 59}) == ("vertical", "B")
    assert resolve_tier_template({}) == ("vertical", "B")  # 缺省 0


# ------------------------------------------------------------
# T10 文案质量护栏(A2: 瑕疵词/单句长/叠字, LLM 轨重生成+回落)
# ------------------------------------------------------------

def test_t10_lint_texts_rules():
    """lint 三规则命中 + 干净文案零违规"""
    from services.sv73_script_service import lint_texts
    assert any("好火" in f for f in lint_texts(
        [{"role": "cover", "text": "x", "voiceover": "这个好火呀"}]))
    assert any("单句" in f for f in lint_texts(
        [{"role": "selling", "text": "x",
          "voiceover": "今天给大家带来一款特别好的酒" + "字" * 15}]))
    assert any("叠字" in f for f in lint_texts(
        [{"role": "cover", "text": "x", "voiceover": "家人们看看看"}]))
    assert lint_texts([{"role": "cover", "text": "竹香酒",
                        "voiceover": "家人们, 这瓶竹香酒很棒。"}]) == []


def test_t10_lint_retry_then_fallback(monkeypatch):
    """LLM 带瑕疵词 → 重生成一次(仍瑕疵) → 回落 rule(文案 mock 化)"""
    bad = {"scenes": [
        {"role": "cover", "text": "国庆好火",
         "voiceover": "国庆家宴好火, 家人们快看呀。",
         "highlightWords": []},
        {"role": "selling", "text": "竹香工艺",
         "voiceover": "这瓶竹香酒好火, 口感很棒。",
         "highlightWords": []},
    ]}
    calls = {"n": 0}

    def fake_chat(self, system, user):
        calls["n"] += 1
        return (bad, "glm-4-flash")

    monkeypatch.setattr(Sv73ScriptService, "_chat_json", fake_chat)
    sb = _run(Sv73ScriptService().generate(dict(HOTSPOT)))
    assert calls["n"] == 2            # 重生成恰好一次
    assert sb["track"] == TRACK_RULE  # 仍瑕疵 → rule 回落
    assert "好火" not in sb["scenes"][0]["voiceover"]


def test_t10_lint_retry_success(monkeypatch):
    """首答带瑕疵 → 重答干净 → 保留 LLM 轨"""
    bad = {"scenes": [{"role": "cover", "text": "国庆好火",
                       "voiceover": "国庆家宴好火呀。",
                       "highlightWords": []}]}
    good = {"scenes": [
        {"role": "cover", "text": "国庆家宴",
         "voiceover": "家人们, 国庆家宴备好酒。",
         "highlightWords": []},
        {"role": "selling", "text": "竹香工艺",
         "voiceover": "这瓶竹香酒, 口感柔和有层次。",
         "highlightWords": []}]}
    seq = [bad, good]
    monkeypatch.setattr(
        Sv73ScriptService, "_chat_json",
        lambda self, s, u: (seq.pop(0), "glm-4-flash"))
    sb = _run(Sv73ScriptService().generate(dict(HOTSPOT)))
    assert sb["track"] == "glm-4-flash"
    assert sb["scenes"][0]["voiceover"] == "家人们, 国庆家宴备好酒。"


def test_t10_rule_track_lint_clean(monkeypatch):
    """rule 轨注册表文案自身 lint-clean(mock '好火'同源清理回归)"""
    from services.sv73_script_service import lint_texts
    _patch_rule(monkeypatch)
    for tpl in ("vertical", "dh_oral", "dh_mix", "fast", "drama_monologue"):
        sb = _run(Sv73ScriptService().generate(
            dict(HOTSPOT), template=tpl))
        assert lint_texts(sb["scenes"]) == [], tpl


# ------------------------------------------------------------
# T11 drama_monologue 独白短剧轨(P0+P1 2026-10-05):
#     三段结构(压抑→转折→爆发)+lint_drama 钩子复核+合成链复用
# ------------------------------------------------------------


def test_t11_drama_registry_and_rule_track(monkeypatch):
    _patch_rule(monkeypatch)
    reg = sv73mod.TEMPLATES["drama_monologue"]
    assert reg["scenePlan"] == ("drama_oral", "compliance")
    assert reg["sceneDurations"] == (18.0, 4.5)
    sb = _run(Sv73ScriptService().generate(
        {**HOTSPOT, "fingerprint": "fp-sv73-drama-01"},
        template="drama_monologue"))
    assert_schema(sb)
    assert sb["totalDuration"] == 21.9   # 18+4.5-0.6(SCENE_FADE)
    vo = sb["scenes"][0]["voiceover"]
    assert len(vo) <= sv73mod.DRAMA_VOICEOVER_MAX
    # rule 轨注册表文案 lint_drama 自证清洁
    assert sv73mod.lint_drama(sb["scenes"]) == []


def test_t14_drama_entertainment_caliber():
    """短剧娱乐片口径(2026-10-06 用户定案): 短剧=合规娱乐片非酒
    广告——产品钩子断言撤销, 无竹/酒的纯剧情独白可过审;
    广告法专属要求(产品必现)仅适用于广告位内容, 不套用 drama 轨"""
    # cid76 形态(结构全过但无产品提及): 娱乐片口径下合格
    dud = [{"role": "drama_oral",
            "voiceover": "三年来非遗馆门可罗雀，可今天，终于火了！"}]
    assert sv73mod.lint_drama(dud) == []
    # 带产品的独白同样合格(可选元素)
    ok = [{"role": "drama_oral",
           "voiceover": ("三年来非遗馆门可罗雀，可今天，终于火了！"
                         "这热潮，竟是从一杯竹香酒开始的。")}]
    assert sv73mod.lint_drama(ok) == []
    # 产品钩子断言已撤销(常量退场)
    assert not hasattr(sv73mod, "DRAMA_PRODUCT_WORDS")
    # 结构断言保留(娱乐片质量门): 无转折词仍打回
    bad = [{"role": "drama_oral",
            "voiceover": "今天天气很好。我们出门散步。心情不错。"}]
    flaws = sv73mod.lint_drama(bad)
    assert any("转折词" in f for f in flaws)
    # prompt 口径: 可选产品 + 娱乐定位, 不再含硬条件
    prompt = Sv73ScriptService()._system_prompt(
        sv73mod.IP_PERSONAS["zhuxiaomei"], "drama_monologue")
    assert "产品元素(竹香酒)为可选" in prompt
    assert "不是产品广告" in prompt
    assert "必须出现「竹香酒」" not in prompt
    # 极端反例: 三项结构断言全打回(无转折/无爆发/句段<3)
    worst = [{"role": "drama_oral", "voiceover": "今天聊酒"}]
    assert len(sv73mod.lint_drama(worst)) == 3


def test_t11b_drama_mix_plan_reuse():
    """mix_plan 通用 xfade 编排——drama 复用 dh_mix 合成链"""
    from services.sv73_render_service import Sv73RenderService
    sb = {"scenes": [{"duration": 18.0}, {"duration": 4.5}],
          "template": {"fade": sv73mod.SCENE_FADE}}
    plan = Sv73RenderService.mix_plan(sb, oral_duration=19.2)
    assert plan["durations"] == [19.2, 4.5]
    assert plan["offsets"] == [round(19.2 - sv73mod.SCENE_FADE, 2)]
    assert plan["total"] == round(
        19.2 + 4.5 - sv73mod.SCENE_FADE, 2)


# ------------------------------------------------------------
# T12 第二人设 xiaozhushuaige(2026-10-05): 注册表+双模板组合+
#     TTS 音色映射(IP_VOICE 派生)+ dh_batch 基准图路由
# ------------------------------------------------------------


def test_t12_persona_xiaozhushuaige(monkeypatch):
    _patch_rule(monkeypatch)
    assert "xiaozhushuaige" in sv73mod.IP_PERSONAS
    p = sv73mod.IP_PERSONAS["xiaozhushuaige"]
    assert p["voice"] == "xiaochen"   # 小陈·沉稳男声(joyvoice 枚举)
    # vertical + 新人设
    sb = _run(Sv73ScriptService().generate(
        {**HOTSPOT, "fingerprint": "fp-sv73-p12"},
        persona="xiaozhushuaige"))
    assert_schema(sb)
    assert sb["persona"] == "xiaozhushuaige"
    # drama + 新人设(独白短剧兄弟们口吻)
    sb2 = _run(Sv73ScriptService().generate(
        {**HOTSPOT, "fingerprint": "fp-sv73-p12b"},
        persona="xiaozhushuaige", template="drama_monologue"))
    assert_schema(sb2)
    assert sb2["persona"] == "xiaozhushuaige"


def test_t12b_persona_voice_and_image_routing():
    """TTS 音色派生(IP_VOICE)+ dh_batch 基准图路由(本端纯函数)"""
    from services.sv73_render_service import IP_VOICE
    assert IP_VOICE["xiaozhushuaige"] == "xiaochen"
    assert IP_VOICE["zhuxiaomei"] == "tongtong"
    import dh_batch
    assert dh_batch.dh_image_for(
        "xiaozhushuaige") == "/root/assets/ip/xiaozhushuaige_front.jpg"
    assert dh_batch.dh_image_for(
        "zhuxiaomei") == "/root/assets/ip/zhuxiaomei_front.jpg"
    # 未注册人设回落竹小妹(防呆)
    assert dh_batch.dh_image_for(
        "unknown") == "/root/assets/ip/zhuxiaomei_front.jpg"
    # 本地素材在位(跑批前置)
    assert Path(dh_batch.DH_IMAGES["xiaozhushuaige"][0]).is_file()


# ------------------------------------------------------------
# T12c/T12d 网站角色数字人形象库(2026-10-06 P0): 6 人设注册+素材在位+
#     儿童出片合规闸(广告法§40)+ dh 基准图路由中老年四角
# ------------------------------------------------------------

_ROLE_IDS = {
    "role_zhongnian_f": ("竹林惠姐", "female", True),
    "role_zhongnian_m": ("竹林大哥", "male", True),
    "role_laonian_f": ("竹林奶奶", "jam", True),
    "role_laonian_m": ("竹林爷爷", "luodo", True),
    "role_tong_f": ("竹芽妹妹", "chuichui", False),
    "role_tong_m": ("竹笋弟弟", "kazi", False),
    # 2026-10-17 青年双角色(成年形象用户确认, default+alt 两档)
    "role_qingnian_m": ("竹林才俊", "xiaochen", True),
    "role_qingnian_f": ("竹林佳人", "tongtong", True),
}


def test_t12c_roles_registry_and_assets():
    import dh_batch
    from services.sv73_render_service import IP_VOICE
    for rid, (name, voice, allow) in _ROLE_IDS.items():
        p = sv73mod.IP_PERSONAS[rid]
        assert p["name"] == name
        assert p["voice"] == voice and IP_VOICE[rid] == voice
        assert p["allow_alcohol"] is allow
        # 两套装扮素材在位 + prompt 带合规红线
        for outfit in p["outfits"].values():
            assert (Path(__file__).resolve().parent / outfit).is_file(), \
                f"素材缺失: {outfit}"
        assert ("理性饮酒" in p["personaPrompt"]
                if allow else "从不提及任何酒类" in p["personaPrompt"])
    # dh 基准图: 中老年四角+青年双角注册(儿童不入出片链)
    for rid in ("role_zhongnian_f", "role_zhongnian_m",
                "role_laonian_f", "role_laonian_m",
                "role_qingnian_m", "role_qingnian_f"):
        assert dh_batch.dh_image_for(rid) == \
            f"/root/assets/ip/roles/{rid}.jpg"
        assert Path(dh_batch.DH_IMAGES[rid][0]).is_file()
        assert "role_tong" not in dh_batch.DH_IMAGES


def test_t12d_roles_alcohol_gate(monkeypatch):
    _patch_rule(monkeypatch)
    # 中老年: 正常出片(口吻注入生效)
    sb = _run(Sv73ScriptService().generate(
        {**HOTSPOT, "fingerprint": "fp-roles-1"},
        persona="role_laonian_m", template="drama_monologue"))
    assert_schema(sb)
    assert sb["persona"] == "role_laonian_m"
    # 儿童: 全模板拒绝(广告法§40 出片闸)
    for tpl in ("vertical", "dh_oral", "drama_monologue"):
        try:
            _run(Sv73ScriptService().generate(
                {**HOTSPOT, "fingerprint": "fp-roles-gate"},
                persona="role_tong_f", template=tpl))
            raise AssertionError(f"儿童人设 {tpl} 应被出片闸拒绝")
        except ValueError as exc:
            assert "酒类" in str(exc)


# ------------------------------------------------------------
# T13 drama LLM 三段拆分拼接(2026-10-05 实证: flash 把三段独白
#     拆成多个 cover 场景返回——只取首个=纯铺垫段必败 lint)
# ------------------------------------------------------------


def test_t13_drama_cover_segments_join(monkeypatch):
    fake = {"scenes": [
        {"role": "cover", "text": "中秋独饮",
         "voiceover": "三年来中秋都是独饮，家人总说我太孤僻。",
         "highlightWords": ["孤僻"]},
        {"role": "cover", "text": "变局来了",
         "voiceover": "可是今年终于摆上了竹香酒。",
         "highlightWords": ["竹香酒"]},
        {"role": "cover", "text": "敬团圆",
         "voiceover": "这团圆宴，赢回来！",
         "highlightWords": ["赢回来"]},
    ]}
    monkeypatch.setattr(
        Sv73ScriptService, "_chat_json",
        lambda self, s, u: (fake, "glm-4-flash"))
    sb = _run(Sv73ScriptService().generate(
        {**HOTSPOT, "fingerprint": "fp-sv73-t13"},
        persona="xiaozhushuaige", template="drama_monologue"))
    assert sb["track"] == "glm-4-flash"    # 拼接后 lint 通过不再回落
    vo = sb["scenes"][0]["voiceover"]
    # 拼接保住转折词与爆发收束
    assert "终于" in vo and ("！" in vo or "!" in vo)
    assert sv73mod.lint_drama(sb["scenes"]) == []
