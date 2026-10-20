"""zhudun impact 报告生成（T1.7）

全链数据(改动面/反向引用/风险分级/检查清单映射) → docs/registry/reports/影响报告-{ts}.md
报告即交接文档: PR 附件 = junior 教材; 含签字确认行(五步 SOP 门2 凭证)。
"""
import datetime
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPORTS = os.path.abspath(os.path.join(HERE, "..", "..", "docs", "registry", "reports"))

REGRESSION_BY_RISK = {
    "红": "🔴 命中红线——特性测试全锁现行为, 走专项流程(lead 复核, 见红线标记.md 命中判定规则)",
    "高": "🟠 行为资产/运行时开关受及——逐条断言不变式(红线标记.md B 表 / 开关回滚值)",
    "中": "🟡 backend 核心层改动——补跑相关单测, 新逻辑先写测试再实现",
    "低": "⚪ 常规改动——按检查清单映射项执行即可",
}


def generate(files, refs, assessment, checklist, base,
             out_dir=DEFAULT_REPORTS, syms=None, registry_stats=None):
    """组装并落盘影响报告; 返回报告绝对路径"""
    ts = datetime.datetime.now(tz=datetime.timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(out_dir, f"影响报告-{ts}.md")
    counts = {"红": 0, "高": 0, "中": 0, "低": 0}
    for a in assessment.values():
        counts[a["risk"]] += 1
    buf = []
    buf.append(f"# 影响报告 · zhudun impact（{ts}）")
    buf.append("")
    buf.append(f"> 对照基线: `{base}` · 改动面 {len(files)} 文件 · 生成: 竹盾 78号 zhudun impact")
    if registry_stats:
        buf.append(f"> registry 口径: {registry_stats}")
    buf.append("")
    buf.append("## 一、风险分级汇总")
    buf.append("")
    buf.append("| 级别 | 数量 |")
    buf.append("| --- | --- |")
    for lvl in ("红", "高", "中", "低"):
        buf.append(f"| {lvl} | {counts[lvl]} |")
    buf.append("")
    buf.append("## 二、改动面与命中明细")
    buf.append("")
    buf.append("| 文件 | 风险 | 命中 |")
    buf.append("| --- | --- | --- |")
    for f in files:
        a = assessment.get(f, {"risk": "?", "hits": []})
        hits = "; ".join(a["hits"]) if a["hits"] else "-"
        buf.append(f"| {f} | {a['risk']} | {hits} |")
    buf.append("")
    buf.append("## 三、回归建议（按最高风险级执行）")
    buf.append("")
    top = next((lvl for lvl in ("红", "高", "中", "低") if counts[lvl] > 0), None)
    if top:
        buf.append(f"- 本批最高风险级: **{top}** → {REGRESSION_BY_RISK[top]}")
    else:
        buf.append("- 改动面为空——无回归要求")
    buf.append("")
    if refs:
        buf.append(f"## 四、反向引用（改动符号被 {len(refs)} 文件引用）")
        buf.append("")
        buf.append("**复用建议**: 改动符号已被下游引用——优先复核可否扩展现有模块而非另建; 采纳任何复用建议前人工 Read 源码核对（防幻觉复用）。")
        buf.append("")
        buf.append("| 引用方 | 命中行 |")
        buf.append("| --- | --- |")
        for rel, lines in sorted(refs.items())[:30]:
            buf.append(f"| {rel} | {lines} |")
        if len(refs) > 30:
            buf.append(f"| ...（共 {len(refs)} 文件, 报告截断至 30） | |")
        buf.append("")
    if checklist:
        buf.append("## 五、检查清单映射（上线前逐项打勾）")
        buf.append("")
        for f, items in sorted(checklist.items()):
            for it in items:
                buf.append(f"- [ ] **{f}** — {it}")
        buf.append("")
    buf.append("## 六、签字确认（五步 SOP 门 2 凭证, PR 附本报告）")
    buf.append("")
    buf.append("- [ ] lead 已复核本报告（🔴 红线命中处已专项评估）: ________ 日期: ____-__-__")
    buf.append("- [ ] 检查清单映射项已逐项执行: ________ 日期: ____-__-__")
    buf.append("")
    buf.append("> 归档: 本报告与 PR 一一对应, 长期入 git（registry README 规则 4）。")
    buf.append("")
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(buf))
    return path
