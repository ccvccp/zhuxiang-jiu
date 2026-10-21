"""zhuyi audit 第一检验收测试（T1.1: 端点在册检查——构造缺端点链→检出）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import audit as au
import chains as ch


def _step(no, label, eps, internal=False, pending=False, fields=""):
    return {"no": no, "label": label, "endpoints": eps,
            "internal": internal, "pending": pending,
            "pre": "", "fields": fields}


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


# ---------- T1.3 前端调用点（验收: 抽端点正反例） ----------

def test_t13_called_qr_pc_side():
    """正例: qr 链 PC 侧五端点在 entry-login.js 有调用点(含带参拼接写法)"""
    call_map, _ = au.check_frontend_calls()
    for method, path in [("POST", "/api/entry/qr/create"),
                         ("GET", "/api/entry/qr/{qr_id}/status"),
                         ("POST", "/api/entry/qr/{qr_id}/exchange"),
                         ("POST", "/api/entry/qr/{qr_id}/cancel"),
                         ("POST", "/api/entry/qr/{qr_id}/confirm")]:
        assert (method, path) in call_map, f"qr 端点未检出调用点: {path}"
        assert "js/entry-login.js" in call_map[(method, path)]
    assert len(call_map) >= 8


def test_t13_called_param_and_product_list():
    """正例: 带参尾段引号匹配(decide)与静态完整路径(product/list)"""
    call_map, _ = au.check_frontend_calls()
    assert ("POST", "/api/promo/decisions/{hotspot_id}/decide") in call_map
    assert ("GET", "/api/product/list") in call_map
    assert ("POST", "/api/promo/radar/scan") in call_map


def test_t13_no_call_transaction_side():
    """反例: 交易类端点主站无调用点(taro 端/服务间)——如实报 no_call"""
    _, no_call = au.check_frontend_calls()
    paths = {f["path"] for f in no_call}
    assert "/api/checkout/submit" in paths          # 结算在 taro 端
    assert "/api/payment/callback/pay" in paths     # 渠道回调服务间
    assert "/api/order/{order_id}/pay" in paths
    ex = next(f for f in no_call if f["path"] == "/api/checkout/submit")
    assert ex["chain"] == "下单支付链" and ex["step"] == 3
    # 边界: '/pay' 尾段不误命中 '/payment/'——order pay 与 callback 各自独立报告
    assert any(f["path"] == "/api/order/{order_id}/pay" for f in no_call)
    assert not any(f["path"] == "/api/payment/callback/pay" and f["path"].startswith("/api/order")
                   for f in no_call)


def test_t13_call_tokens_unit():
    """token 生成单元: 无参=完整路径; 带参=首段+尾段; 尾参数端=首段"""
    assert au._call_tokens("/api/product/list") == ("/api/product/list", None)
    assert au._call_tokens("/api/entry/qr/{qr_id}/scan") == ("/api/entry/qr/", "/scan")
    assert au._call_tokens("/api/order/{order_id}/pay") == ("/api/order/", "/pay")
    assert au._call_tokens("/api/product/{id}") == ("/api/product/", None)


# ---------- T1.4 数据流衔接（从简——关键 ID 抽验） ----------

def test_t14_qr_chain_clean():
    """M1 基准链数据流完整: 扫码登录链 qr_id 全程衔接, 0 gap"""
    gaps = au.check_dataflow()
    assert not any(g["chain"] == "扫码登录链" for g in gaps)


def test_t14_known_gaps_reported():
    """真实审计发现如实报出: 4 个已知 gap(链定义完善项/外部输入样例)"""
    gaps = au.check_dataflow()
    got = {(g["chain"], g["token"]) for g in gaps}
    assert ("下单支付链", "address_id") in got          # 外部输入: 用户地址簿
    assert ("评价双写链", "product_id") in got           # 链定义缺字段: confirm 应带订单上下文
    assert ("评价双写链", "member_id") in got
    assert ("内容检测链", "hotspot_id") in got            # hotspots 集合→单体选择隐含
    assert len(gaps) == 4


def test_t14_fixture_orphan_detected():
    """验收口径: 构造断流链(凭空关键 ID) → 检出 dataflow_gap"""
    fake = [{"name": "断流链", "goal": "", "steps": [
        _step(1, "起点", [], fields="a_id → b_id"),
        _step(2, "凭空消费", [], fields="ghost_id → c_id"),
    ]}]
    gaps = au.check_dataflow(chs=fake)
    assert len(gaps) == 1 and gaps[0]["token"] == "ghost_id"
    assert gaps[0]["step"] == 2 and gaps[0]["chain"] == "断流链"


def test_t14_first_step_exempt_and_continuity():
    """链首外部输入豁免 + 既有 ID 延续不误报(order_id 贯穿)"""
    fake = [{"name": "延续链", "goal": "", "steps": [
        _step(1, "首步外部输入", [], fields="x_id → order_id"),
        _step(2, "贯穿使用", [], fields="order_id → pay_no"),
        _step(3, "再贯穿", [], fields="order_id → 终态"),
    ]}]
    assert au.check_dataflow(chs=fake) == []  # x_id 链首豁免, order_id 全程延续


# ---------- T1.2 鉴权可达（qr benchmark 核心） ----------

def test_t12_current_registry_all_pass():
    """当前 registry: 六链鉴权期望×实际面全一致(e73f6800 修复后 qr 三步已公开·POST正则)"""
    assert au.check_auth_reachable() == []


def test_t12_qr_dead_fixture_exchanges_401():
    """验收核心: 死条目 fixture → 扫码登录链 scan/exchange/cancel 检出 401 永拦"""
    dead = au.build_qr_dead_registry()
    findings = au.check_auth_reachable(registry=dead)
    assert len(findings) == 3
    assert all(f["kind"] == "auth_401" for f in findings)
    paths = {f["path"] for f in findings}
    assert paths == {"/api/entry/qr/{qr_id}/scan",
                     "/api/entry/qr/{qr_id}/exchange",
                     "/api/entry/qr/{qr_id}/cancel"}
    ex = next(f for f in findings if f["path"].endswith("/exchange"))
    assert ex["chain"] == "扫码登录链" and ex["step"] == 5  # exchange 步骤标 401 永拦
    assert ex["actual"] == "登录·JWT"


def test_t12_auth_exposed_flagged():
    """反向构造: confirm(链上声明登录·JWT设计内)被改为公开 → 过度暴露检出"""
    reg = au.build_qr_dead_registry()  # 复用副本改造
    for e in reg["endpoints"]:
        if e["path"].endswith("/confirm") and e["path"].startswith("/api/entry/qr/"):
            e["auth"] = "公开·EXACT"
    findings = au.check_auth_reachable(registry=reg)
    exposed = [f for f in findings if f["kind"] == "auth_exposed"]
    assert len(exposed) == 1
    assert exposed[0]["step"] == 4 and exposed[0]["path"].endswith("/confirm")
