"""zhuyi cases 检索器验收测试（T3.1: 6 案例检索抽验, qr 关键词首中 qr 案例）"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cases as cs


def test_load_six_cases_full_meta():
    cases = cs.load_cases()
    assert len(cases) == 10  # 首批 6(竹医 T0.3)+二批 4(竹园 80号 T3.5 首批资产案例)
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


# ---------- T3.2 处方模板引擎（每案例可产出处方骨架） ----------

def test_t32_prescription_all_six_cases():
    """验收: 6/6 案例全可产处方骨架——五要素(症状/根因/修正/回归/预防)非空"""
    for c in cs.load_cases():
        p = cs.build_prescription(c)
        assert p["id"] and p["severity"]
        for k in ("symptom", "root_cause", "patch_hint",
                  "regression_hint", "prevention"):
            assert p[k].strip(), f"{c['file']} 处方缺 {k}"


def test_t32_qr_prescription_content():
    """qr 处方: 根因含死条目语义+回归建议含挂载点 T1.2/T2.3"""
    qr = next(c for c in cs.load_cases() if "qr" in c["file"])
    p = cs.build_prescription(qr)
    assert "死条目" in p["root_cause"]
    assert "删 3" in p["patch_hint"] or "正则档" in p["patch_hint"]
    assert "T1.2" in p["regression_hint"] and "T2.3" in p["regression_hint"]
    assert p["commit"] == "e73f6800"


def test_t32_render_prescription():
    """渲染: 处方头+五要素节+只出不施铁律"""
    qr = next(c for c in cs.load_cases() if "qr" in c["file"])
    text = cs.render_prescription(cs.build_prescription(qr))
    assert text.startswith("## 处方 · C-20261021-01")
    assert "根因:" in text and "修正模板:" in text and "回归建议:" in text
    assert "只出不施" in text
    assert "e73f6800" in text


# ---------- T3.3 Blast Radius 集成（真实变更处方含反向引用） ----------

def test_t33_qr_blast_radius_real_refs():
    """验收: qr 处方(auth_middleware 改动)反向引用真实命中且去噪(无 .deps/工具目录)"""
    qr = next(c for c in cs.load_cases() if "qr" in c["file"])
    p = cs.attach_blast_radius(cs.build_prescription(qr))
    br = p["blast_radius"]
    assert br["changed"] == ["backend/core/auth_middleware.py"]
    assert br["refs"], "真实仓库必有引用方(main.py 挂载中间件)"
    assert all(not any(x in f for x in ("/.deps/", "agent-infra", "taro-app",
                                         "test_")) for f in br["refs"])
    assert any(f.startswith("backend/") for f in br["refs"])
    # 渲染含影响面节
    text = cs.render_prescription(p)
    assert "影响面(Blast Radius)" in text and "反向引用" in text


def test_t33_empty_related_files_safe():
    """related_files 为空的处方 attach 不炸(空 BR 占位)"""
    p = {"related_files": [], "symptom": "", "root_cause": "", "patch_hint": "",
         "regression_hint": "", "prevention": "", "id": "C-x", "title": "t",
         "severity": "P1", "commit": "", "date": "", "case_file": "x.md"}
    cs.attach_blast_radius(p)
    assert p["blast_radius"] == {"changed": [], "refs": {}}


# ---------- T3.4 分级+置信度（P0/P1/P2 + 证据引用） ----------

def test_t34_grade_rules_qr_high():
    """qr 处方: P0(阻断)+高置信(三证: 案例编号+commit e73f6800+反向引用)"""
    qr = next(c for c in cs.load_cases() if "qr" in c["file"])
    p = cs.grade_prescription(cs.attach_blast_radius(cs.build_prescription(qr)))
    assert p["grade"] == "P0(阻断)"
    assert p["confidence"] == "高" and len(p["evidence"]) == 3
    assert any("C-20261021-01" in e for e in p["evidence"])
    assert any("e73f6800" in e for e in p["evidence"])
    assert any(e.startswith("反向引用:") for e in p["evidence"])


def test_t34_grade_p1_and_low_confidence():
    """599 案例分级 P1(隐患); 无 commit 无 BR 的处方→低置信"""
    c599 = next(c for c in cs.load_cases() if "599" in c["file"])
    p = cs.grade_prescription(cs.attach_blast_radius(cs.build_prescription(c599)))
    assert p["grade"] == "P1(隐患)"
    assert p["confidence"] in ("中", "高")
    bare = {"id": "C-x", "severity": "P2", "commit": "", "related_files": [],
            "blast_radius": {"changed": [], "refs": {}}}
    p2 = cs.grade_prescription(bare)
    assert p2["grade"] == "P2(优化)" and p2["confidence"] == "低"
    assert p2["evidence"] == ["案例编号:C-x"]  # 无出处标低置信(铁律2)


def test_t34_render_grade_section():
    """渲染: 分级/置信度/证据节齐备"""
    qr = next(c for c in cs.load_cases() if "qr" in c["file"])
    p = cs.grade_prescription(cs.attach_blast_radius(cs.build_prescription(qr)))
    text = cs.render_prescription(p)
    assert "分级: P0(阻断)" in text and "置信度: 高" in text
    assert "案例编号:C-20261021-01" in text


# ---------- T3.5 端到端处方（真实变更全链+回归映射） ----------

def test_t35_regression_map_groups():
    """回归资产映射: qr 组来自 test_qr_public_baseline, B1 组来自 feature_baseline"""
    groups = cs._regression_map()
    assert groups["qr"], "qr 白名单基线(test_qr_public_baseline)函数须在列"
    assert all(fn.startswith("test_") for fn in groups["qr"])
    assert groups["b1"], "B1 运费基线函数须在列"


def test_t35_full_prescription_end_to_end(tmp_path):
    """验收: 真实变更全链——检索命中→BR→分级→回归映射→报告归档"""
    path, p = cs.generate_full_prescription("扫码登录 401 白名单",
                                             out_dir=str(tmp_path))
    assert path and p["id"] == "C-20261021-01"
    assert p["grade"] == "P0(阻断)" and p["confidence"] == "高"
    assert p["blast_radius"]["refs"], "BR 真实反向引用"
    qr_mapped = [g for g in p["regression_tests"] if "qr" in g[0]]
    assert qr_mapped and all(fn.startswith("test_") for fn in qr_mapped[0][1])
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "端到端" in text and "回归测试建议" in text and "只出不施" in text
    assert "test_qr" in text or qr_mapped[0][1][0] in text


def test_t35_no_hit_no_report(tmp_path):
    path, p = cs.generate_full_prescription("完全不相关xyz", out_dir=str(tmp_path))
    assert path is None and p == []
