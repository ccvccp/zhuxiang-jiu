"""zhuyan score: 成熟度评分（81号·T1.1 S_b 业务价值 维度先行）

S_b = 0.40×核心链关联 + 0.30×调用频次(静态代理) + 0.30×转化关联
口径: docs/registry/maturity/成熟度评分标准.md(归一化阈值表);
S_r(T1.2)/S_e(T1.3)/罚分总分(T1.4)逐任务扩展。
数据源复用: 链倒排=竹医 chains.parse_chains(业务链定义.md 权威);
调用面=竹医 contract.extract_frontend_calls+_url_matches(模板互认)。
"""
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
# 调用频次归一: 0=0 / 1-2 处=0.4 / 3-5 处=0.7 / ≥6 处=1.0


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


def run(args=None):
    """zhuyan score CLI: T1.1 S_b 维度(全单元评分+链命中汇总; S_r/S_e 后续任务)。"""
    units = fingerprint.enumerate_units()
    ctx = build_sb_context()
    scored = []
    for u in units:
        s, ev = sb_unit(u, ctx)
        scored.append((u, s, ev))
    chain_hits = Counter()
    for _u, _s, ev in scored:
        for name in ev["hit_chains"]:
            chain_hits[name] += 1
    print(f"[zhuyan score] S_b 业务价值: {len(scored)} 单元已评分(权重 0.40)")
    print("  端点链命中分布: " + (
        ", ".join(f"{k}={v}" for k, v in chain_hits.most_common()) or "无命中"))
    top = sorted(scored, key=lambda x: -x[1])[:5]
    for u, s, ev in top:
        print(f"  [S_b={s:.2f}] {u['unit_id']} —— 链{ev['hit_chains'] or '—'}"
              f" 调用{ev['call_count']}处 转化{ev['conversion']}")
    print("[zhuyan score] S_r(T1.2)/S_e(T1.3)/罚分总分(T1.4)逐任务实装——"
          "当前仅 S_b 维度")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
