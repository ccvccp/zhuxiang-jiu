"""zhuyi cases 检索器验收测试（T3.1: 6 案例检索抽验, qr 关键词首中 qr 案例）"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cases as cs


def test_load_six_cases_full_meta():
    cases = cs.load_cases()
    assert len(cases) == 6
    assert all(c["sections"] == 5 for c in cases)
    qr = next(c for c in cases if "qr" in c["file"])
    assert qr["id"] == "C-20261021-01"
    assert "白名单" in qr["keywords"] and qr["related_chain"] == "扫码登录链"
    assert any("auth_middleware" in f for f in qr["related_files"])


def test_six_queries_top1():
    """验收: 6 案例 6 查询抽验——每查询首中目标案例"""
    checks = [
        ("扫码登录 401 白名单", "qr白名单"),
        ("nginx 证书 相对路径", "nginx证书"),
        ("微信 缓存 钉死 immutable", "微信缓存"),
        ("移动端 筛选 霸屏", "移动筛选"),
        ("599 臆想 断言 假绿", "599"),
        ("库存 键 笔误 45度", "45度库存"),
    ]
    for query, expect in checks:
        top = cs.search(query)
        assert top, f"无命中: {query}"
        assert expect in top[0][0]["file"], f"{query} 首中错: {top[0][0]['file']}"


def test_score_weights():
    """加权: 文件命中(×5)重于关键词(×3)——qr 文件名查询分更高"""
    by_kw = cs.search("白名单")           # 关键词命中
    by_file = cs.search("auth_middleware")  # 关联文件命中
    assert by_kw[0][0]["id"] == "C-20261021-01"
    assert by_file[0][1] > by_kw[0][1]
    assert any("文件" in h for h in by_file[0][2])


def test_no_hit_empty():
    assert cs.search("完全不相关词汇xyz") == []


def test_cli_consult_query():
    """CLI consult --query: 检索输出+命中明细"""
    r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"),
                        "consult", "--query", "扫码登录 401"],
                       check=False, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0
    assert "T3.1 案例检索" in r.stdout
    assert "qr白名单" in r.stdout and "C-20261021-01" in r.stdout
