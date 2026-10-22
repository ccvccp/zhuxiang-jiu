"""zhuyuan evolve 案例草稿验收测试（T3.2: 构造发现→案例草稿五段齐）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import evolve as zev

_COMPLY_GAP = {"file": "js/x.js", "line": 3, "word": "醒酒", "rule_id": "ALC-FUNC",
              "category": "功效宣称", "risk": "high", "suggestion": "替换口感词"}
_AUDIT_GAP = {"asset_id": "y.html", "asset_type": "page", "status": "missing",
              "gap_type": "seo_missing", "priority": "P1",
              "evidence": "无 meta description"}


def test_t32_draft_five_sections():
    """验收核心: 构造发现 → 草稿五段齐+front matter+草稿前缀"""
    d = zev.draft_case(_COMPLY_GAP)
    assert d["sections"] == 5
    for s in ("## 一、现象", "## 二、根因", "## 三、错误代码/配置",
              "## 四、正确写法", "## 五、预防规则"):
        assert s in d["text"]
    assert d["filename"].startswith("草稿-") and d["severity"] == "P0"
    assert "id: 待定" in d["text"], "id 人工确认时才赋号"
    assert "related_files" in d["text"]
    assert "替换口感词" in d["text"], "四段含处方建议(只出不施)"
    assert "B1" in d["text"], "五段预防规则含词表拦截口径"


def test_t32_draft_group_count_and_audit_shape():
    """盘点形态草稿: 同类计数入现象段+severity 沿用缺口级别"""
    d = zev.draft_case(_AUDIT_GAP, count=31)
    assert "同类发现共 31 处" in d["text"]
    assert "meta description" in d["text"]
    assert d["severity"] == "P1" and "SEO描述缺失" in d["filename"]


def test_t32_draft_not_loaded_until_confirmed(tmp_path):
    """入库走人工确认铁证: 草稿- 不被 load_cases 加载; 改名后才可检索"""
    import prescribe as zpr
    cases_mod = zpr._zhuyi_cases()
    d = zev.draft_case(_COMPLY_GAP)
    draft_path = os.path.join(str(tmp_path), d["filename"])
    with open(draft_path, "w", encoding="utf-8") as fh:
        fh.write(d["text"])
    assert cases_mod.load_cases(str(tmp_path)) == [], "草稿前缀不入库"
    confirmed = "案例-" + d["filename"][len("草稿-"):]
    os.rename(draft_path, os.path.join(str(tmp_path), confirmed))
    loaded = cases_mod.load_cases(str(tmp_path))
    assert len(loaded) == 1, "改名即入库"
    assert loaded[0]["sections"] == 5, "五段可被案例库解析"


def test_t32_cli_evolve():
    """CLI evolve: 真实发现→草稿落盘(子进程写真 cases 目录, 差集自清理)"""
    import subprocess

    import audit as za
    cases_dir = os.path.join(za._REPO_ROOT, "docs", "registry", "cases")
    before = set(os.listdir(cases_dir))
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"), "evolve"],
                           check=False, capture_output=True, text=True,
                           encoding="utf-8", timeout=120)
        assert r.returncode == 0
        assert "自进化" in r.stdout and "草稿已生成" in r.stdout
        assert "人工确认" in r.stdout and "五段" in r.stdout
    finally:
        for n in set(os.listdir(cases_dir)) - before:
            os.remove(os.path.join(cases_dir, n))
