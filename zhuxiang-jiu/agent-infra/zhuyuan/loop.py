"""zhuyuan loop: 闭环演示（80号·T3.3）

盘点 → 合规 → 处方 → 入库(草稿) 四环全链, 一条命令跑通:
    python agent-infra/zhuyuan/loop.py
口径: 只调各环扫描/生成函数(不归档报告不上报度量), 草稿落盘沿用
evolve 人工确认门; 环环输出真数据, 演示自进化闭环:
审查发现 → 词表拦截 → 处方建议 → 案例沉淀(人工确认)。
"""
import os
import sys

# 双模块实例防坑: 同进程测试(顶层模块)优先——conftest 注入同实例
try:
    import audit as audit_mod
    import comply as comply_mod
    import evolve as evolve_mod
    import prescribe as prescribe_mod
except ImportError:
    _pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pkg_parent not in sys.path:
        sys.path.insert(0, _pkg_parent)
    from zhuyuan import audit as audit_mod
    from zhuyuan import comply as comply_mod
    from zhuyuan import evolve as evolve_mod
    from zhuyuan import prescribe as prescribe_mod


def run():
    """四环全链演示: 盘点→合规→处方→入库(草稿)。"""
    print("=" * 60)
    print("竹园 zhuyuan 自进化闭环演示（80号·T3.3）")
    print("=" * 60)

    # 环 1: 盘点
    print("\n【环 1·盘点 audit】资产台账——七项口径扫主站")
    assets, summary = audit_mod.scan_assets()
    missing = [a for a in assets if a.get("status") == "missing"]
    pri = {"P0": 0, "P1": 0, "P2": 0}
    for a in missing:
        if a.get("priority") in pri:
            pri[a["priority"]] += 1
    print(f"  healthy={summary['healthy']} missing={len(missing)} "
          f"exempt={summary['exempt']}"
          f" | P0={pri['P0']} P1={pri['P1']} P2={pri['P2']}")
    for a in missing[:5]:
        print(f"    [{a['priority']}] {a['asset_id']}:{a['gap_type']}"
              f" —— {a['evidence']}")
    if len(missing) > 5:
        print(f"    …(共 {len(missing)} 处, 全量见 audit 台账)")

    # 环 2: 合规
    print("\n【环 2·合规 comply】违禁词引擎扫 html+js")
    violations = comply_mod.scan_compliance()
    hit, _miss = comply_mod.b1_replay()
    rate = int(len(hit) / len(comply_mod.B1_WORDS) * 100)
    high = sum(1 for v in violations if v["risk"] == "high")
    print(f"  检出违规 {len(violations)} 处"
          f"(high {high}/med {len(violations) - high})"
          f" | B1 整改实录回放检出率 {rate}%——违规文案上线即拦")
    for v in violations:
        print(f"    [{v['risk']}:{v['rule_id']}] {v['file']}:{v['line']}"
              f" 「{v['word']}」")

    # 环 3: 处方
    print("\n【环 3·处方 prescribe】缺口→建议+案例引用(只出不施)")
    prescs = prescribe_mod.prescribe_gaps(missing + violations)
    cited = sum(1 for p in prescs if p["case_ref"])
    print(f"  处方 {len(prescs)} 条 | 案例引用命中 {cited} 条"
          f" | 无命中如实标注 {len(prescs) - cited} 条")
    for p in prescs[:5]:
        ref = p["case_ref"]["id"] if p["case_ref"] else "无相关命中(如实)"
        print(f"    [{p['level']}] {p['id']} → 建议+{ref}")
    if len(prescs) > 5:
        print(f"    …(共 {len(prescs)} 条)")

    # 环 4: 入库(草稿, 人工确认门)
    print("\n【环 4·入库 evolve】审查发现→五段式案例草稿(入库走人工确认)")
    reps = evolve_mod.collect_findings()
    out_dir = evolve_mod.DRAFTS_DIR or os.path.join(
        audit_mod._REPO_ROOT, "docs", "registry", "cases")
    os.makedirs(out_dir, exist_ok=True)
    for rep, n in reps:
        d = evolve_mod.draft_case(rep, count=n)
        with open(os.path.join(out_dir, d["filename"]), "w",
                  encoding="utf-8") as fh:
            fh.write(d["text"])
        print(f"  草稿: {d['filename']} [severity {d['severity']}"
              f" · 五段 {d['sections']}/5 · 同类 {n} 处]")
    print("  人工确认门: 草稿- 前缀不被案例库加载, "
          "审阅改名+赋 id 才完成入库(检索即刻命中)")

    print("\n" + "=" * 60)
    print(f"闭环跑通: 盘点发现 {len(missing)} 处 + 合规检出 {len(violations)} 处"
          f" → 处方 {len(prescs)} 条 → 案例草稿 {len(reps)} 份(待人工确认)")
    print("四环环环相扣: 盘点暴露缺口 → 合规拦截违规 → 处方给出建议 → "
          "案例沉淀进化(人工确认门)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
