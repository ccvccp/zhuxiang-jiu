"""zhuyuan evolve: 自进化案例草稿（80号·T3.2, 入库走人工确认）

审查发现(盘点/合规/探针) → 五段式案例草稿(对齐竹医案例库规范):
front matter(id 待人工赋号/severity/keywords/related_files) +
一、现象 / 二、根因(待核实假设) / 三、错误代码/配置 / 四、正确写法(只出不施) /
五、预防规则。
入库=人工确认: 草稿落 docs/registry/cases/草稿-*.md; zhuyi.cases.load_cases
只认 案例- 前缀 → 草稿不被检索命中——人工审阅后改名+赋 id 才完成入库。
"""
import datetime
import os

try:
    from zhuyuan import audit as audit_mod
    from zhuyuan import comply as comply_mod
    from zhuyuan import prescribe as prescribe_mod
except ImportError:
    import audit as audit_mod
    import comply as comply_mod
    import prescribe as prescribe_mod

# 草稿目录注入点(测试隔离; None=默认 REPO_ROOT/docs/registry/cases)
DRAFTS_DIR = None

# 缺口类别 → 草稿文件名 slug
_SLUG = {
    "seo_missing": "SEO描述缺失", "content_missing": "关键内容缺失",
    "alt_missing": "图片ALT缺失", "image_broken": "断链图",
    "绝对化用语": "绝对化文案", "功效宣称": "功效宣称文案",
    "无依据营养声称": "营养声称文案", "怂恿饮酒": "怂恿饮酒文案",
}


def _slug_for(finding):
    if "rule_id" in finding:
        return _SLUG.get(finding["category"], finding["category"])
    if "gap_type" in finding:
        return _SLUG.get(finding["gap_type"], finding["gap_type"])
    return "探针发现"


def _root_cause(finding):
    if "rule_id" in finding:
        return ("文案在合规词表(rules.py)上线前撰写, 整改时只清功效类漏了边缘表述"
                "——未经词表准入校验(根因待人工核实)")
    if "gap_type" in finding:
        hint = {
            "seo_missing": "页面模板创建时未带 meta description 段",
            "content_missing": "页面内容裁剪时缺关键模块",
            "alt_missing": "img 标签书写时遗漏 alt 属性",
            "image_broken": "图片路径改动后未同步引用",
        }.get(finding["gap_type"], "资产创建时缺项")
        return f"{hint}——盘点基线(audit T0.3)上线后集中暴露(根因待人工核实)"
    return "页面真实 UI 未包含该要素——设计取舍或实现遗漏(根因待人工核实)"


def _prevention(finding):
    if "rule_id" in finding:
        return ("以 rules.py 词表为内容准入校验; comply 扫描器上线后此类文案"
                "上线即拦(B1 整改实录回放检出 100%); med 级边缘案例人工裁量改写")
    if "gap_type" in finding:
        return ("新页面/新资产接入时对照《资产标准.md》七项口径自检; "
                "audit 盘点台账按 P0-P2 把关; prescribe 按缺口类型出处方(只出不施)")
    return ("上线前跑 probe 三探针(基座/转化/信任); 探针报告 P0 上线前必修/"
            "P1 排期/P2 人工裁量")


def _evidence_block(finding):
    if "rule_id" in finding:
        return (f"{finding['file']}:{finding['line']}\n\n"
                f"「{finding['word']}」[{finding['rule_id']}·{finding['category']}]")
    if "gap_type" in finding:
        return f"{finding['asset_id']} —— {finding.get('evidence') or finding['gap_type']}"
    return finding.get("msg") or "—"


def draft_case(finding, count=None):
    """审查发现 → 五段式案例草稿 dict {filename, text, severity, sections}。

    入库走人工确认: 文件名带 草稿- 前缀, 改名为 案例- 前缀并赋 id 后
    才能被 zhuyi.cases.load_cases 检索命中。
    """
    p = prescribe_mod.normalize_gap(finding)
    now = datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
    dstr = now.strftime("%Y%m%d")
    slug = _slug_for(finding)
    related = finding.get("file") or finding.get("asset_id") or ""
    kw = finding.get("word") or finding.get("gap_type") or "探针"
    phen = f"{p['desc']}——由竹园审查发现于 {p['id']}。"
    if count and count > 1:
        phen += f"同类发现共 {count} 处(本草稿为代表样本, 清单见盘点台账/合规报告)。"
    lines = [
        "---",
        f"id: 待定(人工确认时按竹医编号规则赋 C-{dstr}-NN)",
        f"title: {p['desc'][:40]}",
        f"date: {now.strftime('%Y-%m-%d')}",
        f"severity: {p['level']}",
        "source: 竹园 zhuyuan evolve 案例草稿(80号·T3.2, 入库走人工确认)",
        f"keywords: [{kw}, {slug}]",
    ]
    if related:
        lines += ["related_files:", f"  - {related}"]
    lines += [
        "---", "", f"# 草稿-{dstr}-{slug}", "",
        "## 一、现象", "", phen, "",
        "## 二、根因", "", _root_cause(finding), "",
        "## 三、错误代码/配置", "", _evidence_block(finding), "",
        "## 四、正确写法(只出不施)", "", p["suggestion"], "",
        "## 五、预防规则", "", _prevention(finding), "",
        "> 草稿状态: 未经人工确认不入库——审阅后改名为 案例- 前缀并赋 id,",
        "> zhuyi.cases 检索即刻可命中(五段规范对齐案例库 README)。", ""]
    text = "\n".join(lines)
    sections = sum(1 for s in ("## 一、现象", "## 二、根因", "## 三、错误代码/配置",
                               "## 四、正确写法", "## 五、预防规则") if s in text)
    return {"filename": f"草稿-{dstr}-{slug}.md", "text": text,
            "severity": p["level"], "sections": sections}


def collect_findings(repo_root=None):
    """真实审查发现(盘点 missing 行+合规违规)按类分组 → [(代表发现, 同类数)]。

    按处方级别 P0>P1>P2 排序, 每类出一份代表草稿(计数写入现象段)。
    """
    assets, _summary = audit_mod.scan_assets(repo_root)
    missing = [a for a in assets if a.get("status") == "missing"]
    violations = comply_mod.scan_compliance(repo_root)
    groups = {}
    for f in missing:
        groups.setdefault(("audit", f["gap_type"]), []).append(f)
    for f in violations:
        groups.setdefault(("comply", f["category"]), []).append(f)
    reps = [(fs[0], len(fs)) for fs in groups.values()]
    _order = {"P0": 0, "P1": 1, "P2": 2}
    reps.sort(key=lambda kv: _order.get(
        prescribe_mod.normalize_gap(kv[0])["level"], 9))
    return reps


def run(args=None):
    """zhuyuan evolve CLI: 审查发现 → 五段式案例草稿——入库走人工确认。"""
    reps = collect_findings()
    out_dir = DRAFTS_DIR or os.path.join(
        audit_mod._REPO_ROOT, "docs", "registry", "cases")
    os.makedirs(out_dir, exist_ok=True)
    print(f"[zhuyuan evolve] 自进化案例草稿: 审查发现按类分组 {len(reps)} 类")
    for rep, n in reps:
        d = draft_case(rep, count=n)
        path = os.path.join(out_dir, d["filename"])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(d["text"])
        print(f"  草稿已生成: {d['filename']} [severity {d['severity']}"
              f" · 五段 {d['sections']}/5 · 代表同类 {n} 处]")
    print("[zhuyuan evolve] 入库走人工确认: 草稿- 前缀不被案例库加载"
          "(load_cases 只认 案例-); 审阅后改名+赋 id 即完成入库, 检索即刻命中")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
