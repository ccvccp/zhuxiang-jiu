"""zhuyi CLI 验收测试（T0.1 骨架 + T1.1 audit 实装分发）"""
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


def test_audit_t11_real():
    """T1.1 实装: audit 分发真实现——端点在册检查+cart gap 检出+后续任务提示"""
    r = _run("audit")
    assert r.returncode == 0
    assert "端点在册" in r.stdout
    assert "/api/cart" in r.stdout and "miss=1" in r.stdout
    assert "T1.2 鉴权可达" in r.stdout  # 后续检提示可见(M1 benchmark 链路)


def test_contract_stub():
    r = _run("contract")
    assert r.returncode == 0
    assert "死条目" in r.stdout


def test_consult_t31_real():
    """T3.1 实装: consult 分发案例检索——qr 关键词首中 qr 案例"""
    r = _run("consult", "--query", "扫码登录 401")
    assert r.returncode == 0
    assert "T3.1 案例检索" in r.stdout
    assert "C-20261021-01" in r.stdout
    assert "T3.2-T3.4 处方" in r.stdout and "T3.5 端到端处方报告已归档" in r.stdout


def test_unknown_command_rejected():
    r = _run("nope")
    assert r.returncode != 0  # argparse 无此子命令报错退出
