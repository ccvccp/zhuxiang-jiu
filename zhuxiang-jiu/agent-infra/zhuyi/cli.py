"""竹医 zhuyi CLI（79号·逻辑审查与修正 Agent · P0 T0.1 骨架, audit T1.1 已实装）

三子命令:
  audit     L1 业务链完整性审查——T1.1 端点在册已实装; T1.2-T1.5 逐任务扩展
  contract  L2 前后端契约对齐——前端 JS 调用面 vs 后端路由 → 断契/死条目清单(P2)
  consult   L3 修正处方——cases 故障复盘库检索 → Patch+回归建议+Blast Radius(只出不施, P3)

用法: python agent-infra/zhuyi/cli.py <子命令> [参数]
"""
import argparse
import os
import sys

__version__ = "0.1.0"

STUBS = {
    "audit": "T1.1 已实现(端点在册检查: 链步骤端点 × registry 存在性); T1.2-T1.5 逐任务扩展: 鉴权可达/前端调用点/数据流衔接/断链六段式报告, M1 以 qr 白名单事故回放为 benchmark",
    "contract": "P2 实现(T2.1-T2.4): 前端调用面提取 vs registry 路由对齐, qr 3 死条目回放检出 3/3",
    "consult": "P3 实现(T3.1-T3.5): cases 五段式案例检索 → 处方模板 + zhudun impact Blast Radius + P0/P1/P2 分级, 铁律=只出不施(生成 Patch 建议不写盘)",
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="zhuyi",
        description="竹医逻辑审查与修正 Agent（79号, P0 骨架）——业务链审查/契约对齐/修正处方",
    )
    sub = parser.add_subparsers(dest="command", metavar="<子命令>")
    for name, help_text in (
        ("audit", "L1 业务链完整性审查（断链检测, M1=qr 事故回放）"),
        ("contract", "L2 前后端契约对齐（断契/死条目/未用端点）"),
        ("consult", "L3 修正处方（案例检索+Patch 建议+Blast Radius, 只出不施）"),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--json", action="store_true", help="输出机器可读结果（预留）")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)
    if args.command == "audit":
        from zhuyi import audit
        return audit.run(args)
    if args.command == "contract":
        from zhuyi import contract
        return contract.run(args)
    if args.command in STUBS:
        print(f"[zhuyi {args.command}] 骨架占位——{STUBS[args.command]}")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
