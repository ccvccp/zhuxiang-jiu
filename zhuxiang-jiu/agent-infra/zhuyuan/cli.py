"""竹园 zhuyuan CLI（80号·网站资产自进化 Agent）

五子命令（T0.1-T3.2 已全部实装分发）:
  audit       资产盘点——主站完整性缺口检测(占位符/空正文/断链图/ALT缺/SEO空) → 资产台账
  comply      合规审查——违禁词 regex 引擎(微信整改 20 处实战沉淀) → 合规报告
  probe       性能探针——Playwright 三探针(基座/转化/信任) → 探针报告
  prescribe   缺口处方——竹医 cases 检索复用 → 修正建议(只出不施)
  evolve      自进化——审查发现 → 五段式案例草稿入库(人工确认)

用法: python agent-infra/zhuyuan/cli.py <子命令> [参数]
"""
import argparse
import os
import sys

__version__ = "0.1.0"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="zhuyuan",
        description="竹园资产自进化 Agent（80号, P0 骨架）——盘点/合规/探针/处方/入库",
    )
    sub = parser.add_subparsers(dest="command", metavar="<子命令>")
    for name, help_text in (
        ("audit", "资产盘点（完整性缺口 → 资产台账, B2 基准）"),
        ("comply", "合规审查（违禁词 regex → 合规报告, B1=微信整改实录回放）"),
        ("probe", "性能探针（Playwright 业务指标 → 探针报告）"),
        ("prescribe", "缺口处方（cases 检索复用 → 只出不施）"),
        ("evolve", "自进化（审查发现 → 案例草稿入库, 人工确认）"),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--json", action="store_true", help="输出机器可读结果（预留）")
        if name == "prescribe":
            sp.add_argument("--query", default=None, help="缺口/违规检索词（预留）")
        if name == "probe":
            sp.add_argument("--url", default=None, help="探针目标 URL（默认线上详情页）")
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
        from zhuyuan import audit
        return audit.run(args)
    if args.command == "comply":
        from zhuyuan import comply
        return comply.run(args)
    if args.command == "probe":
        from zhuyuan import probe
        return probe.run(args)
    if args.command == "prescribe":
        from zhuyuan import prescribe
        return prescribe.run(args)
    if args.command == "evolve":
        from zhuyuan import evolve
        return evolve.run(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
