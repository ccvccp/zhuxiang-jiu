"""zhuyuan prescribe 缺口处方验收测试（T3.1: 缺口样本→处方含案例引用+建议）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import prescribe as zpr


def test_t31_normalize_three_sources():
    """三源归一化: 盘点/合规/探针 → 统一 id/level/query/suggestion"""
    a = zpr.normalize_gap({"asset_id": "x.html", "asset_type": "page",
                           "status": "missing", "gap_type": "seo_missing",
                           "priority": "P1", "evidence": "无 meta description"})
    assert a["id"] == "x.html:seo_missing" and a["level"] == "P1"
    assert "description" in a["query"] and "SEO" in a["suggestion"]
    c = zpr.normalize_gap({"file": "js/x.js", "line": 9, "word": "醒酒",
                           "rule_id": "ALC-FUNC", "category": "功效宣称",
                           "risk": "high", "suggestion": "改写口感词"})
    assert c["id"] == "js/x.js:9" and c["level"] == "P0", "high 违规=P0"
    assert c["suggestion"] == "改写口感词" and "醒酒" in c["query"]
    m = zpr.normalize_gap({"file": "js/y.js", "line": 1, "word": "最好",
                           "rule_id": "AD-ABSOLUTE", "category": "绝对化",
                           "risk": "med", "suggestion": "人工裁量改写"})
    assert m["level"] == "P2", "med 边缘=P2 人工裁量不阻断"
    p = zpr.normalize_gap({"level": "P2", "msg": "立即购买按钮不在首屏"})
    assert p["level"] == "P2" and p["id"].startswith("立即购买")
    assert "探针报告" in p["suggestion"]


def test_t31_prescribe_case_hit():
    """验收正例: 可命中案例库的缺口样本 → 处方含案例引用(编号/得分/修正模板)+建议"""
    prescs = zpr.prescribe_gaps(
        [{"level": "P0", "msg": "qr 白名单 死条目 扫码登录 401"}])
    assert len(prescs) == 1
    p = prescs[0]
    assert p["case_ref"] is not None, "应命中 qr 白名单死条目案例"
    assert p["case_ref"]["id"] == "C-20261021-01"
    assert p["case_ref"]["score"] > 0 and p["case_ref"]["hits"]
    assert "正确写法" in p["case_ref"]["patch_hint"], "修正模板源自案例四段"
    assert p["case_ref"]["prevention"], "预防规则随处方"
    assert p["suggestion"] and p["confidence"] in ("中", "低")


def test_t31_prescribe_no_case_honest():
    """验收反例: 案例库无相关命中 → case_ref=None 如实标注+按词表口径处置"""
    prescs = zpr.prescribe_gaps([{
        "file": "js/x.js", "line": 3, "word": "醒酒", "rule_id": "ALC-FUNC",
        "category": "功效宣称", "risk": "high",
        "suggestion": "参照案例四段正确写法替换口感/工艺描述"}])
    p = prescs[0]
    assert p["case_ref"] is None
    assert "案例库无相关命中" in p["suggestion"], "无命中如实标注"
    assert p["level"] == "P0" and p["confidence"] == "低"


def test_t31_weak_hit_filtered():
    """弱命中过滤: 纯技术面 2 分不算相关——.html 资产缺口不误引不相关案例"""
    prescs = zpr.prescribe_gaps([{
        "asset_id": "x.html", "asset_type": "page", "status": "missing",
        "gap_type": "seo_missing", "priority": "P1",
        "evidence": "无 meta description"}])
    p = prescs[0]
    assert p["case_ref"] is None, "技术面泛匹配(2分)不算相关——如实无命中"


def test_t31_cli_prescribe_default():
    """CLI 默认: 真实当前缺口(盘点+合规两源)→处方输出+案例引用+只出不施尾注"""
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"), "prescribe"],
                       check=False, capture_output=True, text=True, encoding="utf-8",
                       timeout=120)
    assert r.returncode == 0
    assert "缺口处方" in r.stdout and "只出不施" in r.stdout
    assert "案例引用" in r.stdout and "建议" in r.stdout
    assert "P1" in r.stdout, "当前真实缺口含 SEO 类 P1"


def test_t31_cli_prescribe_query():
    """CLI --query: 检索模式命中案例+修正模板(源自四段正确写法)"""
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"),
                        "prescribe", "--query", "扫码登录 401 白名单"],
                       check=False, capture_output=True, text=True, encoding="utf-8",
                       timeout=120)
    assert r.returncode == 0
    assert "命中" in r.stdout and "修正模板" in r.stdout
    assert "C-20261021-01" in r.stdout
