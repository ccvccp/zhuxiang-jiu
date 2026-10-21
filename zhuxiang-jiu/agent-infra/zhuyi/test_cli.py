"""zhuyi CLI 骨架验收测试（T0.1: --help 三命令可见 + 三 stub 分发）"""
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
    for cmd in ("audit", "contract", "consult"):
        assert cmd in r.stdout


def test_no_args_prints_help():
    r = _run()
    assert r.returncode == 0
    assert "zhuyi" in r.stdout


def test_audit_stub():
    r = _run("audit")
    assert r.returncode == 0
    assert "audit" in r.stdout and "骨架占位" in r.stdout
    assert "qr" in r.stdout  # M1 benchmark 提示可见


def test_contract_stub():
    r = _run("contract")
    assert r.returncode == 0
    assert "死条目" in r.stdout


def test_consult_stub():
    r = _run("consult")
    assert r.returncode == 0
    assert "只出不施" in r.stdout


def test_unknown_command_rejected():
    r = _run("nope")
    assert r.returncode != 0  # argparse 无此子命令报错退出
