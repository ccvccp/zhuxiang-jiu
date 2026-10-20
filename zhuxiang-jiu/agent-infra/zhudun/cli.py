"""竹盾 zhudun CLI（78号·网站开发增量管理 Agent · P1 T1.1 骨架）

三子命令（P1 逐任务实现，当前为骨架占位）:
  index   资产图谱重建——吸收 P0 临时工具 scan_endpoints/scan_modules/scan_pages/build_registry_json
  impact  影响评估——git diff 改动面 × registry.json(红线/开关/端点/行为资产) → 影响报告 MD
  gate    增量门禁——ZHUDUN_MODE(off/shadow/assist/full) 档位查询与拦截判定

用法: python agent-infra/zhudun/cli.py <子命令> [参数]
"""
import argparse
import os
import sys

__version__ = "0.1.0"

STUBS = {
    "index": "T1.2 实现: 吸收 docs/registry/tools/{scan_endpoints,scan_modules,scan_pages,build_registry_json}.py 后移除临时工具",
    "impact": "T1.3-T1.7 实现: git diff 改动面 → registry.json 命中判定(红线文件面/开关回滚值/行为资产) → docs/registry/reports/影响报告-*.md",
    "gate": "T1.9/P2-P3 实现: ZHUDUN_MODE=off/shadow/assist/full; assist=缺报告提示人工放行, full=pre-commit+PR 阻断含 Override 逃生",
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="zhudun",
        description="竹盾增量管理 Agent（78号, P1 骨架）——影响评估与增量门禁",
    )
    sub = parser.add_subparsers(dest="command", metavar="<子命令>")
    for name, help_text in (
        ("index", "资产图谱重建（L0 三清单 + registry.json）"),
        ("impact", "影响评估（改动面 → 红线命中 → 影响报告）"),
        ("gate", "增量门禁（ZHUDUN_MODE 档位判定）"),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--json", action="store_true", help="输出机器可读结果（P1 预留）")
        if name == "impact":
            sp.add_argument("--base", default="origin/master",
                            help="对照基线（默认 origin/master）")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    import sys
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)
    if args.command == "index":
        from zhudun import index
        return index.run(args)
    if args.command == "impact":
        from zhudun import impact
        return impact.run(args)
    print(f"[zhudun {args.command}] 骨架占位——{STUBS[args.command]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
