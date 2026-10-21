"""zhuyuan rules 词表引擎验收测试（T1.1: 四大类+每词 risk 标注+match 口径）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rules as zr


def test_rules_schema_and_categories():
    """验收: 四大类全 + 每条规则 schema 五字段齐 + risk 标注合法值"""
    cats = {r["category"] for r in zr.RULES}
    for must in ("功效宣称", "怂恿饮酒", "无依据营养声称", "绝对化用语"):
        assert must in cats
    for r in zr.RULES:
        assert all(k in r for k in
                   ("rule_id", "category", "risk_level", "pattern", "legal_reference"))
        assert r["risk_level"] in ("high", "med")
        assert r["legal_reference"].startswith("《广告法》")


def test_match_hits_each_category():
    """match: 各类别违规文本命中对应规则(微信整改实录词)"""
    hits = zr.match("醒酒快、无酒气、不头疼，畅饮更尽兴，富含富硒泉水，顶级养生")
    ids = {h["rule_id"] for h in hits}
    words = {h["word"] for h in hits}
    assert "ALC-FUNC" in ids and "醒酒" in words
    assert "ALC-INCITE" in ids and "畅饮" in words
    assert "ALC-HEALTH" in ids and "富硒" in words
    assert "AD-ABSOLUTE" in ids and "顶级" in words
    assert all(h["risk"] in ("high", "med") for h in hits)


def test_match_clean_text_zero_false_positive():
    """零误报口径: 合规警示语+整改后合规写法不命中"""
    clean = ("竹香清雅，入口绵柔，回味悠长。"
             "过量饮酒有害健康 · 未成年人禁止购买和饮用酒类 · 饮酒后请勿驾车")
    assert zr.match(clean) == []
    assert zr.match("纯净透明，回味悠长，包装精美") == []


def test_medical_claim_flagged():
    """医疗暗示附类: 治疗/疗效命中 high"""
    hits = zr.match("长期饮用可治疗胃病，疗效显著")
    assert any(h["rule_id"] == "AD-MED" for h in hits)
    assert all(h["risk"] == "high" for h in hits if h["rule_id"] == "AD-MED")
