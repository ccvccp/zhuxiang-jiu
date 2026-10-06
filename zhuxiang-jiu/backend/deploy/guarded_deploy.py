#!/usr/bin/env python
"""守卫部署包装(模块保护引擎接入部署链, 跨平台)

用法(任意 cwd, 文件路径相对 git 仓库根):
    python backend/deploy/guarded_deploy.py <文件1> <文件2> ... -- <部署命令>

示例(部署语音服务, 自动过三检后 cp+restart):
    python backend/deploy/guarded_deploy.py \
        zhuxiang-jiu/backend/services/asr_stream_service.py -- \
        "docker cp /opt/zhuxiang/deploy_tmp/guarded/asr_stream_service.py \
         zhuxiang-backend-1:/app/services/ && docker restart zhuxiang-backend-1"

流程: precheck(命中守卫→套件/静态/指纹三检) → 全绿 → scp 上传
      → 执行部署命令; 预检红=终止(保护引擎部署必经之路)。
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent.parent          # git 仓库根
HOST = "root@47.236.61.117"
STAGE = "/opt/zhuxiang/deploy_tmp/guarded"


def run(cmd: list[str] | str, shell: bool = False) -> int:
    r = subprocess.run(cmd, shell=shell)
    return r.returncode


def main() -> int:
    args = sys.argv[1:]
    if "--" in args:
        i = args.index("--")
        files, deploy_cmd = args[:i], " ".join(args[i + 1:])
    else:
        files, deploy_cmd = args, ""
    if not files:
        print(__doc__)
        return 2

    print("=========== ① 模块保护预检 ===========")
    rc = run([sys.executable, str(HERE / "precheck_guard.py")] + files)
    if rc != 0:
        print("❌ 预检未过——部署终止(修复套件/静态守卫后重试)")
        return 1

    print("=========== ② 上传暂存 ===========")
    run(["ssh", HOST, f"mkdir -p {STAGE}"])
    for f in files:
        p = Path(f) if Path(f).is_absolute() else REPO_ROOT / f
        if not p.is_file():
            print(f"❌ 本地文件不存在: {f}")
            return 1
        rc = run(["scp", str(p), f"{HOST}:{STAGE}/"])
        if rc != 0:
            print(f"❌ 上传失败: {f}")
            return 1
        print(f"↑ {p.name}")

    print("=========== ③ 部署命令 ===========")
    if deploy_cmd:
        rc = run(["ssh", HOST, deploy_cmd])
        print("✅ 守卫部署完成" if rc == 0
              else f"❌ 部署命令失败(rc={rc})——检查服务器侧")
        return rc
    print(f"✅ 已上传至 {STAGE}(未给部署命令——手动接续 docker cp/restart)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
