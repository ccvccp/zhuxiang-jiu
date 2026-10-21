"""zhuyi cases: 故障复盘库检索器（79号·T3.1, consult 的第一件）

cases/*.md front matter 解析 + 关键词/元数据加权检索, 供 consult 处方(T3.2+)
与 audit 断链报告关联案例消费。检索口径(方案: 关键词+元数据过滤, 关联文件/
模块加权): 关键词命中 ×3, 链名 ×4, 关联文件 ×5, 模块/技术面 ×2, P0 优先 +1。
"""
import os
import re

try:
    from zhuyi import chains as chains_mod
except ImportError:
    import chains as chains_mod

CASES_DIR = os.path.join(chains_mod.REPO_ROOT, "docs", "registry", "cases")


def _parse_case(text):
    fm = text.split("---")[1] if text.startswith("---") else ""

    def _grab(key, cast=str):
        if cast is list:
            # 多行 YAML list: key: 后跟缩进 "- x" 行
            mm = re.search(rf"{key}:\s*\n((?:[ \t]+-[^\n]+\n?)+)", fm)
            if mm:
                items = []
                for ln in mm.group(1).splitlines():
                    v = ln.strip().lstrip("-").strip().strip("'\"")
                    if v:
                        items.append(v)
                return items
        m = re.search(rf"{key}:\s*(.+)", fm)
        if not m:
            return [] if cast is list else ""
        raw = m.group(1).strip()
        if cast is list:
            if raw.startswith("[") and raw.endswith("]"):
                return [x.strip().strip("'\"") for x in raw[1:-1].split(",") if x.strip()]
            return [raw.strip("'\"")]
        return raw.strip("'\"")

    return {
        "id": _grab("id"), "title": _grab("title"), "date": _grab("date"),
        "severity": _grab("severity"), "module": _grab("module"),
        "keywords": _grab("keywords", list), "tech_stack": _grab("tech_stack", list),
        "related_files": _grab("related_files", list),
        "related_chain": _grab("related_chain"),
        "related_commit": _grab("related_commit"),
    }


def load_cases(cases_dir=None):
    """加载全案例(front matter 全字段 + 五段自检计数)。"""
    cases_dir = cases_dir or CASES_DIR
    out = []
    for name in sorted(os.listdir(cases_dir)):
        if not (name.startswith("案例-") and name.endswith(".md")):
            continue
        with open(os.path.join(cases_dir, name), encoding="utf-8") as fh:
            text = fh.read()
        case = _parse_case(text)
        case["file"] = name
        case["sections"] = sum(1 for s in ("一、现象", "二、根因", "三、错误代码",
                                           "四、正确写法", "五、预防规则") if s in text)
        out.append(case)
    return out


def _tok_hit(token, value):
    return any(token in v or v in token for v in value) if value else False


def score_case(case, tokens):
    """加权评分: 关键词×3 / 链名×4 / 关联文件×5 / 模块与技术面×2, P0 优先+1。"""
    score, hits = 0, []
    for t in tokens:
        if _tok_hit(t, case["keywords"]):
            score += 3
            hits.append(f"关键词:{t}")
        if case["related_chain"] and (t in case["related_chain"]):
            score += 4
            hits.append(f"链:{t}")
        if _tok_hit(t, case["related_files"]):
            score += 5
            hits.append(f"文件:{t}")
        if _tok_hit(t, case["module"] and [case["module"]] or []) or _tok_hit(t, case["tech_stack"]):
            score += 2
            hits.append(f"模块/技术面:{t}")
    if score and case["severity"] == "P0":
        score += 1
    return score, hits


def search(query, cases=None, top=3):
    """检索: query 空白分词 → 加权评分排序。返回 [(case, score, hits)]。"""
    cases = cases if cases is not None else load_cases()
    tokens = [t for t in re.split(r"\s+", query.strip()) if t]
    scored = []
    for c in cases:
        s, hits = score_case(c, tokens)
        if s > 0:
            scored.append((c, s, hits))
    scored.sort(key=lambda x: -x[1])
    return scored[:top]


_SEC_RE = re.compile(r"## [一二三四五]、([^\n]+)\n(.*?)(?=## [一二三四五六]、|\Z)", re.DOTALL)
_SEC_ORDER = ["现象", "根因", "错误代码/配置", "正确写法", "预防规则"]


def _extract_sections(text):
    """五段原文提取: {段名: 正文}。"""
    out = {}
    for m in _SEC_RE.finditer(text):
        name = m.group(1).split("：", 1)[-1].strip()
        out[name] = m.group(2).strip()
    return out


def build_prescription(case, cases_dir=None):
    """T3.2 案例 → 处方骨架(只出不施: 修正模板+回归建议, 不生成可执行 Patch)。

    结构: id/title/severity/commit/symptom/root_cause/patch_hint(错误→正确映射)
    /regression_hint(回归建议=正确写法+挂载点)/prevention(预防规则)。
    """
    cases_dir = cases_dir or CASES_DIR
    with open(os.path.join(cases_dir, case["file"]), encoding="utf-8") as fh:
        text = fh.read()
    sec = _extract_sections(text)
    patch_hint = ""
    if "错误代码/配置" in sec:
        patch_hint = "修正方向(源自案例四段正确写法):\n" + sec.get("正确写法", "")
    regression_hint = sec.get("正确写法", "")[:200]
    refs = re.findall(r"[A-Z]{1,2}\d+(?:\.\d+)?|NEW-[A-Z]?\d+(?:\.\d+)?",
                      sec.get("预防规则", ""))
    if refs:
        regression_hint += f"\n回归建议挂载点: {', '.join(sorted(set(refs)))}"
    return {
        "id": case["id"], "title": case["title"], "severity": case["severity"],
        "commit": case["related_commit"], "date": case["date"],
        "symptom": sec.get("现象", ""), "root_cause": sec.get("根因", ""),
        "patch_hint": patch_hint, "regression_hint": regression_hint,
        "prevention": sec.get("预防规则", ""),
        "case_file": case["file"],
        "related_files": list(case.get("related_files") or []),
    }


_BR_EXCLUDE = ("/.deps/", "/node_modules/", "/.git/", "agent-infra/",
               "/taro-app/", "/__pycache__/", "/.docxgen/", "test_")


def _impact_mod():
    """跨包复用竹盾反向引用(延迟导入+agent-infra 路径注入)。"""
    import sys
    try:
        from zhudun import impact
        return impact
    except ImportError:
        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if pkg not in sys.path:
            sys.path.insert(0, pkg)
        from zhudun import impact
        return impact


def attach_blast_radius(p, repo=None):
    """T3.3: 处方自动附影响面(Blast Radius)——related_files × zhudun.impact
    反向引用, 过滤 .deps/工具目录等噪源聚焦生产面。写回 p['blast_radius']。"""
    changed = [f for f in p.get("related_files") or [] if f]
    if not changed:
        p["blast_radius"] = {"changed": [], "refs": {}}
        return p
    impact = _impact_mod()
    repo = repo or chains_mod.REPO_ROOT
    _syms, refs = impact.referencing_files(changed, repo=repo, registry={})
    refs = {f: lines for f, lines in refs.items()
            if not any(x in f for x in _BR_EXCLUDE)}
    p["blast_radius"] = {"changed": changed, "refs": refs}
    return p


def grade_prescription(p):
    """T3.4: P0(阻断)/P1(隐患)/P2(优化) 分级 + 置信度 + 证据引用。

    铁律 2(证据驱动): 证据=案例编号/修复参照(commit)/反向引用(BR), 三证齐→高,
    二证→中, 少于二证→低(无出处标低置信)。写回 grade/confidence/evidence。
    """
    grade = {"P0": "P0(阻断)", "P1": "P1(隐患)", "P2": "P2(优化)"}.get(
        p.get("severity", ""), "P2(优化)")
    evidence = []
    if p.get("id"):
        evidence.append(f"案例编号:{p['id']}")
    if p.get("commit"):
        evidence.append(f"修复参照:{p['commit']}")
    br = p.get("blast_radius")
    if br and br.get("refs"):
        first = min(br["refs"])
        evidence.append(f"反向引用:{first}:{br['refs'][first][0]}(行)")
    confidence = "高" if len(evidence) >= 3 else ("中" if len(evidence) == 2 else "低")
    p["grade"], p["confidence"], p["evidence"] = grade, confidence, evidence
    return p


def _regression_map(repo=None):
    """回归资产映射: 挂载点 → 基线测试断言编号(函数名)。

    扫描 backend/test_feature_baseline.py(B1/B2/B3 组) 与
    backend/test_qr_public_baseline.py(qr 组) 的测试函数名。
    """
    repo = repo or chains_mod.REPO_ROOT
    groups = {"b1": [], "b2": [], "b3": [], "qr": []}
    specs = [
        (os.path.join(repo, "backend", "test_feature_baseline.py"), ("b1", "b2", "b3")),
        (os.path.join(repo, "backend", "test_qr_public_baseline.py"), ("qr",)),
    ]
    for path, keys in specs:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for m in re.finditer(r"def (test_([a-z0-9]+)_\w+)", fh.read()):
                for k in keys:
                    if m.group(2).startswith(k):
                        groups[k].append(m.group(1))
    return groups


def map_regression(p, repo=None):
    """T3.5: 处方挂载点 → 基线断言编号映射, 写回 p['regression_tests']。"""
    groups = _regression_map(repo)
    text = p.get("regression_hint", "") + " " + p.get("prevention", "")
    mapped = []
    if any(x in text for x in ("T1.2", "T2.3", "qr", "白名单", "死条目")) and groups["qr"]:
        mapped.append(("qr 白名单基线(test_qr_public_baseline)", groups["qr"]))
    if "B1" in text and groups["b1"]:
        mapped.append(("B1 运费基线(test_feature_baseline)", groups["b1"]))
    if "B2" in text or "评价" in text and groups["b2"]:
        mapped.append(("B2 评价双写基线", groups["b2"]))
    if "B3" in text and groups["b3"]:
        mapped.append(("B3 注册面基线", groups["b3"]))
    p["regression_tests"] = mapped
    return p


def generate_full_prescription(query, out_dir=None, repo=None):
    """T3.5 端到端: 检索 → 处方 → BR → 分级 → 回归映射 → 归档处方报告。"""
    import datetime
    results = search(query, cases=load_cases(repo and os.path.join(
        repo, "docs", "registry", "cases") or None), top=1)
    if not results:
        return None, []
    p = build_prescription(results[0][0])
    attach_blast_radius(p, repo)
    grade_prescription(p)
    map_regression(p, repo)
    out_dir = out_dir or os.path.join(chains_mod.REPO_ROOT, "docs",
                                      "registry", "reports")
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
    path = os.path.join(out_dir, now.strftime("处方报告-%Y%m%d-%H%M%S.md"))
    lines = [
        "# 处方报告 · 竹医 zhuyi consult（79号·L3 修正处方, 端到端）", "",
        (f"> 生成: {now.strftime('%Y-%m-%d %H:%M:%S')} · 检索词: 「{query}」 · "
         f"命中: {results[0][0]['id']}(得分 {results[0][1]})"), "",
        render_prescription(p), "",
        "## 回归测试建议(映射到基线断言编号)", "",
    ]
    if p["regression_tests"]:
        for label, fns in p["regression_tests"]:
            lines.append(f"- {label}: " + ", ".join(fns))
    else:
        lines.append("- 无直接基线映射——按处方挂载点人工补充回归断言")
    lines += [
        "",
        ("> **只出不施(铁律 1)**: 本报告为修正方向模板, 不写盘不改代码——"
         "人工确认后经竹盾五步 SOP 落地。"),
        "",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path, p


def render_prescription(p):
    """处方骨架 → Markdown 文本(T3.5 端到端报告的组件)。"""
    lines = [
        f"## 处方 · {p['id']}（{p['severity']}）——{p['title']}",
        "",
        f"- 案例: {p['case_file']} · 事故日: {p['date']} · 修复参照: {p['commit'] or '—'}",
        f"- 症状: {p['symptom'][:120]}…",
        f"- 根因: {p['root_cause'][:120]}…",
        "- 修正模板:\n```",
        (p["patch_hint"] or "—")[:400],
        "```",
        f"- 回归建议: {p['regression_hint'][:200]}",
        f"- 预防规则: {p['prevention'][:160]}",
    ]
    br = p.get("blast_radius")
    if br is not None:
        preview = "; ".join(f"{f}:{br['refs'][f][0]}" for f in sorted(br["refs"])[:3])
        lines.append(f"- 影响面(Blast Radius): 改动 {len(br['changed'])} 文件, "
                     f"反向引用 {len(br['refs'])} 处——{preview or '无'}")
    if p.get("grade"):
        lines.append(f"- 分级: {p['grade']} · 置信度: {p['confidence']} · "
                     f"证据: {'; '.join(p['evidence']) or '无(低置信)'}")
    lines += [
        "",
        "> **只出不施**: 以上为修正方向模板与回归建议, 不写盘不改代码——人工确认后落地。",
    ]
    return "\n".join(lines)


def run(args=None):
    """consult CLI 入口: T3.1-T3.5 全链(检索→处方→BR→分级→回归映射→报告归档)。"""
    query = getattr(args, "query", None) or "扫码登录 401 白名单"
    results = search(query)
    print(f"[zhuyi consult] T3.1 案例检索: 「{query}」→ 命中 {len(results)} 条")
    for case, score, hits in results:
        print(f"  [{score}分] {case['id']} {case['file']}")
    if results:
        top_case = results[0][0]
        p = grade_prescription(attach_blast_radius(
            map_regression(build_prescription(top_case))))
        print(f"[zhuyi consult] T3.2-T3.4 处方: {p['grade']} · 置信{p['confidence']}"
              f" · BR 反向引用 {len(p['blast_radius']['refs'])} 处"
              f" · 回归映射 {len(p['regression_tests'])} 组")
    path, _ = generate_full_prescription(query)
    if path:
        print(f"[zhuyi consult] T3.5 端到端处方报告已归档: {path}")
    else:
        print("[zhuyi consult] T3.5 无命中案例, 未生成报告")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
