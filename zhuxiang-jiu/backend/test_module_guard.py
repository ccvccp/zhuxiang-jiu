"""模块保护引擎单测(语音链 48号 首个注册者)

运行: python -m pytest test_module_guard.py -q  (cwd=zhuxiang-jiu/backend)
"""
import sys
from pathlib import Path

import core.module_guard as mg

# git 仓库根(d:\网站架构设计——backend 的上两级)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_registry_and_clean_path():
    """注册制在位 + 无关变更零负担放行"""
    assert "voice48" in mg.PROTECTED_MODULES
    cfg = mg.PROTECTED_MODULES["voice48"]
    assert ("zhuxiang-jiu/backend/services/asr_stream_service.py"
            in cfg["guard_files"])
    # 无关变更: 干净放行
    r = mg.check_changes(["docs/readme.md",
                          "backend/services/other_service.py"])
    assert r["clean"] is True and r["hits"] == {}


def test_hit_detection_exact_files():
    """守卫文件命中判定(语音核心文件逐一命中, git 根相对路径)"""
    r = mg.check_changes([
        "zhuxiang-jiu/backend/routes/drama76_routes.py",      # 非守卫
        "zhuxiang-jiu/backend/services/asr_stream_service.py",  # 守卫
        "taro-app/public/xiaozhu-voice.html",      # 守卫
    ])
    assert not r["clean"]
    # 82号入册后面板文件交叉命中双模块(voice48+voice82 都要过套件)
    assert set(r["hits"]) == {"voice48", "voice82"}
    assert len(r["hits"]["voice48"]) == 2
    assert len(r["hits"]["voice82"]) == 1   # 面板(asr_stream 不在 82清单)


def test_param_fingerprint_stability_and_drift():
    """参数指纹: 基线稳定/漂移检测(不改真基线——内存级探针验证)"""
    probe = {"services.asr_stream_service:_PACING_MAX_RATE":
             "2.0"}
    fp1 = mg._fingerprint(probe)
    assert fp1 == mg._fingerprint(dict(probe))     # 同参同指纹
    probe2 = dict(probe)
    probe2["services.asr_stream_service:_PACING_MAX_RATE"] = "99.0"
    assert mg._fingerprint(probe2) != fp1           # 漂移可检出


def test_baseline_roundtrip(tmp_path, monkeypatch):
    """基线落盘/比对/重置(隔离 BASELINE_DIR 不污染真基线)"""
    monkeypatch.setattr(mg, "BASELINE_DIR", tmp_path)
    # 首跑=生成基线
    b1 = mg.check_baseline("voice48", REPO_ROOT)
    assert b1["status"] == "rebaselined"
    assert (tmp_path / "voice48.json").is_file()
    # 二跑=稳定(真参数未变)
    b2 = mg.check_baseline("voice48", REPO_ROOT)
    assert b2["status"] == "stable"
    assert b2["fingerprint"] == b1["fingerprint"]
    # 篡改基线=漂移可检出
    bp = tmp_path / "voice48.json"
    bp.write_text('{"fingerprint": "deadbeef", "params": {}}',
                  encoding="utf-8")
    b3 = mg.check_baseline("voice48", REPO_ROOT)
    assert b3["status"] == "drifted"


def test_static_asserts_in_place():
    """前端静态守卫: 当前生产代码关键标识符全在位"""
    flaws = mg.check_static_asserts("voice48", REPO_ROOT)
    assert flaws == [], f"语音前端守卫破损: {flaws}"


def test_full_guard_pass_on_current_tree():
    """全链预检: 命中语音守卫(传守卫文件)→套件绿+静态过→放行"""
    r = mg.full_guard(
        ["zhuxiang-jiu/backend/services/asr_stream_service.py"],
        REPO_ROOT)
    # 套件真实跑(test_asr_stream 5 用例)+静态断言+指纹
    assert r["verdict"] == "pass", r
    assert r["suite_ok"] is True
