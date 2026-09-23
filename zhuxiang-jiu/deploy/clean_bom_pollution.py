"""BOM 污染清理: 验证纯 BOM 差异后批量还原(已实证 Trae CN 索引重建所致)

清理策略: 逐文件验证"去掉 BOM 后 diff 为空"才 git checkout 还原
至 HEAD(字节级正确, 不依赖剥层算法——HEAD 本带单层 BOM 的文件
不会剥过头制造新 diff); 含真实改动的跳过并列出。
用法: python -B clean_bom_pollution.py [--dry-run] [--watch]
  --dry-run 仅检测统计(层数分布)不还原
  --watch  清理后静置 90s 复查污染是否继续发生
"""
import os
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


def bom_layers(path):
    """文件首部连续 BOM 层数(证据统计用)"""
    try:
        with open(os.path.join(ROOT, path), "rb") as f:
            b = f.read(64)
        n, i = 0, 0
        while b[i:i + 3] == b"\xef\xbb\xbf":
            n += 1
            i += 3
        return n
    except OSError:
        return 0


def verify(mods):
    """逐文件验证: 返回 (纯BOM列表, 真实差异列表)"""
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
    return pure, risky


def keep_samples(pure):
    """留存损坏样本副本(层数最高的 3 个)——官方回访索要真实损坏
    文件时直取; 层数最高者证据价值最大(轮次次数直读)。"""
    import shutil
    stamp = time.strftime("%Y%m%d_%H%M")
    base = os.path.join(ROOT, "zhuxiang-jiu", "docs",
                        "evidence_samples", stamp)
    ranked = sorted(pure, key=bom_layers, reverse=True)
    kept = 0
    for f in ranked[:3]:
        src = os.path.join(ROOT, f)
        dst = os.path.join(base, f.replace("/", "__"))
        try:
            os.makedirs(base, exist_ok=True)
            shutil.copy2(src, dst)
            kept += 1
        except OSError:
            pass
    if kept:
        tops = ", ".join(f"{f.split('__')[-1] if '__' in f else f}"
                         f"({bom_layers(f)}层)"
                         for f in ranked[:kept])
        print(f"已留存 {kept} 个损坏样本 → docs/evidence_samples/"
              f"{stamp}/  [{tops}]")


def main():
    dry = "--dry-run" in sys.argv
    mods = collect()
    print(f"工作区未暂存修改: {len(mods)} 个"
          + ("  [dry-run]" if dry else ""))
    if not mods:
        print("无需清理")
        return
    pure, risky = verify(mods)
    print(f"纯 BOM 污染: {len(pure)} | 含真实差异跳过: {len(risky)}")
    for f in risky:
        print("   !! " + f)
    # BOM 层数分布统计(证据数据: 印证"每轮叠一层"模型)
    if pure:
        layers = {}
        for f in pure:
            layers[bom_layers(f)] = layers.get(bom_layers(f), 0) + 1
        dist = "  ".join(f"{k} 层 ×{v}" for k, v in sorted(layers.items()))
        print(f"层数分布: {dist}")
    if dry:
        print("[dry-run] 未做任何修改; 去掉 --dry-run 执行还原")
        return
    keep_samples(pure)  # 还原前留存损坏样本(官方回访取证)
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
              + ("  → 污染仍在发生" if again else "  → 无新污染"))


if __name__ == "__main__":
    main()
