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
    r = _run("impact")
    assert r.returncode == 0
    assert "骨架" in r.stdout
    assert "T1.3-T1.7" in r.stdout


def test_no_args_prints_help():
    r = _run()
    assert r.returncode == 0
    assert "zhudun" in r.stdout
