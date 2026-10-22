"""zhuyuan probe 探针验收测试（T2.1 基座: 跑通线上详情页 / T2.2 转化探针: 真实页面正反例）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import probe as zp

# T2.2 反例页: 真实内容页(留言服务), 无商品卡容器→renderProductCard 不触发,
# 共享头尾亦不含购买按钮文案——干净的"无购物流程"负样本
NEG_URL = "https://zyjiu.com/service.html"
# T2.3 首页: hero 警示语+页脚资质三件套; 商品卡无产地字段
HOME_URL = "https://zyjiu.com/"

# CLI 子进程归档自清理(T1.3 同款差集法): probe 子进程不走 conftest 注入
_REPO = os.path.dirname(os.path.dirname(HERE))
_REPORTS = os.path.join(_REPO, "docs", "registry", "reports")


def _sweep_new_reports(before):
    for n in set(os.listdir(_REPORTS)) - before:
        os.remove(os.path.join(_REPORTS, n))


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
    """CLI 三探针端到端+报告归档(子进程归档自清理)"""
    import subprocess
    before = set(os.listdir(_REPORTS))
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"), "probe"],
                           check=False, capture_output=True, text=True, encoding="utf-8",
                           timeout=180)
        assert r.returncode == 0
        assert "探针成功" in r.stdout and "zyjiu.com" in r.stdout
        assert "✓" in r.stdout or "✗" in r.stdout
        assert "探针报告已归档" in r.stdout
    finally:
        _sweep_new_reports(before)


def test_t22_conversion_positive_live():
    """正例: 线上详情页购物流程关键元素齐备(价格/规格/数量/双购买按钮)"""
    r = zp.probe_conversion(url=zp.DEFAULT_URL, timeout=45000)
    assert r["ok"] is True
    assert r["pass"] is True, f"详情页不应缺关键元素: {r['missing']}"
    assert all(r["elements"].values())
    assert r["buy_box"] is not None, "立即购买按钮已渲染取盒"


def test_t22_conversion_negative_live():
    """反例: 真实内容页无购物流程——探针如实判 FAIL+P0 缺失明细"""
    r = zp.probe_conversion(url=NEG_URL, timeout=45000)
    assert r["ok"] is True
    assert r["pass"] is False, "内容页无购物流程, 应判 FAIL"
    assert not any(r["elements"].values()), "五关键元素应全数缺席"
    assert any(f["level"] == "P0" for f in r["findings"]), "缺购买按钮/价格=P0"


def test_t22_cli_conversion_negative():
    """CLI 反例: --url 指向无购物流程页面 → 退出码 1+FAIL 明细+P0 分级"""
    import subprocess
    before = set(os.listdir(_REPORTS))
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "cli.py"), "probe", "--url", NEG_URL],
            check=False, capture_output=True, text=True, encoding="utf-8", timeout=180)
        assert r.returncode == 1
        assert "转化结论: FAIL" in r.stdout
        assert "P0" in r.stdout
        assert "探针报告已归档" in r.stdout
    finally:
        _sweep_new_reports(before)


def test_t23_trust_detail_page():
    """信任探针·详情页(双页验证之一): 资质三件套(页脚)+产地✓; 评分缺失如实 P2"""
    r = zp.probe_trust(url=zp.DEFAULT_URL, timeout=45000)
    assert r["ok"] is True
    assert r["pass"] is True, f"资质三件套应齐: {r['missing']}"
    for k in ("icp", "license", "warning", "origin"):
        assert r["elements"][k] is True
    assert r["elements"]["rating"] is False, "真实 UI 无商品评分模块——如实缺失"
    assert any(f["level"] == "P2" for f in r["findings"])


def test_t23_trust_homepage():
    """信任探针·首页(双页验证之二): hero警示语+页脚三件套齐; 产地缺失如实 P1"""
    r = zp.probe_trust(url=HOME_URL, timeout=45000)
    assert r["ok"] is True
    assert r["pass"] is True, f"资质三件套应齐: {r['missing']}"
    for k in ("icp", "license", "warning"):
        assert r["elements"][k] is True
    assert r["elements"]["origin"] is False, "首页商品卡无产地字段——如实缺失"
    assert any(f["level"] == "P1" for f in r["findings"])


def test_t24_report_synthetic(tmp_path):
    """T2.4 报告生成(合成三探针数据, 离线): 四节齐+P0-P2 分级+通过率口径"""
    base = {"url": "https://example.com/p", "ok": True, "title": "样例页",
            "found": {"[class*='price']": True, "[class*='buy']": False},
            "shot": None}
    conv = {"url": "https://example.com/p", "ok": True, "pass": False,
            "title": "样例页",
            "elements": {"price_now": True, "spec_options": True,
                         "qty_input": False, "add_cart": True, "buy_now": False},
            "missing": ["qty_input", "buy_now"],
            "findings": [{"level": "P1", "msg": "购物流程缺失数量选择(qty_input)"},
                         {"level": "P0", "msg": "购物流程缺失立即购买按钮(buy_now)"}],
            "buy_first_screen": False, "buy_box": None, "shot": None}
    trust = {"url": "https://example.com/p", "ok": True, "pass": True,
             "title": "样例页",
             "elements": {"icp": True, "license": True, "warning": True,
                          "origin": True, "rating": False},
             "missing": ["rating"],
             "findings": [{"level": "P2", "msg": "信任要素缺失评分展示(rating)"}],
             "shot": None}
    path = zp.generate_probe_report(base, conv, trust, out_dir=str(tmp_path))
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    # 四节齐
    for sec in ("一、探针概况", "二、各探针通过率", "三、失败明细+P0-P2 分级",
                "四、处置建议(只出不施)"):
        assert sec in text
    # 通过率口径: 基座 1/2 / 转化 3/5 / 信任 4/5
    assert "1/2 (50%)" in text
    assert "3/5 (60%)" in text
    assert "4/5 (80%)" in text
    # P0-P2 分级齐+排序(P0 在 P1 前)+基座未命中记信息级
    assert "[P0]" in text and "[P1]" in text and "[P2]" in text
    assert text.index("[P0]") < text.index("[P1]") < text.index("[P2]")
    assert "信息级: 基座选择器未命中 [class*='buy']" in text
    # 概况如实: 转化 FAIL 带缺失明细; 信任 PASS
    assert "FAIL——缺失 qty_input, buy_now" in text
    assert "PASS——资质三件套齐备" in text
    # 处置建议按级聚合
    assert "P0(上线前必修" in text and "P1(排期修复" in text and "P2(人工裁量" in text
    assert "只出不施" in text
