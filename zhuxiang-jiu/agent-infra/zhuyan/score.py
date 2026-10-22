"""zhuyan score: 成熟度评分（81号·T1.1 S_b + T1.2 S_r 维度先行）

S_b = 0.40×核心链关联 + 0.30×调用频次(静态代理) + 0.30×转化关联
S_r = 0.35×异常处理 + 0.30×容错特征 + 0.20×输入校验 + 0.15×并发安全
      (静态代理: APM 缺位, 代码特征近似, 如实标注)
口径: docs/registry/maturity/成熟度评分标准.md(归一化阈值表);
S_e(T1.3)/罚分总分(T1.4)逐任务扩展。
数据源复用: 链倒排=竹医 chains.parse_chains(业务链定义.md 权威);
调用面=竹医 contract.extract_frontend_calls+_url_matches(模板互认)。
"""
import os
import re
from collections import Counter

try:
    from zhuyi import chains as chains_mod
    from zhuyi import contract as contract_mod

    from zhuyan import fingerprint
except ImportError:
    import os
    import sys
    _pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pkg not in sys.path:
        sys.path.insert(0, _pkg)
    from zhuyi import chains as chains_mod
    from zhuyi import contract as contract_mod

    from zhuyan import fingerprint

# 转化关联页(下单支付链步骤页: 详情/购物车/结算)
_CONV_PAGES = ("product-detail.html", "cart.html", "checkout.html")
# 链关联归一: 0 链=0 / 1 链=0.7 / ≥2 链=1.0(评分标准口径)
# 调用频次归一: 0=0 / 1-2 处=0.4 / 3-5 处=0.7 / ≥6 处=1.0(评分标准口径)

# S_r 容错特征类(静态代理): 命中 1 类=0.5 / ≥2 类=1.0
_SR_FAULT = ("retry", "重试", "fallback", "降级", "timeout", "超时",
             "幂等", "idempotent", "防重放", "replay")
# S_r 输入校验特征(块级命中比率)
_SR_VALID = ("required", "validate", "isinstance", "必填", "校验", "assert ")
# S_r 并发安全特征(文件级命中=1.0; Lock 类名形态防 block 子串误命中)
_SR_CONC = ("Lock", "锁", "atomic", "threading", "setnx")
_DEF_LINE_RE = re.compile(r"^\s*def\s+\w+")


def build_sb_context(chains=None, calls=None):
    """S_b 数据源一次性构建: 链端点倒排 + 前端调用面。

    返回 {"chains": {(method, path): {链名...}}, "calls": [...]}。
    """
    chains = chains if chains is not None else chains_mod.parse_chains()
    calls = calls if calls is not None else contract_mod.extract_frontend_calls()
    index = {}
    for ch in chains:
        for step in ch["steps"]:
            for method, path, _amb in step["endpoints"]:
                index.setdefault((method, path), set()).add(ch["name"])
    return {"chains": index, "calls": calls}


def _call_norm(cnt):
    if cnt == 0:
        return 0.0
    if cnt <= 2:
        return 0.4
    if cnt <= 5:
        return 0.7
    return 1.0


def sb_unit(unit, ctx=None):
    """单功能单元 S_b → (0-1 分, 证据 dict)。全静态取证, 无维度如实计 0。

    - 端点: 链命中数归一 ×0.4 + 前端调用点计数归一(模板互认) ×0.3
      + 转化关联(命中下单支付链=1.0/其他链=0.5/无链=0) ×0.3
    - 页面: 链=0 如实 + 页内调用点计数 ×0.3 + 转化页 1.0/其他页 0.5 ×0.3
    - 模块: 三维皆 0 如实(无链/调用面/页面维度)
    """
    ctx = ctx or build_sb_context()
    ev = {}
    if unit["unit_type"] == "endpoint":
        method, path = unit["unit_id"].split(" ", 1)
        hit_chains = ctx["chains"].get((method, path), set())
        chain_v = 0.0 if not hit_chains else (0.7 if len(hit_chains) == 1 else 1.0)
        cnt = sum(1 for c in ctx["calls"]
                  if c["method"] == method
                  and contract_mod._url_matches(c["url"], path))
        conv = (1.0 if any("下单支付链" in n for n in hit_chains)
                else (0.5 if hit_chains else 0.0))
        ev = {"hit_chains": sorted(hit_chains), "call_count": cnt,
              "conversion": conv}
    elif unit["unit_type"] == "page":
        chain_v = 0.0
        cnt = sum(1 for c in ctx["calls"] if c["file"] == unit["unit_id"])
        conv = 1.0 if unit["unit_id"] in _CONV_PAGES else 0.5
        ev = {"hit_chains": [], "call_count": cnt, "conversion": conv}
    else:
        chain_v, cnt, conv = 0.0, 0, 0.0
        ev = {"hit_chains": [], "call_count": 0, "conversion": 0.0}
    s = round(0.4 * chain_v + 0.3 * _call_norm(cnt) + 0.3 * conv, 2)
    return s, ev


def _def_blocks(text):
    """按 def 行切块 → [(起始行号, 块文本)](行级切分, 静态近似)。"""
    lines = text.splitlines()
    blocks, cur = [], None
    for i, ln in enumerate(lines, 1):
        if _DEF_LINE_RE.match(ln):
            if cur:
                blocks.append(cur)
            cur = (i, [ln])
        elif cur is not None:
            cur[1].append(ln)
    if cur:
        blocks.append(cur)
    return [(b[0], "\n".join(b[1])) for b in blocks]


def build_sr_context(units=None, repo_root=None):
    """S_r 源文件文本缓存(端点/模块单元 → {rel: text})。

    页面单元无 Python 运行面不入缓存(sr_unit 对页面如实计 0)。
    """
    units = units if units is not None else fingerprint.enumerate_units()
    repo_root = repo_root or fingerprint.REPO_ROOT
    cache = {}
    for u in units:
        rel = u.get("file")
        if (u["unit_type"] in ("endpoint", "module") and rel
                and rel not in cache):
            full = os.path.join(repo_root, rel.replace("/", os.sep))
            if os.path.exists(full):
                with open(full, encoding="utf-8", errors="replace") as fh:
                    cache[rel] = fh.read()
            else:
                cache[rel] = ""
    return cache


def sr_unit(unit, ctx=None):
    """运行稳健 S_r(静态代理) → (0-1 分, 证据 dict)。

    口径(评分标准): 异常处理 35%(含 try/raise 的 def 块比率)+
    容错特征 30%(1 类=0.5/≥2 类=1.0)+输入校验 20%(校验特征块比率)+
    并发安全 15%(文件级 Lock/锁特征命中=1.0)。
    页面无 Python 运行面 / 源文件缺失 → 如实 0。APM 缺位, 全部为
    代码特征代理非运行时指标(证据中 static_proxy 字段如实标注)。
    """
    if unit["unit_type"] == "page":
        return 0.0, {"static_proxy": True, "note": "页面无 Python 运行面"}
    text = (ctx or {}).get(unit.get("file"))
    if not text:
        return 0.0, {"static_proxy": True, "note": "源文件缺失, 如实 0"}
    blocks = _def_blocks(text) or [(1, text)]
    n = len(blocks)
    exc = sum(1 for _l, b in blocks if "try:" in b or "raise " in b) / n
    fault_kinds = [k for k in _SR_FAULT if k in text]
    fault = 0.0 if not fault_kinds else (0.5 if len(fault_kinds) == 1 else 1.0)
    valid = sum(1 for _l, b in blocks
                if any(k in b for k in _SR_VALID)) / n
    conc = 1.0 if any(k in text for k in _SR_CONC) else 0.0
    s = round(0.35 * exc + 0.30 * fault + 0.20 * valid + 0.15 * conc, 2)
    ev = {"exception_rate": round(exc, 2), "fault_features": fault_kinds,
          "validation_rate": round(valid, 2), "concurrency": conc,
          "static_proxy": "APM 缺位, 代码特征代理"}
    return s, ev


def run(args=None):
    """zhuyan score CLI: S_b+S_r 双维度评分视图(S_e/罚分 T1.3-T1.4 实装)。"""
    units = fingerprint.enumerate_units()
    sb_ctx = build_sb_context()
    sr_ctx = build_sr_context(units)
    scored = []
    for u in units:
        sb, sb_ev = sb_unit(u, sb_ctx)
        sr, sr_ev = sr_unit(u, sr_ctx)
        scored.append((u, sb, sb_ev, sr, sr_ev))
    chain_hits = Counter()
    for _u, _sb, ev, _sr, _sr_ev in scored:
        for name in ev["hit_chains"]:
            chain_hits[name] += 1
    print(f"[zhuyan score] S_b 业务价值: {len(scored)} 单元已评分(权重 0.40)")
    print("  端点链命中分布: " + (
        ", ".join(f"{k}={v}" for k, v in chain_hits.most_common()) or "无命中"))
    top = sorted(scored, key=lambda x: -x[1])[:5]
    for u, s, ev, _sr, _sr_ev in top:
        print(f"  [S_b={s:.2f}] {u['unit_id']} —— 链{ev['hit_chains'] or '—'}"
              f" 调用{ev['call_count']}处 转化{ev['conversion']}")
    print("[zhuyan score] S_r 运行稳健(权重 0.35, 静态代理=APM 缺位代码特征近似):")
    top_sr = sorted(scored, key=lambda x: -x[3])[:5]
    for u, _sb, _sb_ev, sr, ev in top_sr:
        print(f"  [S_r={sr:.2f}] {u['unit_id']} —— 异常{ev['exception_rate']}"
              f" 容错{ev['fault_features'] or '—'}"
              f" 校验{ev['validation_rate']} 并发{ev['concurrency']}")
    print("[zhuyan score] S_e(T1.3)/罚分总分(T1.4)逐任务实装——"
          "当前 S_b+S_r 双维度")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
