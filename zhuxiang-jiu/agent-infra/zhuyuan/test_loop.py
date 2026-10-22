"""zhuyuan loop 闭环演示验收测试（T3.3: 一条命令跑通四环）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import loop as zloop


def test_t33_four_rings_in_process(capsys):
    """四环全链跑通(同进程, conftest 注入草稿隔离目录防污染)"""
    rc = zloop.run()
    out = capsys.readouterr().out
    assert rc == 0
    for ring in ("环 1·盘点", "环 2·合规", "环 3·处方", "环 4·入库"):
        assert ring in out, f"缺环节: {ring}"
    assert "B1 整改实录回放检出率" in out, "环 2 含 B1 基准口径"
    assert "只出不施" in out, "环 3 处方铁律"
    assert "人工确认门" in out, "环 4 入库人工确认"
    assert "闭环跑通" in out and "四环环环相扣" in out


def test_t33_one_command_subprocess():
    """验收口径: 一条命令跑通四环(脚本直跑, 草稿差集自清理)"""
    import subprocess

    import audit as za
    cases_dir = os.path.join(za._REPO_ROOT, "docs", "registry", "cases")
    before = set(os.listdir(cases_dir))
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "loop.py")],
                           check=False, capture_output=True, text=True,
                           encoding="utf-8", timeout=180)
        assert r.returncode == 0
        assert "环 4·入库" in r.stdout and "闭环跑通" in r.stdout
        assert "草稿:" in r.stdout
    finally:
        for n in set(os.listdir(cases_dir)) - before:
            os.remove(os.path.join(cases_dir, n))
