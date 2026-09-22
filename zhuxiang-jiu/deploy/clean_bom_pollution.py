"""BOM 污染清理(复发版): 验证纯 BOM 差异后批量还原

首清 79 文件后复发(76 文件, mtime 分散于 8 分钟窗口——
常驻进程跟随编辑活动逐文件改写)。策略不变: 逐文件验证
"去掉 BOM 后 diff 为空"才还原, 含真实改动的跳过并列出。
用法: python -B clean_bom_pollution.py [--watch]
  --watch 清理后静置 90s 复查污染是否继续发生(源进程探测)
"""
import subprocess
import sys
import time

ROOT = r"d:\网站架构设计"


def run(*args):
    r = subprocess.run(
        ["git", "-C", ROOT, "-c", "core.quotepath=false"]
        + list(args), capture_output=True)
    return r.stdout.decode("utf-8", "replace")


def collect():
    out = run("status", "--porcelain")
    return [l[3:].strip('"') for l in out.splitlines()
            if l[:2] == " M"]


def main():
    mods = collect()
    print(f"工作区未暂存修改: {len(mods)} 个")
    if not mods:
        print("无需清理")
        return
    pure, risky = [], []
    for f in mods:
        diff = run("diff", "--", f)
        minus = [l for l in diff.splitlines()
                 if l.startswith("-") and not l.startswith("---")]
        plus = [l for l in diff.splitlines()
                if l.startswith("+") and not l.startswith("+++")]
        okf = (len(minus) == 1 and len(plus) == 1
               and minus[0][1:].replace("\ufeff", "")
               == plus[0][1:].replace("\ufeff", ""))
        (pure if okf else risky).append(f)
    print(f"纯 BOM 污染: {len(pure)} | 含真实差异跳过: {len(risky)}")
    for f in risky:
        print("   !! " + f)
    for i in range(0, len(pure), 30):
        subprocess.run(
            ["git", "-C", ROOT, "checkout", "--"] + pure[i:i + 30],
            check=True)
    print(f"已还原 {len(pure)} 个")
    left = collect()
    print(f"剩余未暂存修改: {len(left)} 个")

    if "--watch" in sys.argv:
        print("\n静置 90s 复查(污染源进程是否持续活动)...")
        time.sleep(90)
        again = collect()
        print(f"90s 后未暂存修改: {len(again)} 个"
              + ("  → 污染仍在发生(常驻进程活跃)" if again
                 else "  → 无新污染"))


if __name__ == "__main__":
    main()
