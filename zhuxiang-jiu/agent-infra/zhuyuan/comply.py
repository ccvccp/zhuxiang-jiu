"""zhuyuan comply: 合规扫描器（80号·T1.2）

主站 html+js 逐行 × rules 词表 → 违规明细(文件/行/词/类别/risk/建议)。
口径:
- 沿用 audit 豁免清单(内部工具页) + js 侧豁免(modules/test/ai-* 工具 js);
- **B1 基准**: 微信外链整改实录(整改前文案)回放——13 个实录核心词
  检出率必须 100%;
- 当前态(整改已上线): 功效类残留 0; 已知绝对化边缘案例 2 处(med 供人工审)。
"""
import os

try:
    from zhuyuan import audit as audit_mod
    from zhuyuan import rules as rules_mod
except ImportError:
    import audit as audit_mod
    import rules as rules_mod

_JS_EXEMPT = ("modules", "test", "ai-", "voice-", "challenge-", "entry-login")

# 归档目录注入点(测试隔离用; None=默认 REPO_ROOT/docs/registry/reports)
REPORTS_DIR = None

# 微信整改实录核心违规词(B1 已知答案)
B1_WORDS = ["醒酒", "无酒气", "不头疼", "畅饮", "尽兴更尽兴", "富硒",
            "天然健康", "有益微量元素", "有益成分", "健康密码",
            "就喝竹奕酒", "清心怡神", "懂你的身体"]


def scan_compliance(repo_root=None):
    """主站扫描 → 违规明细 [{file, line, word, rule_id, category, risk, suggestion}]。"""
    repo_root = repo_root or audit_mod._REPO_ROOT
    violations = []
    targets = []
    for name in sorted(os.listdir(repo_root)):
        if name.endswith(".html") and not audit_mod._is_exempt(name):
            targets.append(name)
    js_dir = os.path.join(repo_root, "js")
    if os.path.isdir(js_dir):
        for name in sorted(os.listdir(js_dir)):
            if (name.endswith(".js")
                    and not any(x in name for x in _JS_EXEMPT)):
                targets.append(f"js/{name}")
    for rel in targets:
        path = os.path.join(repo_root, rel)
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh.read().splitlines(), 1):
                for hit in rules_mod.match(line):
                    violations.append({
                        "file": rel, "line": lineno, "word": hit["word"],
                        "rule_id": hit["rule_id"], "category": hit["category"],
                        "risk": hit["risk"],
                        "suggestion": "参照案例四段正确写法替换口感/工艺描述"
                                      "(竹香清雅/入口绵柔/回味悠长)",
                    })
    return violations


def b1_replay():
    """B1 基准: 整改实录回放 → 检出率。返回 (hit_words, miss_words)。"""
    before = (
        "滴滴醇香，醒酒快、无酒气、不头疼。汲取徂徕山富硒泉水，"
        "天然健康基底，富含竹叶黄酮等有益微量元素。福来就喝竹奕酒！"
        "老友小聚：畅饮不头疼，尽兴更尽兴。【健康密码】"
        "饮后体验：醒酒快、身上无酒气、不头疼，清心怡神，懂你的身体。"
        "酒体富含竹叶黄酮(AOB)、丁香醇等有益成分。"
    )
    words = {h["word"] for h in rules_mod.match(before)}
    hit = [w for w in B1_WORDS if any(w in g or g in w for g in words)]
    miss = [w for w in B1_WORDS if w not in hit]
    return hit, miss


def generate_report(violations=None, out_dir=None):
    """T1.3 合规报告(四节) → 归档路径。"""
    import datetime
    try:
        from zhuyuan import audit as _a
    except ImportError:
        import audit as _a
    if violations is None:
        violations = scan_compliance()
    out_dir = out_dir or REPORTS_DIR or os.path.join(
        _a._REPO_ROOT, "docs", "registry", "reports")
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
    path = os.path.join(out_dir, now.strftime("合规报告-%Y%m%d-%H%M%S.md"))
    by_cat = {}
    for v in violations:
        by_cat[v["category"]] = by_cat.get(v["category"], 0) + 1
    high_n = sum(1 for v in violations if v["risk"] == "high")
    lines = ["# 合规报告 · 竹园 zhuyuan comply（80号·合规审查）", ""]
    lines.append(f"> 生成: {now.strftime('%Y-%m-%d %H:%M:%S')} · "
                 f"扫描面: 主站 html+js(豁免内部工具页) · "
                 f"违规 {len(violations)} 处(high {high_n}/med {len(violations) - high_n})")
    lines += ["", "## 一、违规清单", ""]
    if not violations:
        lines.append("- 无(当前态合规通过)")
    for v in violations:
        lines.append(f"- **{v['file']}:{v['line']}** 「{v['word']}」"
                     f"[{v['risk']}:{v['rule_id']}·{v['category']}]")
    lines += ["", "## 二、分类统计", ""]
    if by_cat:
        for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {cat}: {n} 处")
    else:
        lines.append("- 全类别 0")
    lines += ["", "## 三、处置建议", ""]
    for v in violations:
        lines.append(f"- {v['file']}:{v['line']} {v['suggestion']}"
                     + ("（边缘案例: 新闻纪实/引语语境, 建议人工裁量改写）"
                        if v["risk"] == "med" else ""))
    if not violations:
        lines.append("- 无待处置项; 新内容接入时以 rules.py 词表为准入校验")
    lines += ["", "## 四、案例关联", "",
              ("- 案例库暂无合规类案例——微信外链整改实录为首批入库候选"
               "(任务 T3.5, 五段式: 现象=申诉被拒/根因=功效宣称+怂恿/错误=17处文案/"
               "正确=口感工艺化改写/预防=rules 词表上线拦截)。"),
              "> **只出不施**: 本报告仅输出处置建议, 不写盘不改代码。", ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def run(args=None):
    """zhuyuan comply CLI: 违规明细+B1 自检+T1.3 报告归档+度量上报。"""
    try:
        from zhuyuan import stats as stats_mod
    except ImportError:
        import stats as stats_mod
    violations = scan_compliance()
    print(f"[zhuyuan comply] 主站合规扫描: 违规 {len(violations)} 处")
    for v in violations:
        print(f"  [{v['risk']}:{v['rule_id']}] {v['file']}:{v['line']}"
              f" 「{v['word']}」({v['category']}) —— {v['suggestion']}")
    hit, miss = b1_replay()
    rate = int(len(hit) / len(B1_WORDS) * 100)
    print(f"[zhuyuan comply] B1 整改实录回放: 检出率 {rate}%"
          f"（{len(hit)}/{len(B1_WORDS)}）"
          + (f" 未中: {', '.join(miss)}" if miss else "——B1 PASS"))
    path = generate_report(violations)
    print(f"[zhuyuan comply] T1.3 合规报告已归档: {path}")
    ok = stats_mod.report_stats(metrics={"violations": len(violations)})
    print(f"[zhuyuan comply] 度量上报 zhuyuan:stats {'成功' if ok else '降级(仅打印)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
