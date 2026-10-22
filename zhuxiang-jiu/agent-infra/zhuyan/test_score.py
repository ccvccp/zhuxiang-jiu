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


# ---------- T1.2 S_r 运行稳健(静态代理) ----------

_SR_SAMPLE = '''\
def a(x):
    try:
        return retry_call(x)
    except ValueError:
        raise

def b(y):
    return y

def c(z):
    assert isinstance(z, int), "校验"
    return z

def lock_guard(w):
    with threading.Lock():
        return w
'''


def test_t12_sr_synthetic_evidence():
    """合成取证: 四子指标归一正确(异常 1/4+容错 retry+校验 1/4+并发 Lock)"""
    ctx = {"backend/core/sample.py": _SR_SAMPLE}
    unit = {"unit_id": "core/sample.py", "unit_type": "module",
            "file": "backend/core/sample.py"}
    s, ev = zs.sr_unit(unit, ctx)
    assert ev["exception_rate"] == 0.25, "4 块中仅 a 块含 try/raise"
    assert ev["fault_features"] == ["retry"], "1 类容错特征=0.5 档"
    assert ev["validation_rate"] == 0.25, "4 块中 1 块含校验特征"
    assert ev["concurrency"] == 1.0, "threading.Lock 文件级命中"
    expect = round(0.35 * 0.25 + 0.30 * 0.5 + 0.20 * 0.25 + 0.15 * 1.0, 2)
    assert s == expect, f"权重合成 {expect}"
    assert "APM 缺位" in ev["static_proxy"], "静态代理如实标注"


def test_t12_sr_real_core_evidence():
    """验收口径: 特征正则对 core 真实代码取证——locks.py 并发特征天然命中"""
    units = [u for u in __import__("fingerprint").enumerate_units()
             if u["unit_type"] == "module"]
    ctx = zs.build_sr_context(units)
    locks = next(u for u in units if u["unit_id"] == "core/locks.py")
    s, ev = zs.sr_unit(locks, ctx)
    assert ev["concurrency"] == 1.0, "locks.py 锁模块并发特征必命中"
    assert s >= 0.15
    assert 0.0 <= ev["exception_rate"] <= 1.0, "真实取证比率界内"


def test_t12_sr_page_and_missing_file():
    """页面无 Python 运行面如实 0; 源文件缺失如实 0"""
    page = {"unit_id": "index.html", "unit_type": "page", "file": "index.html"}
    s, ev = zs.sr_unit(page, {})
    assert s == 0.0 and ev["note"]
    gone = {"unit_id": "X /api/none", "unit_type": "endpoint",
            "file": "backend/routes/none.py"}
    s, ev = zs.sr_unit(gone, {"backend/routes/none.py": ""})
    assert s == 0.0 and "缺失" in ev["note"]


def test_t12_sr_fault_kinds_normalization():
    """容错特征档位: 0 类=0 / 1 类=0.5 / ≥2 类=1.0"""
    cases = [
        (_SR_SAMPLE, 0.5),  # retry 一类
        ("def f():\n    return timeout_call()\n", 0.5),  # timeout 一类
        ("def f():\n    retry\n    超时\n", 1.0),  # 两类
        ("def f():\n    return 1\n", 0.0),  # 无
    ]
    for text, expect in cases:
        ctx = {"m.py": text}
        unit = {"unit_id": "core/m.py", "unit_type": "module", "file": "m.py"}
        _s, ev = zs.sr_unit(unit, ctx)
        n = len(ev["fault_features"])
        fault_v = 0.0 if n == 0 else (0.5 if n == 1 else 1.0)
        assert abs(fault_v - expect) < 0.01, f"特征 {ev['fault_features']} 档位"


# ---------- T1.3 S_e 工程复用 ----------

def _fake_se_ctx(src=None, test_asserts=None):
    return {"src": src or {}, "test_asserts": test_asserts or {}}


def _ep_unit(tests=None, **kw):
    u = {"unit_id": "POST /api/x", "unit_type": "endpoint",
         "file": "backend/routes/x.py", "docstring_rate": 0.5,
         "imports": 3, "tests": tests or []}
    u.update(kw)
    return u


def test_t13_test_baseline_boost():
    """验收核心: qr/b1-b3 基线测试命中加权 1.5 封顶 1.0"""
    base = "backend/test_qr_public_baseline.py"
    # 基线+断言 10 → 0.7×1.5=1.05 封顶 1.0
    ctx = _fake_se_ctx(test_asserts={base: 10})
    _s, ev = zs.se_unit(_ep_unit(tests=[base]), ctx)
    assert ev["test_value"] == 1.0 and ev["baseline_boost"] is True
    # 非基线+断言 10 → 0.7 无加权
    other = "backend/test_x.py"
    ctx = _fake_se_ctx(test_asserts={other: 10})
    _s, ev = zs.se_unit(_ep_unit(tests=[other]), ctx)
    assert ev["test_value"] == 0.7 and ev["baseline_boost"] is False
    # 基线+低断言 2 → 0.5×1.5=0.75
    ctx = _fake_se_ctx(test_asserts={base: 2})
    _s, ev = zs.se_unit(_ep_unit(tests=[base]), ctx)
    assert ev["test_value"] == 0.75
    # 无测试 → 0
    _s, ev = zs.se_unit(_ep_unit(), _fake_se_ctx())
    assert ev["test_value"] == 0.0


def test_t13_coup_and_syntax_norm():
    """低耦合四档+规范性 compile 代理(坏语法=0)"""
    for imports, expect in ((0, 1.0), (2, 0.7), (5, 0.4), (9, 0.1)):
        assert zs._coup_norm(imports) == expect
    good = _fake_se_ctx(src={"backend/routes/x.py": "def a():\n    return 1\n"})
    _s, ev = zs.se_unit(_ep_unit(), good)
    assert ev["syntax_ok"] is True
    bad = _fake_se_ctx(src={"backend/routes/x.py": "def a(:\n"})
    _s, ev = zs.se_unit(_ep_unit(), bad)
    assert ev["syntax_ok"] is False, "语法坏→规范性 0"


def test_t13_page_se():
    """页面 S_e: SEO 契约近似(双 True=1.0/单 True=0.5)+测试规范性如实 0"""
    page = {"unit_id": "index.html", "unit_type": "page", "imports": 0,
            "contract": {"has_title": True, "has_description": True}}
    s, ev = zs.se_unit(page, _fake_se_ctx())
    assert ev["contract"] == 1.0
    expect = round(0.30 * 0 + 0.30 * 1.0 + 0.25 * 1.0 + 0.15 * 0, 2)
    assert s == expect, "页面=契约 0.3+低耦合 0.25"
    half = {"unit_id": "a.html", "unit_type": "page", "imports": 0,
            "contract": {"has_title": True, "has_description": False}}
    _s, ev = zs.se_unit(half, _fake_se_ctx())
    assert ev["contract"] == 0.5


def test_t13_real_qr_baseline():
    """真实回放: qr 扫描链端点关联基线测试(竹医 qr 基线加权生效)"""
    import fingerprint as zf
    units = zf.enumerate_units()
    ctx = zs.build_se_context(units)
    qr = next(u for u in units
              if u["unit_id"] == "POST /api/entry/qr/{qr_id}/scan")
    s, ev = zs.se_unit(qr, ctx)
    assert ev["baseline_boost"] is True, "qr 端点应关联 qr 公开基线测试"
    assert ev["test_value"] == 1.0, "基线断言量×1.5 封顶"
    assert s > 0.5, "qr 端点 S_e 高分(B3 高分样本)"


# ---------- T1.4 罚分+总分+报告 ----------

def test_t14_penalty_veto_and_items():
    """罚分取证: 硬否决(密钥字面量)+三重扣(无测试/SQL 拼接/危险封顶)"""
    secret_src = {"backend/routes/x.py": 'api_key = "sk-live-123"\n'}
    _p, ev = zs.penalty_unit(_ep_unit(), secret_src)
    assert ev["vetoed"] is True and "硬否决" in ev["items"][0]
    # 无测试 −0.20(有 SQL f-string 再 −0.20 = 0.40)
    sql_src = {"backend/routes/x.py":
               "def f():\n    sql = f\"SELECT * FROM t WHERE id={i}\"\n"}
    p, ev = zs.penalty_unit(_ep_unit(), sql_src)
    assert abs(p - 0.40) < 0.01 and not ev["vetoed"]
    assert any("SQL" in it for it in ev["items"])
    # 危险模式 3 处 → 0.45 封顶 0.30; 无测试 → 共 0.50
    danger_src = {"backend/routes/x.py":
                  "eval('1')\nexec('2')\nsubprocess.run(c, shell=True)\n"}
    p, ev = zs.penalty_unit(_ep_unit(), danger_src)
    assert abs(p - 0.50) < 0.01, "0.20 无测试+0.30 危险封顶"
    # 有测试无风险 → 0
    good = _ep_unit(tests=["backend/test_x.py"])
    p, ev = zs.penalty_unit(good, {"backend/routes/x.py": "def f():\n    return\n"})
    assert p == 0.0 and ev["items"] == []


def test_t14_maturity_total_and_grade():
    """总分公式+分档: 三维加权−罚分; 硬否决直判 low"""
    m = zs.maturity_unit(_ep_unit(tests=["backend/test_x.py"]))
    expect = round(0.40 * m["s_b"] + 0.35 * m["s_r"] + 0.25 * m["s_e"]
                   - m["penalty"], 2)
    assert m["total"] == max(expect, 0.0)
    assert m["grade"] in ("high", "mid", "low")
    veto_unit = _ep_unit()
    veto_unit["file"] = "backend/routes/x.py"
    m = zs.maturity_unit(veto_unit, None,
                          {"backend/routes/x.py": 'password = "x"\n'}, None)
    assert m["total"] == 0.0 and m["grade"] == "low", "硬否决直判 0 档"


def test_t14_b3_replay():
    """B3 基准: 已知答案分档回放——成熟端点不落 low+缺口页必 low"""
    hits, misses = zs.b3_replay()
    assert not misses, f"B3 FAIL: {misses}"
    assert len(hits) == 2 and "qr scan" in hits[0] and "activity-detail" in hits[1]


def test_t14_report(tmp_path):
    """评分报告四节+每分证据链口径(隔离目录生成)"""
    units = __import__("fingerprint").enumerate_units()
    ctxs = (zs.build_sb_context(), zs.build_sr_context(units),
            zs.build_se_context(units))
    scored = [zs.maturity_unit(u, *ctxs) for u in units[:50]]
    path = zs.generate_report(scored, out_dir=str(tmp_path))
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for sec in ("一、评分总览", "二、分档清单", "三、罚分明细", "四、B3 分档回放"):
        assert sec in text
    assert "S=0.40S_b" in text and "只出不施" in text
    assert "静态代理" in text or "静态近似" in text, "口径声明"
