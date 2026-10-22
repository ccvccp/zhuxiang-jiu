"""zhuyan loop: 闭环演示（81号·T3.2）

一条命令跑通竹演四环(全真数据, 零副作用):
  环1 评分 score  —— 2651 单元三维+总分分档+B3 回放
  环2 矩阵 matrix —— 四象限分类计数
  环3 类比 analogize —— 优先进化区目标三策略方案(案例命中)
  环4 进化 evolve —— EVO 五段草稿(人工确认门)
零副作用口径: 只调内部函数, 不归档报告/不落盘草稿/不上报度量;
ctxs/units 全链一次构建传递(各环不重复构建)。
用法: python agent-infra/zhuyan/loop.py
"""
from collections import Counter

try:
    from zhuyan import analogize as analogize_mod
    from zhuyan import evolve as evolve_mod
    from zhuyan import fingerprint
    from zhuyan import matrix as matrix_mod
    from zhuyan import score as score_mod
    from zhuyan import seed as seed_mod
except ImportError:
    import os
    import sys
    _pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pkg not in sys.path:
        sys.path.insert(0, _pkg)
    from zhuyan import analogize as analogize_mod
    from zhuyan import evolve as evolve_mod
    from zhuyan import fingerprint
    from zhuyan import matrix as matrix_mod
    from zhuyan import score as score_mod
    from zhuyan import seed as seed_mod


def run_loop():
    """竹演闭环四环(全真数据, 零副作用) → 各环摘要 dict。"""
    # 环1 评分: 全量单元三维+罚分+分档+B3
    units = fingerprint.enumerate_units()
    ctxs = (score_mod.build_sb_context(), score_mod.build_sr_context(units),
            score_mod.build_se_context(units))
    scored = [score_mod.maturity_unit(u, *ctxs) for u in units]
    grades = Counter(m["grade"] for m in scored)
    avg = round(sum(m["total"] for m in scored) / len(scored), 2) if scored else 0
    hits, misses = score_mod.b3_replay(units, ctxs)
    print(f"[zhuyan loop] 环1 评分: {len(scored)} 单元 · 均分 {avg} · "
          f"high={grades['high']}/mid={grades['mid']}/low={grades['low']} · "
          f"B3 {'PASS' if not misses else 'FAIL'}({len(hits)}/2)")
    vetoed = sum(1 for m in scored if m["pen_ev"]["vetoed"])
    if vetoed:
        print(f"  硬否决 {vetoed} 处(真实发现, 见评分报告)")

    # 环2 矩阵: 四象限
    mtx = matrix_mod.build_matrix(units, ctxs)
    quads = Counter(q for _m, q in mtx)
    print("[zhuyan loop] 环2 矩阵: " + " / ".join(
        f"{q}{quads[q]}" for q in ("优先进化区", "战略保护区",
                                   "观察等待区", "维持现状区")))

    # 环3 类比: 优先进化区目标三策略(ref_seed 预计算复用)
    evos = sorted((m for m, q in mtx if q == "优先进化区"),
                  key=lambda m: -m["total"])
    all_cases = evolve_mod._zhuyi_cases().load_cases()
    cands = seed_mod.top_candidates(units, ctxs, top_n=1)
    ref_seed = cands[0][0]["unit"]["unit_id"] if cands else "无候选(如实)"
    src_ctx = ctxs[1]
    plans = [analogize_mod.analogize_unit(
        m["unit"]["unit_id"], ctxs, units, src_ctx, all_cases,
        ref_seed=ref_seed) for m in evos]
    hit_n = sum(1 for p in plans if p["case_ref"])
    print(f"[zhuyan loop] 环3 类比: 优先进化区 {len(plans)} 目标 → "
          f"三策略方案(案例命中 {hit_n}/{len(plans)}, 参照 {ref_seed})")
    for p in plans[:3]:
        c = p["case_ref"]
        print(f"  {p['unit']['unit_id']} —— "
              + (f"案例 {c['id']} {c['score']}分" if c else "无命中(如实)"))

    # 环4 进化: EVO 草稿(零副作用=只生成不落盘)
    groups = {}
    for p in plans:
        key = evolve_mod._pain_key(p["pains"][0] if p["pains"] else "")
        groups.setdefault(key, []).append(p)
    drafts = [evolve_mod.draft_evo_case(g[0], count=len(g))
              for g in groups.values()]
    ok_sections = sum(1 for d in drafts if d["sections"] == 5)
    print(f"[zhuyan loop] 环4 进化: 痛点主类 {len(drafts)} 组 → EVO 草稿 "
          f"{len(drafts)} 份(五段 {ok_sections}/{len(drafts)} · "
          "人工确认门: 草稿-EVO- 前缀不入库)")
    print("[zhuyan loop] 闭环完成: 评分→矩阵→类比→进化案例草稿——"
          "入库走人工确认; 全链只出不施")
    return {"units": len(units), "avg": avg, "grades": dict(grades),
            "b3_pass": not misses, "quads": dict(quads),
            "plans": len(plans), "case_hits": hit_n,
            "drafts": len(drafts), "sections_ok": ok_sections}


if __name__ == "__main__":
    raise SystemExit(run_loop() and 0)
