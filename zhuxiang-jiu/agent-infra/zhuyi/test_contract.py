"""zhuyi contract 前端调用面提取验收测试（T2.1: 对照真实页面抽验 10 调用）"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import contract as ct


def _calls():
    return {(c["method"], c["url"]): c for c in ct.extract_frontend_calls()}


def test_t21_qr_chain_six_calls():
    """qr 链六端点全提取(含 WebSocket ws), 模板化 {var}, 定位 entry-login.js"""
    calls = _calls()
    qr = [("POST", "/api/entry/qr/create"), ("GET", "/api/entry/qr/{var}/status"),
          ("POST", "/api/entry/qr/{var}/confirm"), ("POST", "/api/entry/qr/{var}/exchange"),
          ("POST", "/api/entry/qr/{var}/cancel"), ("WS", "/api/entry/qr/{var}/ws")]
    for key in qr:
        assert key in calls, f"qr 调用面缺失: {key}"
        assert calls[key]["file"] == "js/entry-login.js"
        assert calls[key]["line"] > 0


def test_t21_ten_calls_verified():
    """验收口径: 对照真实页面抽验 10 调用(跨 6 页面/5 js 文件)"""
    calls = _calls()
    expected = [
        ("POST", "/api/entry/qr/create"),          # entry-login.js 登录页
        ("POST", "/api/entry/qr/{var}/exchange"),
        ("WS", "/api/entry/qr/{var}/ws"),          # WebSocket 轮询(method=WS)
        ("GET", "/api/product/list"),               # products-live.js + 三 html
        ("GET", "/api/product/"),                   # 详情动态前缀(拼接变量)
        ("POST", "/api/order/price/preview"),       # cart.html 结算预览
        ("GET", "/api/community/feed"),             # news.html 社区
        ("POST", "/api/alliance/order"),            # alliance-mall.html 同盟
        ("POST", "/api/search-ai/query"),           # search.html 搜索
        ("POST", "/api/hub/route"),                 # ai-hub-widget.js 挂件
    ]
    for key in expected:
        assert key in calls, f"抽验调用缺失: {key}"


def test_t21_method_line_and_query_strip():
    """method 提取 + 行号定位 + 查询串剥离"""
    calls = _calls()
    ex = calls[("POST", "/api/entry/qr/{var}/exchange")]
    assert ex["line"] == 175
    pl = calls[("GET", "/api/product/list")]
    assert pl["file"] == "js/products-live.js"
    assert "?" not in pl["url"]  # ?page=1&page_size=100 已剥


def test_t21_cli_output():
    """CLI contract: 调用面计数+断契对齐+明细定位"""
    r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"), "contract"],
                       check=False, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0
    assert "T2.1 前端调用面" in r.stdout and "T2.2 断契对齐" in r.stdout
    assert "T2.3 死条目检测" in r.stdout
    # 真实断契明细带文件:行定位
    assert "/api/pay71" in r.stdout and "js/payport-dashboard.js:37" in r.stdout


# ---------- T2.2 断契对齐（验收: 构造断契例→检出） ----------

def test_t22_real_break_pay71():
    """真实断契如实检出: GET /api/pay71 前端调用后端无对应路由"""
    hits, breaks = ct.check_contract_break()
    assert len(hits) + len(breaks) == 45  # 去重形态守恒
    pay = next(b for b in breaks if b["url"] == "/api/pay71")
    assert pay["method"] == "GET"
    assert any("js/payport-dashboard.js" in f for f in pay["files"])


def test_t22_fixture_break_detected():
    """验收口径: 构造幽灵调用 → 检出断契"""
    fake_calls = [{"file": "ghost.html", "line": 1,
                   "method": "POST", "url": "/api/ghost/x"}]
    hits, breaks = ct.check_contract_break(calls=fake_calls)
    assert hits == set()
    assert len(breaks) == 1
    assert breaks[0]["url"] == "/api/ghost/x" and breaks[0]["registered"] == "无"


def test_t22_param_template_and_prefix_match():
    """模板段互认 + 前缀截断: qr {var}~{qr_id} 命中, /api/product/ 命中 {id}"""
    calls = [{"file": "a.js", "line": 1, "method": "POST",
              "url": "/api/entry/qr/{var}/exchange"},
             {"file": "b.js", "line": 2, "method": "GET", "url": "/api/product/"}]
    hits, breaks = ct.check_contract_break(calls=calls)
    assert breaks == []
    assert ("POST", "/api/entry/qr/{var}/exchange") in hits
    assert ("GET", "/api/product/") in hits


def test_t22_method_mismatch_flagged():
    """方法不符断契: POST /api/product/list(registry 为 GET)→检出且标注注册方法"""
    calls = [{"file": "c.js", "line": 3, "method": "POST",
              "url": "/api/product/list"}]
    hits, breaks = ct.check_contract_break(calls=calls)
    assert hits == set()
    assert len(breaks) == 1 and "GET" in breaks[0]["registered"]


def test_t22_ws_call_matches_ws_route():
    """WS 调用命中 WS 路由(extract 修正 method=WS)"""
    calls = ct.extract_frontend_calls()
    ws = [c for c in calls if c["url"] == "/api/entry/qr/{var}/ws"]
    assert ws and ws[0]["method"] == "WS"
    hits, _ = ct.check_contract_break(calls=ws)
    assert ("WS", "/api/entry/qr/{var}/ws") in hits
