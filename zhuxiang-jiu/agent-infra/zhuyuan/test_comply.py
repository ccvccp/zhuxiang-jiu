"""zhuyuan comply 扫描器验收测试（T1.2: B1 整改实录回放检出 100% + 当前态残留 0）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import comply as zc


def test_b1_replay_100_percent():
    """**B1 验收核心**: 微信整改实录回放——13 个实录词检出率 100%"""
    hit, miss = zc.b1_replay()
    assert miss == [], f"B1 未中词: {miss}"
    assert len(hit) == len(zc.B1_WORDS) == 13


def test_current_state_residue_as_is():
    """当前态如实: 整改后功效类已清 0; 残留 2 处已知绝对化边缘案例
    (data.js:177 国家级评酒师=新闻纪实 / :178 最好肯定=引语, med 级供人工审)"""
    violations = zc.scan_compliance()
    assert len(violations) == 2
    assert all(v["rule_id"] == "AD-ABSOLUTE" and v["risk"] == "med"
               for v in violations)
    assert all(v["file"] == "js/data.js" for v in violations)
    # 功效类(hard 实录词)当前 0 残留——整改实录已彻底清干净
    assert not any(v["rule_id"].startswith("ALC-") for v in violations)


def test_scan_fixture_violations(tmp_path):
    """构造违规文件 → 明细含 文件/行/词/类别/建议 五要素"""
    (tmp_path / "bad.html").write_text(
        "<html><body>口感醒酒快，畅饮不头疼</body></html>", encoding="utf-8")
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "bad.js").write_text(
        "var s = '富含富硒泉水，天然健康';\n", encoding="utf-8")
    (tmp_path / "js" / "modules.js").write_text(
        "expected: '醒酒快/无酒气/不头疼'\n", encoding="utf-8")  # 内部检测工具豁免
    (tmp_path / "module-test.html").write_text("畅饮尽兴", encoding="utf-8")  # 豁免页
    vs = zc.scan_compliance(repo_root=str(tmp_path))
    files = {v["file"] for v in vs}
    assert files == {"bad.html", "js/bad.js"}
    line_hit = next(v for v in vs if v["file"] == "js/bad.js")
    assert line_hit["line"] == 1 and "富硒" in line_hit["word"]
    assert all(v["suggestion"] for v in vs)


def test_cli_comply_output():
    """CLI comply: 违规计数+B1 检出率输出"""
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"), "comply"],
                       check=False, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0
    assert "违规" in r.stdout and "B1 整改实录回放" in r.stdout
    assert "100%" in r.stdout and "B1 PASS" in r.stdout
