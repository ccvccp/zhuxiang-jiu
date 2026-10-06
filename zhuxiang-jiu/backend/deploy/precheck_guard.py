#!/usr/bin/env python
"""部署预检: git 变更 → 模块保护引擎判定(语音链先行)

用法(仓库根执行):
    python backend/deploy/precheck_guard.py            # 全量预检(工作区+暂存 vs HEAD)
    python backend/deploy/precheck_guard.py f1 f2 ...  # 显式文件清单

集成: 部署脚本第一步调用本脚本, verdict=block 时停止部署。
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "zhuxiang-jiu" / "backend"))

from core.module_guard import full_guard  # noqa: E402


def git_changed() -> list[str]:
    """工作区+暂存 相对 HEAD 的变更清单(删除/修改/新增)"""
    out = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True, text=True, cwd=REPO_ROOT)
    files = [f.strip() for f in (out.stdout or "").splitlines()
             if f.strip()]
    out2 = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True, cwd=REPO_ROOT)
    files += [f.strip() for f in (out2.stdout or "").splitlines()
              if f.strip()]
    return files


def main() -> int:
    files = sys.argv[1:] or git_changed()
    if not files:
        print("无变更——预检通过")
        return 0
    print(f"检测到 {len(files)} 个变更文件, 进入保护引擎判定...")
    r = full_guard(files, REPO_ROOT)
    return 0 if r["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
