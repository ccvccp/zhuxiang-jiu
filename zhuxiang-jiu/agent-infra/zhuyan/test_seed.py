"""zhuyan seed 黄金种子核查验收测试（T2.2: 五维 Check+排除项如实标注）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import seed as zsd


def _m(pen=0.0, vetoed=False, calls=3, chains=None,
       tests=None, test_v=1.0, contract=0.8, features=None, imports=3):
    """合成候选(默认五维全过)。"""
    chains = ["链一：下单支付链"] if chains is None else chains
    tests = ["backend/test_qr_public_baseline.py"] if tests is None else tests
    features = ["retry", "timeout"] if features is None else features
    return {"unit": {"unit_id": "POST /api/x"}, "total": 0.55, "grade": "mid",
            "penalty": pen,
            "sb_ev": {"call_count": calls, "hit_chains": chains},
            "sr_ev": {"fault_features": features},
            "se_ev": {"tests": tests, "test_value": test_v,
                      "contract": contract, "imports": imports},
            "pen_ev": {"vetoed": vetoed, "items": [], "lines": []}}


def test_t22_five_dims_pass():
    """五维全过 → PASS(种子模板池)"""
    verdict, checks, ex = zsd.check_seed(_m())
    assert verdict == "PASS" and not ex
    assert len(checks) == 5 and all(ok for _n, ok, _d in checks)


def test_t22_exclusions_triggered():
    """排除项触发 → EXCLUDE 如实标注(罚分/无测试/纯CRUD/强绑定)"""
    v, _c, ex = zsd.check_seed(_m(pen=0.2))
    assert v == "EXCLUDE" and any("稳定性" in e for e in ex)
    v, _c, ex = zsd.check_seed(_m(tests=[]))
    assert v == "EXCLUDE" and any("无关联测试" in e for e in ex)
    v, _c, ex = zsd.check_seed(_m(features=[]))
    assert v == "EXCLUDE" and any("纯 CRUD" in e for e in ex)
    v, _c, ex = zsd.check_seed(_m(imports=9))
    assert v == "EXCLUDE" and any("强绑定" in e for e in ex)


def test_t22_watch_partial():
    """无排除但有不达标(如容错仅 1 类/契约低) → WATCH"""
    v, checks, _ex = zsd.check_seed(_m(features=["retry"], contract=0.3))
    assert v == "WATCH"
    assert not all(ok for _n, ok, _d in checks)


def test_t22_real_candidates():
    """真实候选核查: mid 层择优 Top10 逐项 Check 跑通"""
    cands = zsd.top_candidates(top_n=10)
    assert 1 <= len(cands) <= 10
    m0, rank0 = cands[0]
    assert m0["grade"] in ("high", "mid") and rank0 == 1
    verdict, checks, _ex = zsd.check_seed(m0)
    assert verdict in ("PASS", "WATCH", "EXCLUDE")
    assert len(checks) == 5, "每候选五维齐"


def test_t22_report(tmp_path):
    """种子核查报告四节(隔离目录)"""
    cands = zsd.top_candidates(top_n=5)
    path = zsd.generate_report(cands, out_dir=str(tmp_path))
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for sec in ("一、五维逐项 Check", "二、排除项触发清单", "三、判定汇总",
                "四、口径声明"):
        assert sec in text
    assert "只出不施" in text and "high=0" in text, "M1 已证口径如实"
