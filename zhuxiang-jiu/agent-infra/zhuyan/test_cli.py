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


def test_score_t14_real():
    """T1.4 实装: score 完整评分+报告归档(子进程落盘真 reports, 差集自清理)"""
    import os

    rep_dir = os.path.join(os.path.dirname(HERE), "..", "docs", "registry",
                           "reports")
    rep_dir = os.path.abspath(rep_dir)
    before = set(os.listdir(rep_dir)) if os.path.isdir(rep_dir) else set()
    try:
        r = _run("score")
        assert r.returncode == 0
        assert "成熟度评分完成" in r.stdout and "分档分布" in r.stdout
        assert "B3 分档回放" in r.stdout and "评分报告已归档" in r.stdout
        assert "度量上报" in r.stdout
    finally:
        for n in set(os.listdir(rep_dir)) - before:
            os.remove(os.path.join(rep_dir, n))


def test_matrix_t21_real():
    """T2.1 实装: matrix 分发四象限+报告归档(子进程落盘真 reports, 差集自清理)"""
    import os

    rep_dir = os.path.join(os.path.dirname(HERE), "..", "docs", "registry",
                           "reports")
    rep_dir = os.path.abspath(rep_dir)
    before = set(os.listdir(rep_dir)) if os.path.isdir(rep_dir) else set()
    try:
        r = _run("matrix")
        assert r.returncode == 0
        assert "痛点四象限" in r.stdout and "优先进化区" in r.stdout
        assert "战略保护区" in r.stdout and "矩阵报告已归档" in r.stdout
    finally:
        for n in set(os.listdir(rep_dir)) - before:
            os.remove(os.path.join(rep_dir, n))


def test_analogize_t24_real():
    """T2.4 实装: analogize 分发单目标深度方案(--target 快路径, 差集自清理)"""
    import os

    rep_dir = os.path.join(os.path.dirname(HERE), "..", "docs", "registry",
                           "reports")
    rep_dir = os.path.abspath(rep_dir)
    before = set(os.listdir(rep_dir)) if os.path.isdir(rep_dir) else set()
    try:
        r = _run("analogize", "--target", "activity-detail.html")
        assert r.returncode == 0
        assert "类比推荐" in r.stdout and "痛点" in r.stdout
        assert "策略A" in r.stdout and "策略B" in r.stdout and "策略C" in r.stdout
        assert "只出不施" in r.stdout and "类比推荐报告已归档" in r.stdout
    finally:
        for n in set(os.listdir(rep_dir)) - before:
            os.remove(os.path.join(rep_dir, n))


def test_evolve_t31_real():
    """T3.1 实装: evolve 分发(慢路径全量采集由 test_evolve 覆盖;
    此处断言子命令存在且非 stub 输出)"""
    r = _run("evolve", "--help")
    assert r.returncode == 0
    assert "--json" in r.stdout, "evolve 子命令参数可见"
    r2 = _run()
    assert "进化沉淀" in r2.stdout, "主 help 描述含进化沉淀(全实装)"


def test_unknown_command_rejected():
    r = _run("nope")
    assert r.returncode != 0
