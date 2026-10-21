"""zhuyi report: 断链六段式报告生成（79号·T1.5）

audit 四检(T1.1-T1.4) findings → docs/registry/reports/断链报告-*.md 归档。
六段: 链概览/断点明细(断链级+疑点级)/涉及文件/复现指引/关联案例/处置建议。
分级: auth_401=断链(链走不通实锤); miss/partial/auth_exposed/no_call/
dataflow_gap=疑点(需人工解读——目标形态gap/调用面在taro端/外部输入等)。
铁律: 处方只出不施——本报告仅输出建议, 不写盘不改代码。
报告结构对齐竹盾影响报告风格(编号+日期+归档说明)。
"""
import datetime
import os
import re

try:
    from zhuyi import chains as chains_mod
except ImportError:
    import chains as chains_mod

REPORTS_DIR = os.path.join(chains_mod.REPO_ROOT, "docs", "registry", "reports")

KIND_LEVEL = {
    "auth_401": "断链",
    "miss": "疑点", "partial": "疑点", "auth_exposed": "疑点",
    "no_call": "疑点", "dataflow_gap": "疑点",
}

_ADVICE = {
    "auth_401": "白名单条目与 L0-端点清单实际路由逐条对照(含参数形态)——"
                "死条目删除并按路由形态改配(如 EXACT→POST 正则档)",
    "miss": "端点不在册: 确认目标形态是否已实现(拍板规划)或链定义端点是否写错",
    "partial": "模糊端点: 到 L0-端点清单确认准确路由后改链定义为精确形态",
    "auth_exposed": "鉴权期望与实现不一致: 确认是否过度公开, 对照设计意图收紧或改链",
    "no_call": "主站前端无调用点: 确认调用端归属(taro 端/服务间), "
               "如属主站则补调用点或修链",
    "dataflow_gap": "关键 ID 凭空出现: 属外部输入则链定义补注来源, "
                    "属服务端携带则补全前序步骤出字段",
}


def _load_cases():
    """轻量加载 cases front matter(id/title/keywords/related_chain)。"""
    cases_dir = os.path.join(chains_mod.REPO_ROOT, "docs", "registry", "cases")
    out = []
    for name in sorted(os.listdir(cases_dir)):
        if not (name.startswith("案例-") and name.endswith(".md")):
            continue
        with open(os.path.join(cases_dir, name), encoding="utf-8") as fh:
            text = fh.read()
        fm = text.split("---")[1] if text.startswith("---") else ""
        cid = re.search(r"id:\s*(\S+)", fm)
        title = re.search(r"title:\s*(.+)", fm)
        chain = re.search(r"related_chain:\s*(\S+)", fm)
        kws = re.search(r"keywords:\s*\[(.+?)\]", fm)
        out.append({
            "id": cid.group(1) if cid else "",
            "title": title.group(1).strip() if title else "",
            "chain": chain.group(1) if chain else "",
            "keywords": [k.strip() for k in kws.group(1).split(",")] if kws else [],
        })
    return out


def _match_cases(cases, chain_name, tokens):
    """关联案例匹配: 链名等于 related_chain, 或关键词与 token 交集。"""
    hits = []
    for c in cases:
        if c["chain"] and c["chain"] == chain_name:
            hits.append(c)
            continue
        if tokens and set(c["keywords"]) & set(tokens):
            hits.append(c)
    return hits


def generate(chs, registry, f1, f2, call_map, no_call, gaps, out_dir=None):
    """六段式断链报告 → 归档路径。参数为 audit 四检产物。"""
    now = datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
    rep_dir = out_dir or REPORTS_DIR
    os.makedirs(rep_dir, exist_ok=True)
    path = os.path.join(rep_dir, now.strftime("断链报告-%Y%m%d-%H%M%S.md"))
    index = {(e["method"], e["path"]): e for e in registry["endpoints"]}

    # 链级汇总
    per = {c["name"]: {"steps": len(c["steps"]), "f1": 0, "f2": 0,
                       "no_call": 0, "gap": 0, "called": 0}
           for c in chs}
    for f in f1:
        per[f["chain"]]["f1"] += 1
    for f in f2:
        per[f["chain"]]["f2"] += 1
    for f in no_call:
        per[f["chain"]]["no_call"] += 1
    for g in gaps:
        per[g["chain"]]["gap"] += 1
    for c in chs:
        for s in c["steps"]:
            for m, p, _a in s["endpoints"]:
                if (m, p) in call_map:
                    per[c["name"]]["called"] += 1

    lines = []
    lines.append("# 断链报告 · 竹医 zhuyi audit（79号·L1 业务链审查）")
    lines.append("")
    lines.append(f"> 生成: {now.strftime('%Y-%m-%d %H:%M:%S')} · "
                 f"基线: registry.json({len(registry['endpoints'])} 端点) · "
                 f"四检: T1.1 端点在册 / T1.2 鉴权可达 / T1.3 前端调用点 / T1.4 数据流衔接")
    lines.append("")

    lines.append("## 一、链概览")
    lines.append("")
    lines.append("| 链 | 步骤 | 不在册 | 鉴权异常 | 无调用点 | 数据流gap | 调用点 | 判定 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for c in chs:
        d = per[c["name"]]
        broken = d["f2"] and any(f["kind"] == "auth_401" for f in f2
                                 if f["chain"] == c["name"])
        verdict = "**断链**" if broken else ("疑点" if (
            d["f1"] or d["f2"] or d["gap"] or d["no_call"]) else "健康")
        lines.append(f"| {c['name']} | {d['steps']} | {d['f1']} | {d['f2']} | "
                     f"{d['no_call']} | {d['gap']} | {d['called']} | {verdict} |")
    lines.append("")

    lines.append("## 二、断点明细")
    lines.append("")
    broken = [f for f in f1 + f2 if KIND_LEVEL.get(f["kind"]) == "断链"]
    suspects = [f for f in f1 + f2 + no_call + gaps
                if KIND_LEVEL.get(f["kind"]) == "疑点"]
    lines.append("### 断链级")
    lines.append("")
    if not broken:
        lines.append("- 无（当前审查面未发现链走不通实锤）")
    for f in broken:
        lines.append(f"- **[T1.2:{f['kind']}]** {f['chain']} 步骤{f['step']}"
                     f"({f['label']}): {f['method']} {f['path']} "
                     f"期望{f['expect']} 实际{f['actual']}——401 永拦")
    lines.append("")
    lines.append("### 疑点级")
    lines.append("")
    if not suspects:
        lines.append("- 无")
    for f in suspects:
        loc = (f"{f.get('method', '')} {f.get('path', f.get('token', ''))}").strip()
        src = {"miss": "T1.1", "partial": "T1.1", "auth_exposed": "T1.2",
               "no_call": "T1.3", "dataflow_gap": "T1.4"}[f["kind"]]
        detail = ("主站前端无调用点(可能在 taro 端/服务间)"
                  if f["kind"] == "no_call"
                  else f"入字段「{f.get('token', '')}」未溯源至前序产出"
                  if f["kind"] == "dataflow_gap" else
                  (f"期望{f.get('expect')} 实际{f.get('actual')}——过度暴露"
                   if f["kind"] == "auth_exposed" else "端点不在册/模糊形态"))
        lines.append(f"- [{src}:{f['kind']}] {f['chain']} 步骤{f['step']}"
                     f"({f['label']}): {loc}——{detail}")
    lines.append("")

    lines.append("## 三、涉及文件")
    lines.append("")
    fe_files = sorted({f for files in call_map.values() for f in files})
    lines.append("- 前端调用面: " + (", ".join(fe_files) if fe_files else "无"))
    be_files = sorted({index[(f["method"], f["path"])]["module"]
                       for f in f1 + f2 if (f["method"], f["path"]) in index})
    lines.append("- 后端路由模块: " + (", ".join(be_files) if be_files else "无"))
    lines.append("- 鉴权白名单: backend/core/auth_middleware.py(死条目归因必查)")
    lines.append("")

    lines.append("## 四、复现指引")
    lines.append("")
    lines.append("- 全检复现: `python agent-infra/zhuyi/cli.py audit`")
    lines.append("- qr 死条目回放(已知答案基准): 构造 fixture 见 "
                 "`audit.build_qr_dead_registry()` + test_t12_qr_dead_fixture_exchanges_401")
    lines.append("")

    lines.append("## 五、关联案例")
    lines.append("")
    cases = _load_cases()
    tokens = {f.get("path", f.get("token", "")) for f in f1 + f2 + gaps}
    seen_ids = set()
    matched = False
    for c in chs:
        for case in _match_cases(cases, c["name"], tokens):
            if case["id"] in seen_ids:
                continue
            seen_ids.add(case["id"])
            matched = True
            lines.append(f"- {case['id']} {case['title']}"
                         + (f"(链: {case['chain']})" if case["chain"] else ""))
    if not matched:
        lines.append("- 无直接关联案例")
    lines.append("")

    lines.append("## 六、处置建议")
    lines.append("")
    kinds = {f["kind"] for f in f1 + f2 + no_call + gaps}
    for k in sorted(kinds):
        lines.append(f"- [{k}] {_ADVICE[k]}")
    lines.append("")
    lines.append("> **只出不施**: 本报告仅输出处置建议, 不写盘不改代码——"
                 "人工确认后落地。")
    lines.append("")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path
