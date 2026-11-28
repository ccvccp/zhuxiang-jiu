import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from model_adapter import ModelUnavailable
from orchestrator import accept_run, revalidate_run, run_pipeline, status_run, sync_run
from render_docx import RenderError, render_document


def build_parser():
    parser = argparse.ArgumentParser(prog="zhuchuang", description="竹创多 Agent 创新规划与 Word 交付")
    sub = parser.add_subparsers(dest="command", metavar="<命令>")
    run = sub.add_parser("run", help="生成四章源稿和合并 Word")
    run.add_argument("--topic", default="")
    run.add_argument("--request-file")
    run.add_argument("--repo-root")
    run.add_argument("--output-dir")
    run.add_argument("--generation", choices=("auto", "external", "rule"), default="auto")
    run.add_argument("--model", default="")
    run.add_argument("--resume", help="从既有运行目录恢复；与 --topic 互斥")
    render = sub.add_parser("render", help="按已审查源稿重新生成 Word")
    render.add_argument("--run-dir", required=True)
    accept = sub.add_parser("accept", help="导入真实验收证据并重新审查")
    accept.add_argument("--run-dir", required=True)
    accept.add_argument("--evidence", required=True)
    sync = sub.add_parser("sync", help="同步真实开发或部署证据并重新审查")
    sync.add_argument("--run-dir", required=True)
    sync.add_argument("--update", required=True)
    status = sub.add_parser("status", help="只读查看统一运行状态")
    status.add_argument("--run-dir", required=True)
    revalidate = sub.add_parser("revalidate", help="不调用模型，重新审查当前源稿")
    revalidate.add_argument("--run-dir", required=True)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        if args.command == "run":
            if args.resume and args.topic:
                raise ValueError("--resume 与 --topic 不能同时使用")
            if not args.resume and not args.topic.strip():
                raise ValueError("创新主题不能为空")
            request = Path(args.request_file).read_text(encoding="utf-8") if args.request_file else ""
            manifest = run_pipeline(
                topic=args.topic, request=request, repo_root=args.repo_root,
                output_root=args.output_dir, generation=args.generation,
                model=args.model, resume_dir=args.resume,
            )
            print(json.dumps(manifest, ensure_ascii=False))
            return 0 if manifest["review"]["word_ready"] else 4
        if args.command == "render":
            print(str(render_document(args.run_dir)))
            return 0
        if args.command == "accept":
            manifest = accept_run(args.run_dir, args.evidence)
            print(json.dumps(manifest, ensure_ascii=False))
            return 0 if manifest["review"]["word_ready"] else 4
        if args.command == "sync":
            manifest = sync_run(args.run_dir, args.update)
            print(json.dumps(manifest, ensure_ascii=False))
            return 0 if manifest["review"]["word_ready"] else 4
        if args.command == "status":
            print(json.dumps(status_run(args.run_dir), ensure_ascii=False))
            return 0
        if args.command == "revalidate":
            manifest = revalidate_run(args.run_dir)
            print(json.dumps(manifest, ensure_ascii=False))
            return 0 if manifest["review"]["word_ready"] else 4
    except ModelUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except RenderError as exc:
        print(str(exc), file=sys.stderr)
        return 5
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 5 if args.command == "render" and isinstance(exc, OSError) else 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
