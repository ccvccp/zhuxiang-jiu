"""zhuyuan audit 盘点基线验收测试（T0.3: B2 基准——健康样本全过+挖空样本检出）"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import audit as za

REPO = os.path.dirname(os.path.dirname(HERE))  # zhuxiang-jiu


def test_b2_real_index_healthy():
    """B2 健康样本: 真 index.html(noscript 资质块)无 P0 缺口"""
    assets, _ = za.scan_assets()
    idx_p0 = [a for a in assets
              if a["asset_id"] == "index.html" and a.get("priority") == "P0"]
    assert idx_p0 == []  # noscript 资质+警示语全 → 无 P0
    assert any(a["asset_id"] == "index.html" and a["status"] == "healthy"
               for a in assets)


def test_b2_hollow_variant_detected(tmp_path):
    """**B2 验收**: 挖空副本(移除 noscript 资质+main.js fallback)必检出 2 处 P0"""
    with open(os.path.join(REPO, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    hollow = re.sub(r"<noscript>.*?</noscript>", "", html, flags=re.DOTALL)
    hollow = re.sub(r'<script src="js/main.js[^>]*></script>', "", hollow)
    (tmp_path / "index.html").write_text(hollow, encoding="utf-8")
    (tmp_path / "js").mkdir()
    assets, summary = za.scan_assets(repo_root=str(tmp_path))
    p0s = [a for a in assets if a.get("priority") == "P0"]
    kinds = {a["evidence"] for a in p0s}
    assert any("ICP" in k for k in kinds)
    assert any("食品经营许可证" in k for k in kinds)
    # 警示语在 hero 区保留(未挖) → 无警示 P0, 挖空范围精确
    assert not any("健康警示语" in k for k in kinds)
    assert summary["missing"] == 1 and summary["healthy"] == 0


def test_placeholder_and_exempt(tmp_path):
    """占位符页→missing(P0); 豁免清单页→exempt"""
    (tmp_path / "promo-x.html").write_text(
        "<html><head><title>促销</title>"
        '<meta name="description" content="d"></head><body>敬请期待，TODO 补内容'
        "鲁ICP备2026059492号-1 食品经营许可证：SC1 未成年人禁止饮酒</body></html>",
        encoding="utf-8")
    (tmp_path / "module-test.html").write_text("<html>内部页</html>", encoding="utf-8")
    assets, _ = za.scan_assets(repo_root=str(tmp_path))
    ph = [a for a in assets if a["asset_id"] == "promo-x.html"]
    assert any(a["priority"] == "P0" and "占位符" in a["evidence"] for a in ph)
    assert any(a["status"] == "exempt" for a in assets if a["asset_id"] == "module-test.html")


def test_seo_and_alt_and_broken_img(tmp_path):
    """SEO 空→P1; img 无 alt→P2; 断链图→P2"""
    (tmp_path / "page-x.html").write_text(
        '<html><head><title></title></head><body>鲁ICP备2026059492号-1 '
        "食品经营许可证：SC1 未成年人禁止饮酒 " + "足够长的正文内容" * 30 +
        '<img src="ghost.png"></body></html>', encoding="utf-8")
    assets, _ = za.scan_assets(repo_root=str(tmp_path))
    gaps = {(a["gap_type"], a["priority"]) for a in assets if a["asset_id"] == "page-x.html"}
    assert ("seo_missing", "P1") in gaps
    assert ("alt_missing", "P2") in gaps
    assert ("image_broken", "P2") in gaps


def test_dynamic_shell_skips_text_check():
    """SPA 壳(引用 js/main.js)跳过空正文判定"""
    shell = ('<html><head><title>t</title>'
             '<meta name="description" content="d"></head><body>'
             '<script src="js/main.js?v=1"></script></body></html>')
    findings = za.audit_page(shell, "shell.html", REPO, shared_text="鲁ICP备1号 食品经营许可证 未成年人禁止")
    assert not any("可见正文" in f["evidence"] for f in findings)
