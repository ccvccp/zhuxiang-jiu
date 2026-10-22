"""zhuyan matrix 痛点四象限验收测试（T2.1: 四区规则+已知样本落区正确）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import matrix as zm


def test_t21_quadrant_rules():
    """验收核心: 四区分类规则(S_b×总分 二分阈值)"""
    cases = [
        (0.43, 0.35, "优先进化区"),  # 高价值+低质量
        (0.70, 0.55, "战略保护区"),  # 高价值+高质量
        (0.15, 0.16, "观察等待区"),  # 低价值+低质量
        (0.12, 0.57, "维持现状区"),  # 低价值+高质量
        (zm.SB_THRESHOLD, 0.20, "优先进化区"),  # 阈值含等号
        (0.29, zm.TOTAL_THRESHOLD, "维持现状区"),  # 阈值含等号
    ]
    for sb, total, expect in cases:
        assert zm.quadrant_of(sb, total) == expect, f"S_b={sb} 总分={total}"


def test_t21_known_samples_real():
    """已知样本落区如实: qr scan→战略保护区; activity-detail→优先进化区"""
    matrix = zm.build_matrix()
    quad = {m["unit"]["unit_id"]: q for m, q in matrix}
    assert quad["POST /api/entry/qr/{qr_id}/scan"] == "战略保护区", \
        "qr scan: S_b=0.43(链命中)+总分≥0.40(B3 已证未落 low)"
    assert quad["activity-detail.html"] == "优先进化区", \
        "竹园缺口页: 页面调用面使 S_b≥0.30(高价值)+质量 0.16(低)——正是待进化目标"


def test_t21_matrix_counts_consistent():
    """全量矩阵: 四区计数和=总单元数; 象限名仅四值"""
    matrix = zm.build_matrix()
    quads = {q for _m, q in matrix}
    assert quads <= {"优先进化区", "战略保护区", "观察等待区", "维持现状区"}
    counts = sum(1 for _m, _q in matrix)
    assert counts == len(matrix)


def test_t21_report(tmp_path):
    """矩阵报告四节+已知样本回放行(隔离目录生成)"""
    units = __import__("fingerprint").enumerate_units()[:60]
    ctxs = (None, __import__("score").build_sr_context(units),
            __import__("score").build_se_context(units))
    matrix = zm.build_matrix(units, ctxs)
    path = zm.generate_report(matrix, out_dir=str(tmp_path))
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for sec in ("一、四象限总览", "二、各区样例", "三、战略保护区口径", "四、已知样本落区"):
        assert sec in text
    assert "只出不施" in text and "优先进化区" in text
