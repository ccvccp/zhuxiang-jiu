"""zhuyuan prescribe: 缺口处方（80号·T3.1, 只出不施铁律）

三源缺口/违规样本(盘点 audit 台账行 / 合规 comply 违规明细 / 探针 probe findings)
→ 归一化 → 竹医 zhuyi.cases 检索复用(关键词/链/文件加权评分) → 修正建议处方:
处置建议 + 案例引用(编号/得分/命中面/修正模板/预防规则) + 置信度。
铁律=只出不施: 输出修正方向与建议, 不写盘不改代码; 案例库无命中如实标注。
"""
import os

# audit 缺口类型 → 处置建议
_GAP_SUGGESTIONS = {
    "content_missing": "按《资产标准.md》补齐关键内容(资质/警示语/正文达标)",
    "seo_missing": "补 SEO 基线(title+meta description)",
    "alt_missing": "img 补 alt 属性(可访问性)",
    "image_broken": "修复断链图(核对路径或补传资源)",
}

# 案例引用最低分: 4=至少一档真实关键词命中(×3, P0 优先+1 可达 4);
# 3=纯技术面 2 分+P0 加成 1 的擦线弱命中, 不算相关(T3.5 实跑暴露后收紧)
_CASE_MIN_SCORE = 4


def _zhuyi_cases():
    """跨包复用竹医案例检索(延迟导入+agent-infra 路径注入, 同竹医 impact 模式)。"""
    import sys
    try:
        from zhuyi import cases
    except ImportError:
        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if pkg not in sys.path:
            sys.path.insert(0, pkg)
        from zhuyi import cases
    return cases


def normalize_gap(gap):
    """三源缺口归一化 → {id, desc, level, query, suggestion}。

    形态判别: gap_type=盘点台账行 / rule_id=合规违规明细 / 其余=探针 finding。
    合规 risk high→P0(功效/怂恿类阻断), med→P2(绝对化等人工裁量不阻断)。
    """
    if "gap_type" in gap:
        return {
            "id": f"{gap['asset_id']}:{gap['gap_type']}",
            "desc": gap.get("evidence") or gap["gap_type"],
            "level": gap.get("priority") or "P2",
            "query": " ".join(filter(None, (
                gap["asset_id"], gap["gap_type"], gap.get("evidence") or ""))),
            "suggestion": _GAP_SUGGESTIONS.get(
                gap["gap_type"], "按《资产标准.md》口径补齐"),
        }
    if "rule_id" in gap:
        return {
            "id": f"{gap['file']}:{gap['line']}",
            "desc": f"违规词「{gap['word']}」[{gap['rule_id']}·{gap['category']}]",
            "level": "P0" if gap.get("risk") == "high" else "P2",
            "query": f"{gap['word']} {gap['category']} 违规",
            "suggestion": gap.get("suggestion") or "参照 rules.py 词表 suggestion 改写",
        }
    return {
        "id": (gap.get("msg") or "probe")[:24],
        "desc": gap.get("msg") or "",
        "level": gap.get("level") or "P2",
        "query": gap.get("msg") or "",
        "suggestion": "按探针报告处置建议执行(P0 上线前必修/P1 排期/P2 人工裁量)",
    }


def prescribe_gaps(gaps, top=1):
    """缺口列表 → 处方列表: 归一化+竹医案例检索+建议/案例引用/置信度。

    案例引用含 build_prescription 的修正模板(源自案例四段正确写法)与预防规则;
    无命中→case_ref=None 且建议追加如实标注, 置信度低。
    弱命中过滤: 得分<_CASE_MIN_SCORE(4)不算相关——防 ".html" 资产缺口靠
    技术面泛匹配(2 分)+P0 加成(1 分)=3 分擦线拉进不相关案例(两轮实跑暴露)。
    """
    cases_mod = _zhuyi_cases()
    all_cases = cases_mod.load_cases()
    out = []
    for g in gaps:
        p = normalize_gap(g)
        results = [r for r in cases_mod.search(p["query"], cases=all_cases, top=top)
                   if r[1] >= _CASE_MIN_SCORE]
        if results:
            case, score, hits = results[0]
            full = cases_mod.build_prescription(case)
            p["case_ref"] = {
                "id": case["id"], "title": case["title"], "file": case["file"],
                "score": score, "hits": hits,
                "patch_hint": full["patch_hint"], "prevention": full["prevention"],
            }
            p["confidence"] = "中" if score >= 5 else "低"
        else:
            p["case_ref"] = None
            p["confidence"] = "低"
            p["suggestion"] += "(案例库无相关命中——按资产标准/词表口径处置)"
        out.append(p)
    return out


def _current_gaps(repo_root=None):
    """当前真实缺口: 盘点 missing 行 + 合规违规明细(两静态源, 离线)。"""
    try:
        from zhuyuan import audit as audit_mod
        from zhuyuan import comply as comply_mod
    except ImportError:
        import audit as audit_mod
        import comply as comply_mod
    assets, _summary = audit_mod.scan_assets(repo_root)
    gaps = [a for a in assets if a.get("status") == "missing"]
    gaps += comply_mod.scan_compliance(repo_root)
    return gaps


def run(args=None):
    """zhuyuan prescribe CLI: 缺口→处方(案例引用+建议)——只出不施。"""
    query = getattr(args, "query", None)
    if query:
        cases_mod = _zhuyi_cases()
        results = cases_mod.search(query)
        print(f"[zhuyuan prescribe] 检索「{query}」→ 命中 {len(results)} 条")
        for case, score, hits in results:
            print(f"  [{score}分] {case['id']} {case['title']} ({case['file']})")
            print(f"    命中面: {', '.join(hits)}")
        if results:
            full = cases_mod.build_prescription(results[0][0])
            print("  修正模板(源自案例四段正确写法):")
            for ln in (full["patch_hint"] or "—").splitlines()[:5]:
                print(f"    {ln}")
        print("[zhuyuan prescribe] 只出不施: 修正方向模板, 不写盘不改代码")
        return 0
    gaps = _current_gaps()
    prescs = prescribe_gaps(gaps)
    print(f"[zhuyuan prescribe] 缺口处方: 真实当前缺口 {len(gaps)} 条"
          f" → 处方 {len(prescs)} 条")
    for p in prescs:
        print(f"  [{p['level']}] {p['id']} —— {p['desc']}")
        print(f"    建议: {p['suggestion']}")
        if p["case_ref"]:
            c = p["case_ref"]
            print(f"    案例引用: {c['id']}「{c['title']}」"
                  f"({c['score']}分, 置信{p['confidence']}) {c['file']}")
            first = (c["patch_hint"] or "—").splitlines()[0]
            print(f"    修正模板: {first}")
        else:
            print("    案例引用: 无相关命中(如实)——按资产标准/词表口径处置")
    print("[zhuyuan prescribe] 只出不施: 输出修正建议, 不写盘不改代码"
          "——人工确认后落地")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
