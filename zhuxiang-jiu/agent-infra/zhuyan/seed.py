"""zhuyan seed: 黄金种子核查（81号·T2.2）

前沿五维种子标准裁剪为静态可取证口径(APM 缺位, 排除项触发如实标注):
1. 稳定性守护: 零罚分+无硬否决(排除=罚分/硬否决命中——如密钥字面量)
2. 业务普适: 前端调用面≥3 处或业务链命中(弱项=WATCH 不硬排除)
3. 工程完备: 测试保障≥0.7 且契约≥0.5(排除=无关联测试)
4. 模式代表: 容错特征≥2 类(排除=零增强模式纯 CRUD)
5. 低耦合可迁移: import≤3(排除=≥7 强绑定)
候选来源: M1 已证 high=0 如实——从 mid 层(178 个)按总分择优 Top N。
判定: PASS(黄金种子模板池)/EXCLUDE(排除项触发)/WATCH(部分达标)。
种子核查为 T2.4 类比推荐的模板来源, 报告只出不施。
"""
import os

try:
    from zhuyan import fingerprint
    from zhuyan import score as score_mod
except ImportError:
    import sys
    _pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pkg not in sys.path:
        sys.path.insert(0, _pkg)
    from zhuyan import fingerprint
    from zhuyan import score as score_mod

SEED_TOP_N = 10
# 归档目录注入点(测试隔离; None=默认 REPO_ROOT/docs/registry/reports)
REPORTS_DIR = None

_DIMS = ("稳定性守护", "业务普适", "工程完备", "模式代表", "低耦合可迁移")


def check_seed(m):
    """单候选五维核查 → (verdict, checks[(dim, ok, detail)], excludes[])。

    m 为 score.maturity_unit 返回的成熟度 dict(含四组证据)。
    """
    sb_ev, sr_ev, se_ev, pen_ev = (m["sb_ev"], m["sr_ev"],
                                   m["se_ev"], m["pen_ev"])
    checks, excludes = [], []

    stable = (not pen_ev["vetoed"]) and m["penalty"] == 0
    if not stable:
        excludes.append(f"稳定性: 罚分 {m['penalty']}"
                        f"/硬否决 {pen_ev['vetoed']}")
    checks.append(("稳定性守护", stable,
                   f"罚分 {m['penalty']}, 硬否决 {pen_ev['vetoed']}"))

    calls = sb_ev.get("call_count", 0)
    chains = sb_ev.get("hit_chains") or []
    universal = calls >= 3 or bool(chains)
    checks.append(("业务普适", universal,
                   f"调用 {calls} 处, 链命中 {len(chains)} 条"))

    complete = (se_ev.get("test_value", 0) >= 0.7
               and se_ev.get("contract", 0) >= 0.5)
    if not (se_ev.get("tests") or []):
        excludes.append("工程完备: 无关联测试")
    checks.append(("工程完备", complete,
                   (f"测试 {se_ev.get('test_value')}, "
                    f"契约 {se_ev.get('contract')}")))

    features = sr_ev.get("fault_features") or []
    representative = len(features) >= 2
    if not features:
        excludes.append("模式代表: 零增强模式(纯 CRUD)")
    checks.append(("模式代表", representative,
                   f"容错特征 {features or '无'}"))

    imports = se_ev.get("imports", 0)
    migratable = imports <= 3
    if imports >= 7:
        excludes.append(f"可迁移: 外部依赖 {imports} 个(强绑定)")
    checks.append(("低耦合可迁移", migratable, f"import {imports} 个"))

    if excludes:
        verdict = "EXCLUDE"
    elif all(ok for _n, ok, _d in checks):
        verdict = "PASS"
    else:
        verdict = "WATCH"
    return verdict, checks, excludes


def top_candidates(units=None, ctxs=None, top_n=SEED_TOP_N):
    """种子候选: high 层(如有)+mid 层按总分择优 Top N → [(m, rank)]。"""
    units = units if units is not None else fingerprint.enumerate_units()
    if ctxs is None:
        ctxs = (score_mod.build_sb_context(), score_mod.build_sr_context(units),
                score_mod.build_se_context(units))
    scored = [score_mod.maturity_unit(u, *ctxs) for u in units]
    pool = sorted((m for m in scored if m["grade"] in ("high", "mid")),
                  key=lambda m: -m["total"])
    return [(m, i + 1) for i, m in enumerate(pool[:top_n])]


def generate_report(candidates, out_dir=None):
    """T2.2 种子核查报告(四节) → 归档路径。"""
    import datetime
    out_dir = out_dir or REPORTS_DIR or os.path.join(
        fingerprint.REPO_ROOT, "docs", "registry", "reports")
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
    path = os.path.join(out_dir, now.strftime("种子核查报告-%Y%m%d-%H%M%S.md"))
    results = [(m, rank, *check_seed(m)) for m, rank in candidates]
    lines = ["# 种子核查报告 · 竹演 zhuyan seed（81号·黄金种子标准）", ""]
    lines.append(f"> 生成: {now.strftime('%Y-%m-%d %H:%M:%S')} · "
                 f"候选 {len(results)} 个 · 口径: 前沿五维标准静态取证版"
                 "(M1 已证 high=0, 候选取自 mid 层择优)")
    lines += ["", "## 一、五维逐项 Check", ""]
    for m, rank, verdict, checks, _ex in results:
        lines.append(f"- [{verdict}] #{rank} {m['unit']['unit_id']}"
                     f"(总分 {m['total']}, {m['grade']}):")
        for dim, ok, detail in checks:
            lines.append(f"  - {'✓' if ok else '✗'} {dim}: {detail}")
    lines += ["", "## 二、排除项触发清单(如实标注)", ""]
    excluded = [r for r in results if r[2] == "EXCLUDE"]
    if excluded:
        for m, rank, _v, _c, ex in excluded:
            for e in ex:
                lines.append(f"- #{rank} {m['unit']['unit_id']} —— {e}")
    else:
        lines.append("- 无排除项触发(候选池均未被硬排除)")
    lines += ["", "## 三、判定汇总", ""]
    for verdict in ("PASS", "WATCH", "EXCLUDE"):
        grp = [r for r in results if r[2] == verdict]
        lines.append(f"- {verdict}: {len(grp)} 个"
                     + ("——黄金种子模板池" if verdict == "PASS" else ""))
    lines += ["", "## 四、口径声明", "",
              ("- 五维标准为前沿文档静态取证裁剪版(稳定性=罚分零命中代理 90 天零故障, "
               "运行时数据挂账);"),
              ("- 种子核查为 T2.4 类比推荐模板来源; "
               "**只出不施**——核查报告不改代码不动资产。"), ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def run(args=None):
    """zhuyan seed: mid 层择优 Top N 五维核查+排除项标注+报告归档。"""
    candidates = top_candidates()
    results = [(m, rank, *check_seed(m)) for m, rank in candidates]
    print(f"[zhuyan seed] 黄金种子核查: mid 层择优 Top {len(results)} 候选"
          "(M1 已证 high=0 如实, 从 178 个 mid 择优)")
    for m, rank, verdict, checks, ex in results[:5]:
        passed = sum(1 for _d, ok, _t in checks if ok)
        print(f"  [{verdict}] #{rank} {m['unit']['unit_id']}"
              f" 总分{m['total']} 五维 {passed}/5"
              + (f" 排除: {ex[0]}" if ex else ""))
    verdicts = {}
    for _m, _r, v, _c, _e in results:
        verdicts[v] = verdicts.get(v, 0) + 1
    print("  判定汇总: " + ", ".join(f"{k}={v}" for k, v in verdicts.items()))
    path = generate_report(candidates)
    print(f"[zhuyan seed] T2.2 种子核查报告已归档: {path}")
    print("[zhuyan seed] PASS 候选入黄金种子模板池, 供 T2.4 类比推荐引用"
          "(只出不施)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
