"""zhuyan antipattern 反模式规则库验收测试（T2.3: B4 五类拦截 5/5）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import antipattern as zap


def test_t23_b4_replay_five():
    """验收核心: B4 五类已知触发例拦截 5/5=100%"""
    hits, _clean = zap.b4_replay()
    assert len(hits) == 5
    for rule, ok, got in hits:
        assert ok, f"{rule} 未拦截(实际 {got})"
    assert all(ok for _r, ok, _g in hits), "5/5=100% PASS"


def test_t23_b4_clean_zero_false_positive():
    """对照: 干净样本零误报"""
    _hits, clean = zap.b4_replay()
    assert clean == [], f"干净样本误报: {clean}"


def test_t23_line_rule_details():
    """行级规则取证: AP-1 单参 cache_set / AP-2 async 门槛"""
    hits = zap.scan_text("x", "def f():\n    cache_set('k', data)\n")
    assert hits[0][0] == "AP-1 过度缓存" and hits[0][1] == 2
    hits = zap.scan_text("x", "def f():\n    time.sleep(1)\n")
    assert not hits, "无 async def 上下文不触发 AP-2"
    hits = zap.scan_text("x", "async def g():\n    time.sleep(1)\n")
    assert any(h[0] == "AP-2 异步误用" for h in hits)


def test_t23_block_rule_details():
    """块级规则: for 循环+调用共现才触发(无 for 不触发)"""
    no_for = zap.scan_text("x", "def h():\n    db.execute(sql)\n")
    assert not any(h[0] == "AP-3 批量陷阱" for h in no_for), "无循环不触发"
    with_for = zap.scan_text("x", "def h(ids):\n    for i in ids:\n        db.execute(sql % i)\n")
    assert any(h[0] == "AP-3 批量陷阱" for h in with_for)


def test_t23_real_scan_runs():
    """真实代码全量快筛跑通(命中如实, 每规则去重)"""
    units = [u for u in __import__("fingerprint").enumerate_units()
             if u["unit_type"] == "endpoint"][:30]
    found = zap.scan_antipatterns(units, {})
    assert isinstance(found, list), "空缓存如实零命中不炸"
    src_ctx = {}
    import os
    for u in units:
        rel = u.get("file")
        if rel and rel not in src_ctx:
            full = os.path.join(
                __import__("fingerprint").REPO_ROOT, rel.replace("/", os.sep))
            if os.path.exists(full):
                with open(full, encoding="utf-8", errors="replace") as fh:
                    src_ctx[rel] = fh.read()
            else:
                src_ctx[rel] = ""
    found = zap.scan_antipatterns(units, src_ctx)
    rules = {f[1] for f in found}
    assert rules <= {"AP-1 过度缓存", "AP-2 异步误用", "AP-3 批量陷阱",
                     "AP-4 分布式滥用", "AP-5 日志风暴"}
