"""zhuyi chains 解析器验收测试（T0.5: 6 链全解析 + 端点可对齐 registry）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import chains as ch


def test_parse_six_chains():
    cs = ch.parse_chains()
    assert [c["name"] for c in cs] == [
        "下单支付链", "扫码登录链", "评价双写链",
        "会员升档链", "分润结算链", "内容检测链"]
    assert all(c["goal"] for c in cs)


def test_chain_steps_count():
    cs = {c["name"]: c for c in ch.parse_chains()}
    assert len(cs["下单支付链"]["steps"]) == 8
    assert len(cs["扫码登录链"]["steps"]) == 6
    assert len(cs["评价双写链"]["steps"]) == 4


def test_qr_chain_endpoints_extract():
    cs = {c["name"]: c for c in ch.parse_chains()}
    eps = [(m, p) for s in cs["扫码登录链"]["steps"]
           for m, p, _ in s["endpoints"]]
    assert ("POST", "/api/entry/qr/create") in eps
    assert ("GET", "/api/entry/qr/{qr_id}/status") in eps
    assert ("POST", "/api/entry/qr/{qr_id}/exchange") in eps
    assert len(eps) == 6


def test_timeout_expansion_and_multiform():
    cs = {c["name"]: c for c in ch.parse_chains()}
    steps = {s["no"]: s for s in cs["下单支付链"]["steps"]}
    t = [(m, p) for m, p, _ in steps[8]["endpoints"]]
    assert ("POST", "/api/order/{order_id}/timeout/confirm") in t
    assert ("POST", "/api/order/{order_id}/timeout/close") in t
    assert ("POST", "/api/order/{order_id}/timeout/complete") in t
    # 拍板① cart 统一层为 ambiguous 形态("系列") → 降级标记
    assert all(a for m, p, a in steps[2]["endpoints"])


def test_internal_and_pending_marks():
    by_name = {c["name"]: c for c in ch.parse_chains()}
    review = {s["no"]: s for s in by_name["评价双写链"]["steps"]}
    assert review[3]["internal"] and not review[3]["endpoints"]
    upgrade = {s["no"]: s for s in by_name["会员升档链"]["steps"]}
    assert upgrade[2]["pending"]  # audit-1 待核标记
    settle = {s["no"]: s for s in by_name["分润结算链"]["steps"]}
    assert settle[2]["pending"]  # audit-2 待核标记


def test_align_registry_real():
    """真 registry 对齐: 扫码登录 6 端点全 hit; miss 项如实呈现(=T1.1 审查发现)"""
    cs = ch.parse_chains()
    reg = ch.load_registry()
    rep, total = ch.align_registry(cs, reg)
    qr = rep["扫码登录链"]
    qr_all = [r for s in qr.values() for r in s["results"]]
    assert len(qr_all) == 6 and all(r[0] == "hit" for r in qr_all)
    # 三分类计数守恒: hit+partial+miss = 提取到的标准端点总数
    n_eps = sum(len(s["endpoints"]) for c in cs for s in c["steps"])
    assert total["hit"] + total["partial"] + total["miss"] == n_eps
    # 汇总行可读(打印自检口径)
    assert total["hit"] >= 20  # 主体端点(订单/支付/qr/评价/promo)均应命中
