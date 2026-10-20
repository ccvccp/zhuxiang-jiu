"""zhudun impact 输入层（T1.3: diff 采集）

改动面 = git diff --name-only <base>（已改未提交 + 已提交未推送）
        ∪ git status --porcelain 未跟踪新文件（新文件也是改动，影响分析需要）
默认基线 = origin/master；生产仓库根 = 本包上三级（d:/网站架构设计）。
后续任务在此层之上叠加: T1.4 反向引用 / T1.5 红线命中 / T1.6 检查清单映射 / T1.7 报告生成。
"""
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEFAULT_BASE = "origin/master"


def changed_files(base=DEFAULT_BASE, repo=DEFAULT_REPO):
    """返回改动文件清单（相对仓库根的路径列表, 已排序）; git 不可用/无差异返回空表"""
    diff = subprocess.run(
        ["git", "diff", "--name-only", base],
        capture_output=True, text=True, encoding="utf-8", check=False, cwd=repo)
    files = {ln.strip().strip('"') for ln in diff.stdout.splitlines() if ln.strip()}
    status = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        capture_output=True, text=True, encoding="utf-8", check=False, cwd=repo)
    for ln in status.stdout.splitlines():
        if ln.startswith("??"):
            name = ln[3:].strip().strip('"')
            if name and not name.endswith("/"):
                files.add(name)
    return sorted(files)


def run(args=None):
    base = getattr(args, "base", None) or DEFAULT_BASE
    files = changed_files(base=base)
    if not files:
        print(f"[zhudun impact] 改动面 0 文件（对照基线 {base}）——T1.4+ 分析层待接入")
        return 0
    print(f"[zhudun impact] 改动面 {len(files)} 文件（对照基线 {base}）:")
    for f in files:
        print("  " + f)
    return 0
