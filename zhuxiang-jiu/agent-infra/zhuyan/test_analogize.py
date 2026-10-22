"""zhuyan analogize 类比推荐验收测试（T2.4: 三策略只出不施）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import analogize as zalg


def _fake_m(penalty=0.0, tests=None, contract=0.2, features=None, imports=9):
    tests = ["backend/test_qr_public_baseline.py"] if tests is None else tests
    features = ["retry"] if features is None else features
    return {"total": 0.35, "grade": "low", "penalty": penalty,
            "sb_ev": {"call_count": 1, "hit_chains": []},
            "sr_ev": {"fault_features": features},
            "se_ev": {"tests": tests, "test_value": 1.0 if tests else 0.0,
                      "contract": contract, "imports": imports},
            "pen_ev": {"vetoed": False, "items": [], "lines": []}}


def test_t24_pain_points_evidence():
    """痛点取证: 罚分/无测试/契约/容错/耦合 如实列示"""
    m = _fake_m(penalty=0.2, tests=[], imports=15)
    pains = " ".join(zalg.pain_points(m))
    assert "罚分 0.2" in pains and "无关联测试" in pains and "耦合偏高" in pains
    clean = _fake_m(contract=0.9, features=["retry", "timeout"], imports=3,
                    penalty=0.0)
    assert zalg.pain_points(clean) == ["无明显低分维度"]


def test_t24_strategies_three_kinds():
    """三策略齐备: A 补强/B 参照重构(人工主导)/C 模式移植(命中条件)"""
    m = _fake_m()
    ap_hits = [("u", "AP-5 日志风暴", 3, "循环内高频日志(建议聚合)")]
    plan_hit = zalg._strategies(m, ap_hits, {"id": "C-1", "title": "t",
                                             "score": 7, "hits": ["x"]}, "seed-x")
    plan_miss = zalg._strategies(m, [], None, "seed-x")
    assert [p[0] for p in plan_hit] == ["策略A 保守补强", "策略B 参照重构",
                                        "策略C 模式移植"]
    assert "先清反模式" in plan_hit[0][2]
    assert "人工主导" in plan_hit[1][2]
    assert "不适用" in plan_miss[2][2], "无命中时策略C 如实不适用"


def test_t24_analogize_unit_synthetic():
    """单目标深度类比(合成): 痛点+反模式+案例检索+三策略全链跑通"""
    units = [{"unit_id": "POST /api/x", "unit_type": "endpoint",
              "file": "backend/routes/x.py", "docstring_rate": 0.2,
              "imports": 9, "tests": [], "lines": 10, "defs": 1}]
    src_ctx = {"backend/routes/x.py":
               "def f(ids):\n    for i in ids:\n        print(i)\n"}
    sb_ctx = {"chains": {}, "calls": {}}
    sr_ctx = {"backend/routes/x.py": src_ctx["backend/routes/x.py"]}
    se_ctx = {"src": sr_ctx, "test_asserts": {}}
    plan = zalg.analogize_unit("POST /api/x", (sb_ctx, sr_ctx, se_ctx),
                               units, src_ctx, all_cases=[])
    assert "无关联测试" in " ".join(plan["pains"])
    assert any("AP-5" in h[1] for h in plan["ap_hits"]), "日志风暴命中"
    assert plan["case_ref"] is None, "空案例库如实无命中"
    assert len(plan["strategies"]) == 3


def test_t24_report(tmp_path):
    """类比推荐报告五节+只出不施口径(隔离目录)"""
    m = _fake_m()
    plan = {"unit": {"unit_id": "POST /api/x"}, "m": m,
            "pains": zalg.pain_points(m), "ap_hits": [], "case_ref": None,
            "ref_seed": "seed-x",
            "strategies": zalg._strategies(m, [], None, "seed-x")}
    path = zalg.generate_report([plan], out_dir=str(tmp_path))
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for sec in ("一、目标与痛点", "二、反模式前置过滤", "三、案例检索命中",
                "四、三策略方案", "五、口径声明"):
        assert sec in text
    assert "只出不施" in text and "竹盾五步" in text
