"""zhuyan analogize: 类比推荐（81号·T2.4, 只出不施铁律）

优先进化区目标(matrix) × 反模式前置过滤(antipattern) × 竹医案例检索
(zhuyi.cases 加权) × 种子参照(seed) → 三策略进化方案:
- 策略A 保守补强: 最小改动修复痛点(补测试/清反模式/补 SEO)
- 策略B 参照重构: 以 mid 层最佳候选为模板的结构性改造(人工主导)
- 策略C 模式移植: 案例库修正模板移植(命中时)
三策略附适用条件/预期收益/风险; **只出不施**——方案落报告不改代码。
"""
import os

try:
    from zhuyan import antipattern as ap_mod
    from zhuyan import fingerprint
    from zhuyan import matrix as matrix_mod
    from zhuyan import score as score_mod
    from zhuyan import seed as seed_mod
except ImportError:
    import sys
    _pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pkg not in sys.path:
        sys.path.insert(0, _pkg)
    from zhuyan import antipattern as ap_mod
    from zhuyan import fingerprint
    from zhuyan import matrix as matrix_mod
    from zhuyan import score as score_mod
    from zhuyan import seed as seed_mod

_CASE_MIN_SCORE = 4  # 同竹园 prescribe 弱命中阈值(T3.5 收紧口径)
# 归档目录注入点(测试隔离)
REPORTS_DIR = None


def _zhuyi_cases():
    import sys
    try:
        from zhuyi import cases
    except ImportError:
        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if pkg not in sys.path:
            sys.path.insert(0, pkg)
        from zhuyi import cases
    return cases


def pain_points(m):
    """成熟度证据 → 痛点清单(低分子指标如实)。"""
    pains = []
    if m["penalty"] > 0 or m["pen_ev"]["vetoed"]:
        pains.append(f"罚分 {m['penalty']}({'硬否决' if m['pen_ev']['vetoed'] else '重扣'})")
    if not (m["se_ev"].get("tests") or []):
        pains.append("无关联测试")
    elif m["se_ev"].get("test_value", 0) < 0.7:
        pains.append(f"测试保障弱({m['se_ev']['test_value']})")
    if m["se_ev"].get("contract", 0) < 0.5:
        pains.append(f"契约不清(docstring {m['se_ev'].get('contract')})")
    if len(m["sr_ev"].get("fault_features") or []) < 2:
        pains.append("容错特征不足(<2 类)")
    if m["se_ev"].get("imports", 0) > 3:
        pains.append(f"耦合偏高(import {m['se_ev']['imports']})")
    return pains or ["无明显低分维度"]


def _strategies(m, ap_hits, case_ref, ref_seed):
    """三策略方案(只出不施; 各附条件/收益/风险)。"""
    ap_note = ("先清反模式: " + "; ".join(f"{r}({a.split('(')[0]})"
                for _u, r, _l, a in ap_hits)) if ap_hits else "无已知反模式命中"
    plan = [
        ("策略A 保守补强",
         f"最小改动修复痛点: {'; '.join(pain_points(m))}",
         ("条件: 痛点可局部修复; 收益: 总分升 0.1-0.2 不动结构; "
          f"风险: 低。{ap_note}")),
        ("策略B 参照重构",
         f"以 mid 层最佳候选为模板的结构性改造(参照: {ref_seed})",
         ("条件: 痛点属结构耦合/巨石文件; 收益: 三维同升; "
          "风险: 高——须人工主导+竹盾五步 SOP")),
        ("策略C 模式移植",
         (f"案例修正模板移植: {case_ref['id']}「{case_ref['title']}」"
          f"({case_ref['score']}分)" if case_ref else "案例库无相关命中——如实"),
         ("条件: 案例命中(≥4 分); 收益: 带预防规则少踩坑; 风险: 低"
          if case_ref else "条件不满足(无命中), 此策略不适用")),
    ]
    return plan


def analogize_unit(unit_id, ctxs=None, units=None, src_ctx=None, all_cases=None):
    """单目标深度类比 → 方案 dict(痛点/反模式/案例/三策略/种子参照)。"""
    units = units if units is not None else fingerprint.enumerate_units()
    src_ctx = src_ctx if src_ctx is not None else {}
    unit = next(u for u in units if u["unit_id"] == unit_id)
    if ctxs is None:
        ctxs = (score_mod.build_sb_context(), score_mod.build_sr_context(units),
                score_mod.build_se_context(units))
    m = score_mod.maturity_unit(unit, *ctxs)
    ap_hits = ap_mod.scan_text(unit_id, src_ctx.get(unit.get("file")) or "") \
        if unit["unit_type"] != "page" else []
    ap_hits = [(unit_id, r, l, a) for r, l, a in ap_hits]
    query = unit_id + " " + " ".join(pain_points(m))
    if all_cases is None:
        all_cases = _zhuyi_cases().load_cases()
    results = [r for r in _zhuyi_cases().search(
        query, cases=all_cases, top=1) if r[1] >= _CASE_MIN_SCORE]
    case_ref = ({"id": results[0][0]["id"], "title": results[0][0]["title"],
                 "score": results[0][1], "hits": results[0][2]}
                if results else None)
    cands = seed_mod.top_candidates(units, ctxs, top_n=1)
    ref_seed = cands[0][0]["unit"]["unit_id"] if cands else "无候选(如实)"
    return {"unit": unit, "m": m, "pains": pain_points(m), "ap_hits": ap_hits,
            "case_ref": case_ref, "ref_seed": ref_seed,
            "strategies": _strategies(m, ap_hits, case_ref, ref_seed)}


def generate_report(plans, out_dir=None):
    """T2.4 类比推荐报告(五节) → 归档路径。"""
    import datetime
    out_dir = out_dir or REPORTS_DIR or os.path.join(
        fingerprint.REPO_ROOT, "docs", "registry", "reports")
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
    path = os.path.join(out_dir, now.strftime("类比推荐报告-%Y%m%d-%H%M%S.md"))
    lines = ["# 类比推荐报告 · 竹演 zhuyan analogize（81号·T2.4）", ""]
    lines.append(f"> 生成: {now.strftime('%Y-%m-%d %H:%M:%S')} · "
                 f"方案 {len(plans)} 份 · 口径: 优先进化区目标"
                 "×反模式过滤×竹医案例×种子参照, **只出不施**")
    lines += ["", "## 一、目标与痛点", ""]
    for p in plans:
        lines.append(f"- {p['unit']['unit_id']}(总分 {p['m']['total']},"
                     f"{p['m']['grade']}): {', '.join(p['pains'])}")
    lines += ["", "## 二、反模式前置过滤", ""]
    for p in plans:
        if p["ap_hits"]:
            for _u, r, ln, a in p["ap_hits"]:
                lines.append(f"- {p['unit']['unit_id']} —— {r}(行 {ln}): {a}")
        else:
            lines.append(f"- {p['unit']['unit_id']} —— 无反模式命中(快筛口径)")
    lines += ["", "## 三、案例检索命中", ""]
    for p in plans:
        c = p["case_ref"]
        lines.append(f"- {p['unit']['unit_id']} —— "
                     + (f"{c['id']}「{c['title']}」{c['score']}分"
                        f"(命中: {', '.join(c['hits'])})" if c else "无相关命中(如实)"))
    lines += ["", "## 四、三策略方案", ""]
    for p in plans:
        lines.append(f"- {p['unit']['unit_id']}:")
        for name, action, note in p["strategies"]:
            lines.append(f"  - {name}: {action}")
            lines.append(f"    {note}")
    lines += ["", "## 五、口径声明", "",
              ("- 种子参照取 mid 层最佳候选(T2.2 PASS=0 如实, 最佳可得参照);"),
              ("- **只出不施**: 方案为建议输出, 不写盘不改代码; "
               "策略B 须人工主导并经竹盾五步 SOP。"), ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def run(args=None):
    """zhuyan analogize CLI: --target 单目标深度 / 无参=优先进化区全量概要。"""
    target = getattr(args, "target", None)
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
    ctxs = (score_mod.build_sb_context(), score_mod.build_sr_context(units),
            score_mod.build_se_context(units))
    if target:
        plan = analogize_unit(target, ctxs, units, src_ctx)
        plans = [plan]
    else:
        mtx = matrix_mod.build_matrix(units, ctxs)
        evos = [m for m, q in mtx if q == "优先进化区"]
        evos.sort(key=lambda m: -m["total"])
        plans = [analogize_unit(m["unit"]["unit_id"], ctxs, units, src_ctx)
                 for m in evos]
    print(f"[zhuyan analogize] 类比推荐: {len(plans)} 份方案"
          + (f"(目标 {target})" if target else "(优先进化区全量)"))
    for p in plans:
        print(f"  {p['unit']['unit_id']} —— 痛点: {'; '.join(p['pains'][:3])}")
        c = p["case_ref"]
        print("    案例: " + (f"{c['id']} {c['score']}分" if c else "无命中(如实)")
              + f" | 参照: {p['ref_seed']}")
        for name, _a, note in p["strategies"]:
            print(f"    {name}")
    path = generate_report(plans)
    print(f"[zhuyan analogize] T2.4 类比推荐报告已归档: {path}")
    print("[zhuyan analogize] **只出不施**: 三策略为建议方案, "
          "人工确认后落地; 策略B 须竹盾五步 SOP")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
