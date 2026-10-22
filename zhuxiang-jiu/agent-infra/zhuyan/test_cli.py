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


def test_score_t13_real():
    """T1.3 实装: score 分发 S_b+S_r+S_e 三维度视图(基线加权标注)"""
    r = _run("score")
    assert r.returncode == 0
    for dim in ("S_b 业务价值", "S_r 运行稳健", "S_e 工程复用"):
        assert dim in r.stdout
    assert "[S_e=" in r.stdout and "基线" in r.stdout, "S_e Top 样例+加权标注"
    assert "T1.4" in r.stdout, "罚分总分待实装指引"


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
