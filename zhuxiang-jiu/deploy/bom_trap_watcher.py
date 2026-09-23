"""BOM 污染抓现行陷阱: 哨兵阵列 + 高频 mtime 监控 + 进程 CPU 双快照

部署: 14 个哨兵文件(撒布项目各目录/各类型) + 高频轮询
6 个历史目标文件的 mtime。任一文件被写入的瞬间:
  1. 毫秒级记录时刻
  2. 立即双快照全进程 CPU(ctypes EnumProcesses/GetProcessTimes,
     ~10ms) —— 写入轮逐文件推进 2-21s/个, 窗口 30s+, 写入者
     进程 CPU 必然持续爬升
  3. 跟踪整轮所有文件变化时刻
判定逻辑:
  - 哨兵也被写 → 全项目扫描型(排除"清单型"假说)
  - CPU 增量 Top 进程 → 写入者实锤
纯标准库零权限, 日志写 %TEMP%(仓库外)。
用法: python -B bom_trap_watcher.py [小时数, 默认 4]
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

ROOT = r"d:\网站架构设计"
LOG = os.path.join(os.environ["TEMP"], "bom_trap.log")

TARGETS = [
    # 6 个历史污染目标(波 3)
    "taro-app/src/pages/activity/index.tsx",
    "taro-app/src/pages/index/index.tsx",
    "taro-app/src/pages/mine/index.tsx",
    "taro-app/test/test-promo.js",
    "zhuxiang-jiu/backend/prod_groupbuy_e2e.py",
    "zhuxiang-jiu/docs/48号_小竹语音_v3低延迟对话_真机验收记录表.md",
]

SENTINELS = [
    "bomsentinel_root.py",
    "bomsentinel_root.md",
    "bomsentinel_root.js",
    "zhuxiang-jiu/bomsentinel_zj.py",
    "zhuxiang-jiu/backend/bomsentinel_bk.py",
    "zhuxiang-jiu/backend/services/bomsentinel_svc.py",
    "zhuxiang-jiu/js/bomsentinel_js.js",
    "zhuxiang-jiu/docs/bomsentinel_doc.md",
    "zhuxiang-jiu/deploy/bomsentinel_dep.py",
    "zhuxiang-jiu/backend/routes/bomsentinel_rt.py",
    "taro-app/bomsentinel_app.ts",
    "taro-app/src/bomsentinel_src.tsx",
    "taro-app/src/pages/bomsentinel_page.tsx",
    "taro-app/test/bomsentinel_test.js",
]

# ---------- ctypes 进程枚举 ----------
psapi = ctypes.WinDLL("psapi")
kernel32 = ctypes.WinDLL("kernel32")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

psapi.EnumProcesses.restype = wintypes.BOOL
psapi.EnumProcesses.argtypes = [
    ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD)]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                  wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_wchar_p]
kernel32.GetProcessTimes.restype = wintypes.BOOL
kernel32.GetProcessTimes.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _ft_ms(ft):
    """FILETIME(100ns) → CPU 累计毫秒"""
    return (ft.dwHighDateTime << 32 | ft.dwLowDateTime) // 10000


def proc_snapshot():
    """全进程 {pid: (name, cpu_ms)} 快照(~10ms)"""
    arr = (wintypes.DWORD * 2048)()
    n = wintypes.DWORD()
    if not psapi.EnumProcesses(arr, ctypes.sizeof(arr),
                               ctypes.byref(n)):
        return {}
    out = {}
    for i in range(n.value // 4):
        pid = arr[i]
        if pid == 0:
            continue
        h = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(512)
            ln = wintypes.DWORD(512)
            name = "?"
            if kernel32.QueryFullProcessImageNameW(
                    h, 0, ln, buf):
                name = buf.value
            c = wintypes.FILETIME()
            e = wintypes.FILETIME()
            k = wintypes.FILETIME()
            u = wintypes.FILETIME()
            cpu = 0
            if kernel32.GetProcessTimes(h, c, e, k, u):
                cpu = _ft_ms(k) + _ft_ms(u)
            out[pid] = (name, cpu)
        finally:
            kernel32.CloseHandle(h)
    return out


def deploy_sentinels():
    """部署哨兵(已存在则跳过)"""
    made = 0
    for i, rel in enumerate(SENTINELS):
        p = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if not os.path.exists(p):
            body = ("# BOM sentinel %02d — 污染源追踪实验文件\n"
                    "# 请勿删除/勿提交; 追踪 BOM 污染写入者\n"
                    "# sentinel-%02d\n" % (i, i))
            if rel.endswith((".py", ".js", ".ts")):
                body = ("// BOM sentinel %02d — 污染源追踪实验文件\n"
                        "// 请勿删除/勿提交; 追踪 BOM 污染写入者\n"
                        "// sentinel-%02d\n" % (i, i))
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            made += 1
    print(f"哨兵已部署: 新建 {made}/{len(SENTINELS)}")


def bom_layers(path):
    try:
        with open(path, "rb") as f:
            b = f.read(12)
        n = 0
        i = 0
        while b[i:i + 3] == b"\xef\xbb\xbf":
            n += 1
            i += 3
        return n
    except OSError:
        return -1


def main():
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    deploy_sentinels()
    watch = {}
    for rel in TARGETS + SENTINELS:
        p = os.path.join(ROOT, rel)
        try:
            watch[rel] = os.path.getmtime(p)
        except OSError:
            watch[rel] = None
    end = time.time() + hours * 3600
    lf = open(LOG, "a", encoding="utf-8", buffering=1)
    lf.write("=== trap watcher start %s (watch=%d files) ===\n"
             % (time.strftime("%m-%d %H:%M:%S"), len(watch)))
    print(f"监控 {len(watch)} 个文件, 持续 {hours} 小时, 日志 {LOG}")

    armed = True  # 事件触发后进入 CPU 采样模式
    last_beat = time.time()

    while time.time() < end:
        changed = []
        for rel, mt in watch.items():
            p = os.path.join(ROOT, rel)
            try:
                cur = os.path.getmtime(p)
            except OSError:
                cur = None
            if cur != mt:
                changed.append((rel, mt, cur))
                watch[rel] = cur
        if changed:
            lf.write("[%s] !!! 写入事件\n" % time.strftime(
                "%m-%d %H:%M:%S"))
            for rel, old, new in changed:
                layers = bom_layers(os.path.join(ROOT, rel))
                lf.write("  %s bom=%d mtime=%s\n"
                         % (rel, layers,
                            time.strftime(
                                "%H:%M:%S", time.localtime(new))
                            if new else "GONE"))
            if armed:
                # CPU 双快照(2s 窗口) 抓写入轮中的活跃进程
                a = proc_snapshot()
                time.sleep(2.0)
                b = proc_snapshot()
                deltas = []
                for pid, (name, cpu) in b.items():
                    if pid in a:
                        d = cpu - a[pid][1]
                        if d > 0:
                            deltas.append((d, pid, name))
                deltas.sort(reverse=True)
                lf.write("  CPU 2s 增量 Top12:\n")
                for d, pid, name in deltas[:12]:
                    lf.write("    %6dms  pid=%-5d %s\n"
                             % (d, pid, name))
                lf.flush()
                armed = False  # 一轮只需一次快照(后续事件仅记录)
        elif not armed and time.time() % 1 < 0.5:
            pass
        if time.time() - last_beat > 600:
            lf.write("[%s] heartbeat\n" % time.strftime(
                "%m-%d %H:%M:%S"))
            last_beat = time.time()
            armed = True  # 恢复布防(下轮事件再快照)
        time.sleep(0.4)
    lf.write("=== trap watcher end %s ===\n"
             % time.strftime("%m-%d %H:%M:%S"))
    lf.close()


if __name__ == "__main__":
    main()
