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
          ("POST", "/api/entry/qr/{var}/cancel"), ("GET", "/api/entry/qr/{var}/ws")]
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
        ("GET", "/api/entry/qr/{var}/ws"),          # WebSocket 轮询
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
    """CLI contract: 调用面计数+去重形态+后续任务提示"""
    r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"), "contract"],
                       check=False, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0
    assert "前端调用面" in r.stdout and "去重端点形态" in r.stdout
    assert "T2.2 断契对齐" in r.stdout
    assert "js/entry-login.js:141" in r.stdout  # 明细带文件:行定位
