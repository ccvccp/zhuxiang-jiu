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
    lines += [
        "",
        "> **只出不施**: 以上为修正方向模板与回归建议, 不写盘不改代码——人工确认后落地。",
    ]
    return "\n".join(lines)


def run(args=None):
    """consult CLI 入口: T3.1 检索 + T3.2 处方骨架, T3.3-T3.5 逐任务补齐。"""
    query = getattr(args, "query", None) or "扫码登录 401 白名单"
    results = search(query)
    print(f"[zhuyi consult] T3.1 案例检索: 「{query}」→ 命中 {len(results)} 条")
    for case, score, hits in results:
        print(f"  [{score}分] {case['id']} {case['file']}")
    if results:
        top_case = results[0][0]
        p = build_prescription(top_case)
        print("[zhuyi consult] T3.2 处方骨架(首中案例 → 只出不施):")
        for line in render_prescription(p).splitlines()[:8]:
            print(f"  {line}")
        print("  …(骨架渲染见 T3.5 端到端报告)")
    print("[zhuyi consult] T3.3 Blast Radius / "
          "T3.4 分级+置信度 / T3.5 端到端处方: 后续任务实现")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
