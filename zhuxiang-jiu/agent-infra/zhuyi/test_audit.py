"""zhuyi audit 第一检验收测试（T1.1: 端点在册检查——构造缺端点链→检出）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import audit as au
import chains as ch


def _step(no, label, eps, internal=False, pending=False):
    return {"no": no, "label": label, "endpoints": eps,
            "internal": internal, "pending": pending,
            "pre": "", "fields": ""}


def test_real_chains_cart_gap_detected():
    """真六链跑第一检: 唯一 miss=/api/cart(拍板①目标形态gap)被如实检出"""
    findings, total = au.check_endpoint_registry()
    assert total["hit"] == 23 and total["miss"] == 1
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "miss" and f["path"] == "/api/cart"
    assert f["chain"] == "下单支付链" and f["step"] == 2


def test_fixture_missing_endpoint_detected():
    """验收口径: 构造缺端点链 → 检出(存在端点 hit/幽灵端点 miss)"""
    fake = [{"name": "假想链", "goal": "T1.1 验收", "steps": [
        _step(1, "存在步骤", [("GET", "/api/health", False)]),
        _step(2, "缺端点步骤", [("POST", "/api/ghost/x", False)]),
    ]}]
    findings, _ = au.check_endpoint_registry(
        chs=fake, registry=ch.load_registry())
    assert len(findings) == 1
    assert findings[0]["path"] == "/api/ghost/x"
    assert findings[0]["chain"] == "假想链" and findings[0]["step"] == 2


def test_all_hit_chain_passes_clean():
    """全 hit 构造链: 零检出(扫描回归口径)"""
    fake = [{"name": "健康链", "goal": "", "steps": [
        _step(1, "探活", [("GET", "/api/health", False)]),
    ]}]
    findings, total = au.check_endpoint_registry(
        chs=fake, registry=ch.load_registry())
    assert findings == [] and total["hit"] == 1 and total["miss"] == 0


def test_audit_run_prints_report(capsys):
    """run() 输出: 标题+miss 明细+汇总+后续任务提示"""
    assert au.run() == 0
    out = capsys.readouterr().out
    assert "端点在册" in out
    assert "/api/cart" in out and "miss=1" in out
    assert "T1.2 鉴权可达" in out
