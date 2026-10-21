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


def run(args=None):
    """consult CLI 入口: T3.1 案例检索, T3.2-T3.5 逐任务补齐。"""
    query = getattr(args, "query", None) or "扫码登录 401 白名单"
    results = search(query)
    print(f"[zhuyi consult] T3.1 案例检索: 「{query}」→ 命中 {len(results)} 条")
    for case, score, hits in results:
        print(f"  [{score}分] {case['id']} {case['file']}")
        print(f"          {case['title']}")
        print(f"          命中: {', '.join(sorted(set(hits))) or '无'}")
    if not results:
        print("  无命中案例")
    print("[zhuyi consult] T3.2 处方模板 / T3.3 Blast Radius / "
          "T3.4 分级+置信度 / T3.5 端到端处方: 后续任务实现")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
