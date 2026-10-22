"""竹演 zhuyan CLI 骨架验收测试（T0.1: --help 四命令可见 + 四 stub 分发）"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "cli.py")


def _run(*args):
    return subprocess.run([sys.executable, CLI, *args], check=False,
                          capture_output=True, text=True, encoding="utf-8")


def test_help_lists_four_subcommands():
    r = _run("--help")
    assert r.returncode == 0
    for cmd in ("score", "matrix", "analogize", "evolve"):
        assert cmd in r.stdout


def test_no_args_prints_help():
    r = _run()
    assert r.returncode == 0
    assert "zhuyan" in r.stdout


def test_score_t03_real():
    """T0.3 实装: score 分发指纹台账(子进程落盘真目录, 差集自清理)"""
    import os

    mat_dir = os.path.join(HERE, "..", "..", "docs", "registry", "maturity")
    mat_dir = os.path.abspath(mat_dir)
    before = set(os.listdir(mat_dir)) if os.path.isdir(mat_dir) else set()
    try:
        r = _run("score")
        assert r.returncode == 0
        assert "功能指纹台账" in r.stdout and "指纹基线已落盘" in r.stdout
        assert "T1.1-T1.4" in r.stdout, "评分待实装指引保留"
    finally:
        for n in set(os.listdir(mat_dir)) - before:
            os.remove(os.path.join(mat_dir, n))


def test_matrix_stub():
    r = _run("matrix")
    assert r.returncode == 0
    assert "四象限" in r.stdout


def test_analogize_stub():
    r = _run("analogize")
    assert r.returncode == 0
    assert "只出不施" in r.stdout and "B4" in r.stdout


def test_evolve_stub():
    r = _run("evolve")
    assert r.returncode == 0
    assert "人工确认门" in r.stdout


def test_unknown_command_rejected():
    r = _run("nope")
    assert r.returncode != 0
