"""zhuyan evolve 进化案例草稿验收测试（T3.1: EVO 五段式+人工确认门）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import evolve as zev


def _fake_plan():
    m = {"total": 0.16, "grade": "low", "s_b": 0.36, "s_r": 0.0, "s_e": 0.40,
         "penalty": 0.0, "sb_ev": {}, "sr_ev": {"fault_features": []},
         "se_ev": {"tests": [], "test_value": 0.0, "contract": 0.5,
                   "imports": 0}, "pen_ev": {"vetoed": False}}
    return {"unit": {"unit_id": "activity-detail.html", "unit_type": "page",
                     "file": "activity-detail.html"},
            "m": m, "pains": ["无关联测试", "容错特征不足(<2 类)"],
            "ap_hits": [], "case_ref": None, "ref_seed": "seed-x",
            "strategies": [
                ("策略A 保守补强", "最小改动修痛点", "条件: 可局部修复"),
                ("策略B 参照重构", "以候选为模板", "风险: 高——须人工主导"),
                ("策略C 模式移植", "无命中不适用", "条件: 不满足")]}


def test_t31_draft_five_sections():
    """EVO 草稿: 五段 5/5+文件名 草稿-EVO- 前缀+front matter id 待定"""
    d = zev.draft_evo_case(_fake_plan(), count=2)
    assert d["sections"] == 5, "五段齐"
    assert d["filename"].startswith("草稿-EVO-")
    assert "id: 待定(人工确认时按竹演进化编号规则赋 EVO-" in d["text"]
    assert "优先进化区" in d["text"] and "只出不施" in d["text"]
    assert "同类目标共 2 个" in d["text"], "计数入现象段"
    for name in ("策略A", "策略B", "策略C"):
        assert name in d["text"], "三策略入正确写法段"


def test_t31_confirmation_gate(tmp_path):
    """确认门铁证: 草稿前缀不被竹医案例库加载; 改名+赋 EVO id 后命中五段"""
    cases_mod = zev._zhuyi_cases()
    d = zev.draft_evo_case(_fake_plan())
    draft_path = tmp_path / d["filename"]
    draft_path.write_text(d["text"], encoding="utf-8")
    loaded = cases_mod.load_cases(str(tmp_path))
    assert loaded == [], "草稿-EVO- 前缀不入库(确认门生效)"
    text = d["text"].replace(
        "id: 待定(人工确认时按竹演进化编号规则赋 EVO-20261022-NN)",
        "id: EVO-20261022-01").replace("# 草稿-EVO-", "# 案例-EVO-")
    (tmp_path / "案例-EVO-20261022-缺测试.md").write_text(
        text, encoding="utf-8")
    loaded = cases_mod.load_cases(str(tmp_path))
    assert len(loaded) == 1, "改名即入库"
    assert loaded[0]["sections"] == 5, "五段可被案例库解析"
    assert str(loaded[0].get("id", "")).startswith("EVO-")


def test_t31_pain_grouping():
    """痛点主类分组 slug 映射"""
    assert zev._pain_key("无关联测试") == "无关联测试"
    assert zev._pain_key("耦合偏高(import 15)") == "耦合偏高"
    assert zev._pain_key("未知痛点xyz") == "未知痛点xyz"


def test_t31_collect_and_cli_real():
    """真实采集+CLI evolve 分发(子进程写真 cases 目录, 差集自清理)"""
    import subprocess

    cases_dir = os.path.join(
        __import__("fingerprint").REPO_ROOT, "docs", "registry", "cases")
    before = set(os.listdir(cases_dir))
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "cli.py"), "evolve"],
            check=False, capture_output=True, text=True,
            encoding="utf-8", timeout=480)
        assert r.returncode == 0
        assert "进化案例草稿" in r.stdout and "五段" in r.stdout
        assert "入库走人工确认" in r.stdout and "EVO" in r.stdout
        drafts = set(os.listdir(cases_dir)) - before
        assert any(n.startswith("草稿-EVO-") for n in drafts), "EVO 草稿落盘"
        assert any("5/5" in ln for ln in r.stdout.splitlines()), "五段齐自检"
    finally:
        for n in set(os.listdir(cases_dir)) - before:
            os.remove(os.path.join(cases_dir, n))
