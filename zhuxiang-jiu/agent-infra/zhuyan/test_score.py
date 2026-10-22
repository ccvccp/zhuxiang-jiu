"""zhuyan score 业务价值验收测试（T1.1: S_b 三子指标+6 链命中归一正确）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import score as zs

_QR_SCAN = {"unit_id": "POST /api/entry/qr/{qr_id}/scan", "unit_type": "endpoint"}


def _fake_ctx(chains_index=None, calls=None):
    """合成 ctx(链倒排+调用面), 归一逻辑与真实 build_sb_context 同构。"""
    return {"chains": chains_index or {}, "calls": calls or []}


def test_t11_chain_hit_normalization():
    """验收核心: 端点在 6 链命中归一正确(0/0.7/1.0 三档)"""
    # 0 链
    s, ev = zs.sb_unit(_QR_SCAN, _fake_ctx())
    assert s == 0.0 and ev["hit_chains"] == []
    # 1 链(扫码登录链): 链 0.7×0.4 + 其他链转化 0.5×0.3 = 0.43
    ctx = _fake_ctx({("POST", "/api/entry/qr/{qr_id}/scan"): {"链二：扫码登录链"}})
    s, ev = zs.sb_unit(_QR_SCAN, ctx)
    assert s == 0.43, "链 0.7×0.4 + 其他链转化 0.5×0.3"
    assert ev["hit_chains"] == ["链二：扫码登录链"]
    # ≥2 链
    ctx = _fake_ctx({("POST", "/api/entry/qr/{qr_id}/scan"):
                     {"链一：下单支付链", "链二：扫码登录链"}})
    s, _ev = zs.sb_unit(_QR_SCAN, ctx)
    assert s == 0.7, "2 链归一 1.0×0.4 + 下单支付链转化 1.0×0.3 = 0.7"


def test_t11_chain_hit_two_chains_value():
    """2 链端点精确值: 链 1.0×0.4 + 转化 1.0×0.3 = 0.7(无调用)"""
    ctx = _fake_ctx({("POST", "/api/entry/qr/{qr_id}/scan"):
                     {"链一：下单支付链", "链六：内容检测链"}})
    s, _ev = zs.sb_unit(_QR_SCAN, ctx)
    assert s == 0.7, "下单支付链在 2 链中→转化 1.0"


def test_t11_call_count_thresholds():
    """调用频次四档: 0=0 / 1-2=0.4 / 3-5=0.7 / ≥6=1.0"""
    base = {"method": "POST", "url": "/api/entry/qr/{qr_id}/scan"}
    for n, expect in ((0, 0.0), (1, 0.4), (2, 0.4), (3, 0.7), (5, 0.7), (6, 1.0)):
        calls = [dict(base)] * n
        s, ev = zs.sb_unit(_QR_SCAN, _fake_ctx(calls=calls))
        assert ev["call_count"] == n
        assert abs(s - 0.3 * expect) < 0.01, f"调用 {n} 处档位 {expect}"


def test_t11_page_and_module():
    """页面: 转化三页 1.0/其他页 0.5; 模块: 三维如实 0"""
    pd = {"unit_id": "product-detail.html", "unit_type": "page"}
    s, ev = zs.sb_unit(pd, _fake_ctx())
    assert ev["conversion"] == 1.0 and abs(s - 0.3) < 0.01, "转化页 1.0×0.3"
    other = {"unit_id": "news.html", "unit_type": "page"}
    s, ev = zs.sb_unit(other, _fake_ctx())
    assert ev["conversion"] == 0.5 and abs(s - 0.15) < 0.01, "其他页 0.5×0.3"
    mod = {"unit_id": "core/locks.py", "unit_type": "module"}
    s, ev = zs.sb_unit(mod, _fake_ctx())
    assert s == 0.0 and ev["call_count"] == 0, "模块无维度如实计 0"


def test_t11_real_smoke():
    """真实数据冒烟: qr 扫描链端点链命中(竹医业务链定义权威源)"""
    ctx = zs.build_sb_context()
    assert ctx["chains"], "6 链倒排非空"
    qr_key = ("POST", "/api/entry/qr/{qr_id}/scan")
    assert qr_key in ctx["chains"], "qr scan 端点应命中扫码登录链"
    assert any("扫码登录链" in n for n in ctx["chains"][qr_key])
    s, ev = zs.sb_unit(_QR_SCAN, ctx)
    assert s > 0, "链命中即 S_b>0"
    assert ev["hit_chains"]
