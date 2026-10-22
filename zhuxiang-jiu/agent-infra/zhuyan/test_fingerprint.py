"""zhuyan fingerprint 功能指纹验收测试（T0.3: backend 全量扫描跑通+指纹字段取证）"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fingerprint as zf

_QR_SCAN = "POST /api/entry/qr/{qr_id}/scan"


def test_t03_enumerate_endpoints():
    """端点枚举: L0 权威源复用——全量非空+qr 扫描链端点在列+指纹字段取证"""
    units = zf.enumerate_units()
    eps = [u for u in units if u["unit_type"] == "endpoint"]
    assert len(eps) > 2000, "L0 台账 2612 条端点应全量在列"
    qr = next(u for u in eps if u["unit_id"] == _QR_SCAN)
    assert qr["file"] and qr["file"].startswith("backend/routes/"), \
        "端点→backend 源文件映射"
    assert qr["func"] and qr["line"] > 0
    assert qr["contract"]["auth"], "契约特征=auth 口径"
    assert isinstance(qr["tests"], list), "关联测试字段"
    assert qr["lines"] > 0 and qr["imports"] >= 0 and "docstring_rate" in qr


def test_t03_modules_and_pages():
    """模块+页面枚举: core 模块在列+页面豁免口径复用(dashboard 不在列)"""
    units = zf.enumerate_units()
    mods = [u["unit_id"] for u in units if u["unit_type"] == "module"]
    assert "core/locks.py" in mods, "backend/core 模块在列"
    pages = [u for u in units if u["unit_type"] == "page"]
    ids = [u["unit_id"] for u in pages]
    assert "index.html" in ids and "product-detail.html" in ids
    assert not any("-dashboard" in i for i in ids), "竹园豁免口径复用"
    idx = next(u for u in pages if u["unit_id"] == "index.html")
    assert idx["contract"]["has_title"] is True
    assert idx["contract"]["has_description"] is True, "index SEO 契约特征"


def test_t03_page_seo_evidence():
    """已知低分样本取证: seo_missing 页面 has_description=False 如实入指纹"""
    units = zf.enumerate_units()
    act = next(u for u in units if u["unit_id"] == "activity-detail.html")
    assert act["contract"]["has_description"] is False, \
        "竹园 audit 已证缺口——指纹如实呈现(B3 低分样本)"


def test_t03_save_roundtrip(tmp_path):
    """指纹落盘 json 可读回(L2 实体层基线)"""
    units = zf.enumerate_units()
    path = zf.save_fingerprints(units, out_path=str(tmp_path / "fp.json"))
    with open(path, encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert len(loaded) == len(units)
    assert loaded[0]["unit_id"], "json 结构完整"


def test_t03_cli_score(tmp_path):
    """CLI score 分发: 指纹台账三类输出+落盘(子进程落盘真目录, 差集自清理)"""
    import subprocess
    mat_dir = os.path.join(zf.REPO_ROOT, "docs", "registry", "maturity")
    before = set(os.listdir(mat_dir)) if os.path.isdir(mat_dir) else set()
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"), "score"],
                           check=False, capture_output=True, text=True,
                           encoding="utf-8", timeout=180)
        assert r.returncode == 0
        assert "功能指纹台账" in r.stdout and "端点" in r.stdout
        assert "指纹基线已落盘" in r.stdout
    finally:
        for n in set(os.listdir(mat_dir)) - before:
            os.remove(os.path.join(mat_dir, n))
