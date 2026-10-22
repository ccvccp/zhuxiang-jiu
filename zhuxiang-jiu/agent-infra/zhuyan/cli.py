"""竹演 zhuyan CLI（81号·进化功能 Agent · P0 T0.1 骨架）

四子命令（逐任务实现，当前为骨架占位）:
  score      成熟度评分——三维加权 S_b 业务价值/S_r 运行稳健/S_e 工程复用+罚分项 → 评分报告
  matrix     痛点矩阵——价值×质量四象限(优先进化区/战略保护区/观察等待区/维持现状区)
  analogize  类比推荐——种子检索+差距分析+反模式拦截 → 进化方案三策略(只出不施)
  evolve     进化沉淀——进化案例 EVO 编号五段式入 cases 库(人工确认门)

用法: python agent-infra/zhuyan/cli.py <子命令> [参数]
"""
import argparse
import os
import sys

__version__ = "0.1.0"

STUBS = {
    "evolve": "P3(T3.2) 实现: 进化案例 EVO 编号五段式/六要素 schema→草稿人工确认门入 cases",
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="zhuyan",
        description="竹演进化功能 Agent（81号, P0 骨架）——评分/矩阵/类比/进化",
    )
    sub = parser.add_subparsers(dest="command", metavar="<子命令>")
    for name, help_text in (
        ("score", "成熟度评分（三维加权+罚分项 → 评分报告, B3 分档回放）"),
        ("matrix", "痛点矩阵（价值×质量 → 四象限分类报告）"),
        ("analogize", "类比推荐（种子+反模式过滤 → 进化方案, 只出不施）"),
        ("evolve", "进化沉淀（EVO 案例五段式入库, 人工确认门）"),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--json", action="store_true", help="输出机器可读结果（预留）")
        if name == "analogize":
            sp.add_argument("--target", default=None, help="目标功能（预留）")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "score":
        pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if pkg_parent not in sys.path:
            sys.path.insert(0, pkg_parent)
        from zhuyan import score
        return score.run(args)
    if args.command == "matrix":
        pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if pkg_parent not in sys.path:
            sys.path.insert(0, pkg_parent)
        from zhuyan import matrix
        return matrix.run(args)
    if args.command == "analogize":
        pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if pkg_parent not in sys.path:
            sys.path.insert(0, pkg_parent)
        from zhuyan import analogize
        return analogize.run(args)
    if args.command in STUBS:
        print(f"[zhuyan {args.command}] 骨架占位——{STUBS[args.command]}")
        return 0
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
