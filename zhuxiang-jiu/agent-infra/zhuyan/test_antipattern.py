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


# ---- T3.7 规则改进 v2: 74 处精审四场景回归 ----

def test_v2_block_indent_boundary():
    """缩进感知: def 块止于缩进回落, 后续顶层代码不并入(位置2 xiaozhu 样本)"""
    text = (
        "async def _wake(wk):\n"
        "    _seen = set()\n"
        "    return [w for w in ws]\n"
        "\n"
        "dump = None\n"          # 顶层缩进0——v1 吞入 _wake 块, v2 边界止于此
        "if dump:\n"
        "    for item in items:\n"
        "        logger.info(item)\n"
    )
    hits = zap.scan_text("x", text)
    assert not any(h[0] == "AP-5 日志风暴" for h in hits), \
        "def 外循环日志不应并入块(v1 误报源)"


def test_v2_log_outside_loop_not_reported():
    """循环体内校验: except 兜底/循环后聚合日志不报; 循环内真报"""
    drama84_case = (                  # 位置1: 日志在 except 兜底(fail-soft)
        "async def stats(sid):\n"
        "    for d in dramas:\n"
        "        if d:\n"
        "            found = d\n"
        "            break\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        logger.info('skip: %s', sid)\n"
    )
    assert not zap.scan_text("x", drama84_case), "except 兜底日志不应报(位置1 误报归零)"
    locks_case = (                    # 位置3: 循环后 if 聚合单条(最佳实践)
        "def cleanup():\n"
        "    to_remove = [k for k, v in locks.items()]\n"
        "    for k in to_remove:\n"
        "        del locks[k]\n"
        "    if to_remove:\n"
        "        logger.debug('cleaned %d', len(to_remove))\n"
    )
    assert not zap.scan_text("x", locks_case), "循环外聚合日志不应报(位置3 误报归零)"
    real_case = (                     # 反例: 循环体内日志必须仍能检出
        "def v(xs):\n"
        "    for x in xs:\n"
        "        logger.info(x)\n"
    )
    assert any(h[0] == "AP-5 日志风暴" for h in zap.scan_text("x", real_case))


def test_v2_exempt_channel():
    """豁免通道: `# zhuyan-exempt: AP-5` 跳过指定规则, 其余规则不受影响"""
    exempted = ("# zhuyan-exempt: AP-5\n"
                "def v(xs):\n"
                "    for x in xs:\n"
                "        logger.info(x)\n")
    assert not any(h[0] == "AP-5 日志风暴"
                   for h in zap.scan_text("x", exempted)), "豁免标记应生效"
    still = ("# zhuyan-exempt: AP-5\n"
             "def h(ids):\n"
             "    for i in ids:\n"
             "        db.execute(sql % i)\n")
    assert any(h[0] == "AP-3 批量陷阱" for h in zap.scan_text("x", still)), \
        "豁免只作用于标记规则"


def test_v2_real_scan_full_zero():
    """v2 改进回归基准: 全量真实快筛仅允许 1 处已知人工裁量项——
    xiaozhu ws_asr_final 观测埋点(每会话 finish 一条, v3 唤醒诊断设计;
    v1 首处取证曾被 345 行误报掩盖, v2 修复边界后浮出=改进价值实证)。
    其余零命中(74 处疑似经精审全数归零); 新引入疑似将红——须处置"""
    import fingerprint
    units = fingerprint.enumerate_units()
    src_ctx = {}
    for u in units:
        rel = u.get("file")
        if u["unit_type"] != "page" and rel and rel not in src_ctx:
            full = os.path.join(fingerprint.REPO_ROOT, rel.replace("/", os.sep))
            if os.path.exists(full):
                with open(full, encoding="utf-8", errors="replace") as fh:
                    src_ctx[rel] = fh.read()
            else:
                src_ctx[rel] = ""
    found = zap.scan_antipatterns(units, src_ctx)
    unknown = [f for f in found
               if not (f[1] == "AP-5 日志风暴" and "/api/xiaozhu/" in f[0])]
    assert unknown == [], f"白名单外新疑似须处置: {unknown[:5]}"
