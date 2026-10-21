"""zhuyuan probe 探针基座验收测试（T2.1: 探针跑通线上详情页）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import probe as zp


def test_t21_probe_live_detail_page():
    """验收口径: 线上详情页探针跑通——渲染完成+标题+选择器定位"""
    r = zp.probe_page(
        url=zp.DEFAULT_URL,
        selectors=("[class*='price']", "[class*='btn']"),
        timeout=45000)
    assert r["ok"] is True
    assert "zyjiu.com" in r["url"]
    assert r["title"], "标题非空=渲染完成"
    assert any(r["found"].values()), "详情页必有价格/按钮类元素"


def test_t21_shot_written():
    """首屏截图落盘(微信 UA 视口, 整改截图实战同款)"""
    r = zp.probe_page(url=zp.DEFAULT_URL, selectors=(), timeout=45000,
                     shot_name="t21-shot-test.png")
    assert r["shot"] and os.path.exists(r["shot"])
    assert os.path.getsize(r["shot"]) > 10000, "截图非空"
    os.remove(r["shot"])


def test_t21_cli_probe():
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"), "probe"],
                       check=False, capture_output=True, text=True, encoding="utf-8",
                       timeout=120)
    assert r.returncode == 0
    assert "探针成功" in r.stdout and "zyjiu.com" in r.stdout
    assert "✓" in r.stdout or "✗" in r.stdout
