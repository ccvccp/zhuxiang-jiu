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


def test_score_t11_real():
    """T1.1 实装: score 分发 S_b 评分视图(链命中分布+Top 样例)"""
    r = _run("score")
    assert r.returncode == 0
    assert "S_b 业务价值" in r.stdout and "链命中分布" in r.stdout
    assert "[S_b=" in r.stdout, "Top 样例评分行"
    assert "T1.2" in r.stdout, "后续维度待实装指引"


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
