"""zhuyan evolve: 进化案例草稿（81号·T3.1, 入库走人工确认）

类比推荐发现(优先进化区目标+痛点+三策略) → EVO 进化案例五段式草稿
(对齐竹医/竹园案例库规范): front matter(id 待人工赋 EVO 编号/severity/
keywords/related_files) + 一、现象 / 二、根因(待核实假设) /
三、错误代码/配置(痛点证据) / 四、正确写法(只出不施=三策略+行动要点) /
五、预防规则。
入库=人工确认: 草稿落 docs/registry/cases/草稿-EVO-*.md; zhuyi.cases.load_cases
只认 案例- 前缀 → 草稿不被检索命中——人工审阅后改名+赋 EVO id 才完成入库。
"""
import datetime
import os

try:
    from zhuyan import analogize as analogize_mod
    from zhuyan import fingerprint
    from zhuyan import matrix as matrix_mod
    from zhuyan import score as score_mod
except ImportError:
    import sys
    _pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pkg not in sys.path:
        sys.path.insert(0, _pkg)
    from zhuyan import analogize as analogize_mod
    from zhuyan import fingerprint
    from zhuyan import matrix as matrix_mod
    from zhuyan import score as score_mod

# 草稿目录注入点(测试隔离; None=默认 REPO_ROOT/docs/registry/cases)
DRAFTS_DIR = None

# 痛点主类 → slug
_SLUG = {"无关联测试": "缺测试", "测试保障弱": "测试弱", "契约不清": "契约缺",
         "容错特征不足": "容错缺", "耦合偏高": "耦合高", "罚分": "罚分命中"}

_SECTIONS = ("## 一、现象", "## 二、根因", "## 三、错误代码/配置",
             "## 四、正确写法(只出不施)", "## 五、预防规则")


def _zhuyi_cases():
    import sys
    try:
        from zhuyi import cases
    except ImportError:
        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if pkg not in sys.path:
            sys.path.insert(0, pkg)
        from zhuyi import cases
    return cases


def _pain_key(pain):
    for k in _SLUG:
        if pain.startswith(k):
            return k
    return pain.split("(")[0].strip() or "其他"


def _root_cause(plan):
    unit = plan["unit"]
    if unit["unit_type"] == "page":
        return ("页面资产创建时未过《资产标准》七项口径+SEO 基线——"
                "竹园 audit 台账与竹演 S_e 契约维度共同暴露(根因待人工核实)")
    if not plan["m"]["se_ev"].get("tests"):
        return ("功能上线时未配套关联测试(竹医 qr/b1-b3 基线模式未覆盖此面)"
                "——测试欠账在 S_e 测试保障维度如实呈现(根因待人工核实)")
    return ("迭代优先业务功能、欠下工程债(契约/容错/耦合)——"
            "三维评分低分维度集中暴露(根因待人工核实)")


def _prevention(plan):
    return ("新功能接入时跑 zhuyan score 三维评分(基线测试命中加权 1.5)+"
            "matrix 四象限定位; 优先进化区目标经 analogize 三策略出方案"
            "(只出不施)再人工落地; antipattern 五类快筛(B4 拦截 5/5)前置过闸")


def draft_evo_case(plan, count=None):
    """类比推荐 plan → EVO 五段式草稿 dict {filename, text, severity, sections}。"""
    now = datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
    dstr = now.strftime("%Y%m%d")
    unit = plan["unit"]
    pains = plan["pains"]
    slug = _SLUG.get(_pain_key(pains[0] if pains else ""), "进化欠账")
    m = plan["m"]
    phen = (f"{unit['unit_id']}(总分 {m['total']}, {m['grade']})落 matrix 优先进化区"
            f"——痛点: {'; '.join(pains)}。由竹演类比推荐发现(T2.4)。")
    if count and count > 1:
        phen += f"同类目标共 {count} 个(本草稿为代表样本, 全量见矩阵报告)。"
    strat_lines = []
    for name, action, note in plan["strategies"]:
        strat_lines += [f"- **{name}**: {action}", f"  - {note}"]
    case_note = ""
    if plan["case_ref"]:
        c = plan["case_ref"]
        case_note = (f"\n- 案例修正模板可移植: {c['id']}「{c['title']}」"
                     f"({c['score']} 分, 命中: {', '.join(c['hits'])})")
    lines = [
        "---",
        f"id: 待定(人工确认时按竹演进化编号规则赋 EVO-{dstr}-NN)",
        f"title: {unit['unit_id']}进化方案({slug})",
        f"date: {now.strftime('%Y-%m-%d')}",
        f"severity: {'P0' if m['pen_ev']['vetoed'] else 'P1' if m['grade'] == 'low' else 'P2'}",
        "source: 竹演 zhuyan evolve 进化案例草稿(81号·T3.1, 入库走人工确认)",
        f"keywords: [进化方案, {slug}, 优先进化区]",
        "related_files:",
        f"  - {unit.get('file') or unit['unit_id']}",
        "---", "", f"# 草稿-EVO-{dstr}-{slug}", "",
        "## 一、现象", "", phen, "",
        "## 二、根因", "", _root_cause(plan), "",
        "## 三、错误代码/配置", "",
        (f"{unit['unit_id']} —— S_b={m['s_b']}/S_r={m['s_r']}/S_e={m['s_e']}"
         f"/罚分 {m['penalty']}(证据链见评分报告)"), "",
        "## 四、正确写法(只出不施)", "",
        "三策略(人工选择采纳, Agent 不代施):",
        *strat_lines, case_note.strip("\n"), "",
        "## 五、预防规则", "", _prevention(plan), "",
        "> 草稿状态: 未经人工确认不入库——审阅后改名为 案例- 前缀并赋 EVO id,",
        "> zhuyi.cases 检索即刻可命中(五段规范对齐案例库 README)。", ""]
    text = "\n".join(lines)
    sections = sum(1 for s in _SECTIONS if s in text)
    return {"filename": f"草稿-EVO-{dstr}-{slug}.md", "text": text,
            "severity": "P1", "sections": sections}


def collect_evolutions():
    """优先进化区目标深度类比 → 按痛点主类分组 → [(代表 plan, 同类数)]。"""
    units = fingerprint.enumerate_units()
    ctxs = (score_mod.build_sb_context(), score_mod.build_sr_context(units),
            score_mod.build_se_context(units))
    src_ctx = ctxs[1]  # sr 文本缓存与 antipattern 源缓存同构
    mtx = matrix_mod.build_matrix(units, ctxs)
    evos = [m for m, q in mtx if q == "优先进化区"]
    evos.sort(key=lambda m: -m["total"])
    all_cases = _zhuyi_cases().load_cases()
    cands = analogize_mod.seed_mod.top_candidates(units, ctxs, top_n=1)
    ref_seed = cands[0][0]["unit"]["unit_id"] if cands else "无候选(如实)"
    groups = {}
    for m in evos:
        plan = analogize_mod.analogize_unit(
            m["unit"]["unit_id"], ctxs, units, src_ctx, all_cases,
            ref_seed=ref_seed)
        groups.setdefault(_pain_key(plan["pains"][0] if plan["pains"] else ""),
                          []).append(plan)
    return [(g[0], len(g)) for g in groups.values()]


def run(args=None):
    """zhuyan evolve CLI: 类比推荐发现 → EVO 进化案例草稿——入库走人工确认。"""
    reps = collect_evolutions()
    out_dir = DRAFTS_DIR or os.path.join(
        fingerprint.REPO_ROOT, "docs", "registry", "cases")
    os.makedirs(out_dir, exist_ok=True)
    print(f"[zhuyan evolve] 进化案例草稿: 优先进化区按痛点主类分组 {len(reps)} 类")
    for rep, n in reps:
        d = draft_evo_case(rep, count=n)
        path = os.path.join(out_dir, d["filename"])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(d["text"])
        print(f"  草稿已生成: {d['filename']} [severity {d['severity']}"
              f" · 五段 {d['sections']}/5 · 代表同类 {n} 个]")
    print("[zhuyan evolve] 入库走人工确认: 草稿-EVO- 前缀不被案例库加载"
          "(load_cases 只认 案例-); 审阅后改名+赋 EVO id 即完成入库, "
          "检索即刻命中")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
