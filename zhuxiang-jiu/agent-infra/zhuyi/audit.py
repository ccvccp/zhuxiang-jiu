"""zhuyi audit: L1 业务链完整性审查（79号·P1, 当前实现 T1.1 端点在册检查）

链步骤端点 × registry.json → 端点存在性检查（6 链逐 27 步）。
miss = 端点不在册（断链候选第一类）。后续任务逐检扩展本模块:
T1.2 鉴权可达（qr benchmark 核心）/ T1.3 前端调用点 / T1.4 数据流衔接 /
T1.5 断链六段式报告。
"""
try:
    from zhuyi import chains as chains_mod
except ImportError:
    import chains as chains_mod


def check_endpoint_registry(chs=None, registry=None):
    """第一检: 端点在册。返回 (findings, total)。

    findings: 非 hit 项明细 [{chain, step, label, method, path, kind}],
    kind=miss(不在册)/partial(模糊降级)。total: 对齐汇总(对齐 chains 口径)。
    """
    chs = chs if chs is not None else chains_mod.parse_chains()
    registry = registry if registry is not None else chains_mod.load_registry()
    rep, total = chains_mod.align_registry(chs, registry)
    findings = []
    for chain in chs:
        for step in chain["steps"]:
            for kind, method, path in rep[chain["name"]][step["no"]]["results"]:
                if kind != "hit":
                    findings.append({
                        "chain": chain["name"], "step": step["no"],
                        "label": step["label"], "method": method,
                        "path": path, "kind": kind,
                    })
    return findings, total


def run(args=None):
    """audit CLI 入口: 当前输出第一检（端点在册）, T1.2-T1.5 逐任务补齐。"""
    print("[zhuyi audit] L1 业务链审查 · T1.1 端点在册检查")
    findings, total = check_endpoint_registry()
    for f in findings:
        print(f"  [{f['kind']}] {f['chain']} 步骤{f['step']}({f['label']}): "
              f"{f['method']} {f['path']}")
    print(f"  扫描汇总: hit={total['hit']} partial={total['partial']} "
          f"miss={total['miss']} internal={total['internal']}")
    print("[zhuyi audit] T1.2 鉴权可达 / T1.3 前端调用点 / "
          "T1.4 数据流衔接 / T1.5 断链报告: 后续任务实现")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
