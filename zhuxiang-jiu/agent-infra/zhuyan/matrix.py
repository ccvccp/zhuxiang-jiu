"""zhuyan matrix: 痛点四象限矩阵（81号·T2.1）

S_b(业务价值, 阈值 0.30) × 总分(质量, 阈值 0.40 同分档线) → 四象限:
- 优先进化区: 高价值+低质量 —— Agent 首选目标(进化改造切入点)
- 战略保护区: 高价值+高质量 —— 人工主导保护口径, 改动须人审(如核心账务类)
- 观察等待区: 低价值+低质量 —— 暂不投入(投入产出比低)
- 维持现状区: 低价值+高质量 —— 作为潜在种子(成熟但价值面窄)

阈值论证: 0.30=链命中端点(S_b≈0.43+)/转化页(0.30)达标线, 无关联单元(0-0.15)
不达; 0.40 与分档 mid/low 线一致(同一把质量尺)。
"""
import os
from collections import Counter

try:
    from zhuyan import fingerprint
    from zhuyan import score as score_mod
except ImportError:
    import os
    import sys
    _pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pkg not in sys.path:
        sys.path.insert(0, _pkg)
    from zhuyan import fingerprint
    from zhuyan import score as score_mod

SB_THRESHOLD = 0.30
TOTAL_THRESHOLD = 0.40

# 象限口径(战略保护区=高价值面改动须人工, 挂只出不施)
_QUADRANT_DESC = {
    "优先进化区": "高价值+低质量——Agent 首选进化目标",
    "战略保护区": "高价值+高质量——人工主导保护, 改动须人审",
    "观察等待区": "低价值+低质量——暂不投入",
    "维持现状区": "低价值+高质量——作为潜在种子",
}

# 归档目录注入点(测试隔离; None=默认 REPO_ROOT/docs/registry/reports)
REPORTS_DIR = None


def quadrant_of(sb, total):
    """S_b×总分 → 象限名。"""
    high_value = sb >= SB_THRESHOLD
    high_quality = total >= TOTAL_THRESHOLD
    if high_value and not high_quality:
        return "优先进化区"
    if high_value and high_quality:
        return "战略保护区"
    if not high_value and not high_quality:
        return "观察等待区"
    return "维持现状区"


def build_matrix(units=None, ctxs=None):
    """全量单元 → [(成熟度 dict, 象限名)]。复用 score 三维评分。"""
    units = units if units is not None else fingerprint.enumerate_units()
    if ctxs is None:
        ctxs = (score_mod.build_sb_context(), score_mod.build_sr_context(units),
                score_mod.build_se_context(units))
    out = []
    for u in units:
        m = score_mod.maturity_unit(u, *ctxs)
        out.append((m, quadrant_of(m["s_b"], m["total"])))
    return out


def generate_report(matrix, out_dir=None):
    """T2.1 矩阵报告(四节) → 归档路径。"""
    import datetime
    out_dir = out_dir or REPORTS_DIR or os.path.join(
        fingerprint.REPO_ROOT, "docs", "registry", "reports")
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
    path = os.path.join(out_dir, now.strftime("矩阵报告-%Y%m%d-%H%M%S.md"))
    counts = Counter(q for _m, q in matrix)
    n = len(matrix)
    lines = ["# 矩阵报告 · 竹演 zhuyan matrix（81号·痛点四象限）", ""]
    lines.append(f"> 生成: {now.strftime('%Y-%m-%d %H:%M:%S')} · 单元 {n} 个 · "
                 f"口径: S_b≥{SB_THRESHOLD}(价值线) × 总分≥{TOTAL_THRESHOLD}(质量线)")
    lines += ["", "## 一、四象限总览", ""]
    for q in ("优先进化区", "战略保护区", "观察等待区", "维持现状区"):
        lines.append(f"- {q}: {counts[q]} 个 —— {_QUADRANT_DESC[q]}")
    lines += ["", "## 二、各区样例(按总分排序前 5)", ""]
    for q in ("优先进化区", "战略保护区", "观察等待区", "维持现状区"):
        grp = sorted((m for m, qq in matrix if qq == q),
                     key=lambda m: -m["total"])[:5]
        lines.append(f"- {q}:")
        if grp:
            for m in grp:
                lines.append(f"  - {m['unit']['unit_id']}"
                             f" S_b={m['s_b']} 总分={m['total']}"
                             f"({m['grade']})")
        else:
            lines.append("  - 无")
    lines += ["", "## 三、战略保护区口径(挂只出不施)", "",
              ("- 战略保护区单元业务价值高、改动影响面大——竹演对其仅产出建议,"),
              ("  改造须人工主导并经竹盾五步 SOP; Agent 不自动生成代码。")]
    lines += ["", "## 四、已知样本落区(回放)", ""]
    known = {m["unit"]["unit_id"]: q for m, q in matrix}
    for uid, expect in (
            ("POST /api/entry/qr/{qr_id}/scan", "战略保护区"),
            ("activity-detail.html", "优先进化区")):
        got = known.get(uid, "未在枚举面")
        mark = "✓" if got == expect else "✗"
        lines.append(f"- {mark} {uid} → {got}(预期 {expect})")
    lines += ["", ("> 口径声明: 阈值与《成熟度评分标准》分档线一致; "
                   "四区分类供 T2.2 种子核查与 T2.4 类比推荐消费。"), ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def run(args=None):
    """zhuyan matrix CLI: 四象限分类+计数与样例+矩阵报告归档。"""
    matrix = build_matrix()
    counts = Counter(q for _m, q in matrix)
    print(f"[zhuyan matrix] 痛点四象限: {len(matrix)} 单元已分类"
          f"(价值线 S_b≥{SB_THRESHOLD} × 质量线 总分≥{TOTAL_THRESHOLD})")
    for q in ("优先进化区", "战略保护区", "观察等待区", "维持现状区"):
        print(f"  {q}: {counts[q]} —— {_QUADRANT_DESC[q]}")
    for q in ("优先进化区", "战略保护区"):
        grp = sorted((m for m, qq in matrix if qq == q),
                     key=lambda m: -m["total"])[:3]
        for m in grp:
            print(f"    [{m['total']:.2f}] {m['unit']['unit_id']}"
                  f" S_b={m['s_b']}")
    path = generate_report(matrix)
    print(f"[zhuyan matrix] T2.1 矩阵报告已归档: {path}")
    print("[zhuyan matrix] 战略保护区改动须人工主导(只出不施); "
          "优先进化区为 T2.4 类比推荐首选目标")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
