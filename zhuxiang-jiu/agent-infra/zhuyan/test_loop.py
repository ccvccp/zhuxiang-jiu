"""zhuyan loop 闭环演示验收测试（T3.2: 四环全真跑通+零副作用）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import loop as zloop


def test_t32_loop_four_rings_zero_side_effect():
    """验收核心: 一条命令四环全真跑通+各环结论齐+零副作用(约 4 分钟)"""
    root = __import__("fingerprint").REPO_ROOT
    reports = os.path.join(root, "docs", "registry", "reports")
    cases = os.path.join(root, "docs", "registry", "cases")
    before = (set(os.listdir(reports)), set(os.listdir(cases)))
    summary = zloop.run_loop()
    # 四环结论齐
    assert summary["units"] > 2000, "全量单元(2651)"
    assert set(summary["grades"]) <= {"high", "mid", "low"}
    assert "low" in summary["grades"] and "mid" in summary["grades"], \
        "M1 已证 high=0——Counter 无 high 键, 如实"
    assert summary["b3_pass"] is True, "环1 B3 回放 PASS"
    assert set(summary["quads"]) == {"优先进化区", "战略保护区",
                                     "观察等待区", "维持现状区"}
    assert summary["plans"] == summary["quads"]["优先进化区"], "环3 全量目标"
    assert 0 <= summary["case_hits"] <= summary["plans"]
    assert summary["drafts"] >= 1, "环4 草稿生成"
    assert summary["sections_ok"] == summary["drafts"], "五段全齐"
    # 零副作用: 报告/草稿目录不变
    after = (set(os.listdir(reports)), set(os.listdir(cases)))
    assert before == after, "loop 零副作用(不归档不落盘)"
