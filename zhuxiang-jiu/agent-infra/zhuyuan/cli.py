"""竹园 zhuyuan CLI（80号·网站资产自进化 Agent · P0 T0.1 骨架）

五子命令（逐任务实现，当前为骨架占位）:
  audit       资产盘点——主站完整性缺口检测(占位符/空正文/断链图/ALT缺/SEO空) → 资产台账
  comply      合规审查——违禁词 regex 引擎(微信整改 20 处实战沉淀) → 合规报告
  probe       性能探针——Playwright 业务指标(购买按钮首屏/资质模块) → 探针报告
  prescribe   缺口处方——竹医 cases 检索复用 → 修正建议(只出不施)
  evolve      自进化——审查发现 → 五段式案例草稿入库(人工确认)

用法: python agent-infra/zhuyuan/cli.py <子命令> [参数]
"""
import argparse
import os
import sys

__version__ = "0.1.0"

STUBS = {
    "audit": "P0(T0.3) 实现: 主站 html+js 完整性盘点(占位符/空正文/断链图/ALT 缺/SEO 空)→资产台账, B2 挖空样本检出",
    "comply": "P1(T1.1-T1.3) 实现: 违禁词 regex 引擎(微信整改 20 处实战沉淀)→合规报告, B1 整改实录回放检出 100%",
    "probe": "P2(T2.1-T2.4) 实现: Playwright 业务探针(购买按钮首屏/资质模块/评分展示)→探针报告",
    "prescribe": "P3(T3.1) 实现: 缺口/违规→竹医 cases 检索复用→修正建议处方, 铁律=只出不施",
    "evolve": "P3(T3.2-T3.5) 实现: 审查发现→五段式案例草稿生成入库(人工确认)——自进化闭环",
}


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
    if args.command in STUBS:
        print(f"[zhuyuan {args.command}] 骨架占位——{STUBS[args.command]}")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
