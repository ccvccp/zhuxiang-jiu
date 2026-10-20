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
