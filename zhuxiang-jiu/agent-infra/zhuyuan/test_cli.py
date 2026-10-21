"""竹园 zhuyuan CLI 骨架验收测试（T0.1: --help 五命令可见 + 五 stub 分发）"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "cli.py")


def _run(*args):
    return subprocess.run([sys.executable, CLI, *args], check=False,
                          capture_output=True, text=True, encoding="utf-8")


def test_help_lists_five_subcommands():
    r = _run("--help")
    assert r.returncode == 0
    for cmd in ("audit", "comply", "probe", "prescribe", "evolve"):
        assert cmd in r.stdout


def test_no_args_prints_help():
    r = _run()
    assert r.returncode == 0
    assert "zhuyuan" in r.stdout


def test_audit_t03_real():
    """T0.3 实装: audit 分发盘点——台账汇总+healthy 样本在列"""
    r = _run("audit")
    assert r.returncode == 0
    assert "资产台账" in r.stdout
    assert "healthy" in r.stdout and "missing" in r.stdout


def test_comply_t12_real():
    """T1.2 实装: comply 分发扫描——违规计数+B1 检出率"""
    r = _run("comply")
    assert r.returncode == 0
    assert "合规扫描" in r.stdout and "B1 整改实录回放" in r.stdout


def test_probe_stub():
    r = _run("probe")
    assert r.returncode == 0
    assert "探针" in r.stdout


def test_prescribe_stub():
    r = _run("prescribe")
    assert r.returncode == 0
    assert "只出不施" in r.stdout


def test_evolve_stub():
    r = _run("evolve")
    assert r.returncode == 0
    assert "自进化" in r.stdout and "人工确认" in r.stdout


def test_unknown_command_rejected():
    r = _run("nope")
    assert r.returncode != 0
