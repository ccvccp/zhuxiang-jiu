"""zhudun CLI 骨架验收测试（T1.1: --help 三命令可见 + stub 分发）"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "cli.py")


def _run(*args):
    return subprocess.run([sys.executable, CLI, *args], check=False,
                          capture_output=True, text=True, encoding="utf-8")


def test_help_lists_three_subcommands():
    r = _run("--help")
    assert r.returncode == 0
    for cmd in ("index", "impact", "gate"):
        assert cmd in r.stdout


def test_stub_dispatch_and_exit_zero():
    r = _run("gate")
    assert r.returncode == 0
    assert "骨架" in r.stdout
    assert "T1.9/P2-P3" in r.stdout


def test_no_args_prints_help():
    r = _run()
    assert r.returncode == 0
    assert "zhudun" in r.stdout


def test_index_rebuild_reproduces_p0():
    """T1.2 验收: index 全链重建与 P0 锁定版一致(统计行全量命中)"""
    r = _run("index")
    assert r.returncode == 0
    assert "total=2612" in r.stdout          # scan_endpoints
    assert "runtime=480" in r.stdout         # scan_modules
    assert "root_html=59" in r.stdout         # scan_pages
    assert "OK endpoints=2612" in r.stdout    # build_registry_json 自检
    assert "重建完成" in r.stdout


def test_impact_lists_three_change_kinds(tmp_path):
    """T1.3 验收: 3 种改动(py 已提交未推/ js 未跟踪/ html 已改未提交)全部列出"""
    import subprocess as sp

    def g(*a):
        sp.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (tmp_path / "c.html").write_text("v1", encoding="utf-8")
    g("add", "-A")
    g("commit", "-q", "-m", "base")
    (tmp_path / "c.html").write_text("v2", encoding="utf-8")            # html 已改未提交
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "b.js").write_text("x", encoding="utf-8")       # js 未跟踪
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    g("add", "a.py")
    g("commit", "-q", "-m", "add a")                                   # py 已提交未推
    sys.path.insert(0, os.path.dirname(HERE))
    from zhudun import impact
    files = impact.changed_files(base="HEAD~1", repo=str(tmp_path))
    assert set(files) == {"a.py", "c.html", "js/b.js"}


def test_reverse_references(tmp_path):
    """T1.4 验收: 符号/文件名反查引用方带行号, 与人工 grep(朴素逐行)对照无遗漏"""
    import sys
    (tmp_path / "utils.py").write_text(
        "def helper():\n    pass\n\n\nclass Widget:\n    pass\n", encoding="utf-8")
    (tmp_path / "api.py").write_text(
        "from utils import helper\nfrom utils import Widget\nx = helper()\ny = Widget()\n",
        encoding="utf-8")
    (tmp_path / "main.js").write_text("console.log(1)\n", encoding="utf-8")
    (tmp_path / "page.html").write_text('<script src="main.js"></script>\n', encoding="utf-8")
    sys.path.insert(0, os.path.dirname(HERE))
    from zhudun import impact
    changed = ["utils.py", "main.js"]
    syms, refs = impact.referencing_files(changed, repo=str(tmp_path), registry={})
    # 符号: 文件名+模块名+顶层 def/class（4 字符下限过滤后）
    assert {"helper", "Widget", "main.js", "utils.py", "utils"} <= syms
    # 引用方: api.py 引 helper/Widget/utils; page.html 引 main.js
    assert set(refs) == {"api.py", "page.html"}
    assert 1 in refs["api.py"] and 2 in refs["api.py"]
    assert refs["page.html"] == [1]
    # 人工 grep 基准(独立朴素逐行扫描)对照——行号集合完全一致
    for ref_file, lines in refs.items():
        src = (tmp_path / ref_file).read_text(encoding="utf-8").split("\n")
        manual = [i for i, ln in enumerate(src, 1)
                  if any(s in ln for s in syms)]
        assert lines == [ln for ln in lines if ln in manual]
