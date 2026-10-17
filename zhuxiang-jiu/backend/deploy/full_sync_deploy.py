#!/usr/bin/env python
"""tar 全量部署(防漂移标准模式, 2026-10-18 漂移修复后固化)

背景: 历史"按需单文件 scp"导致生产与 git 漂移(200 运行文件缺口
+ 1156 换行差异 + 7 孤儿文件), 2026-10-18 全量对齐后 DIFF=0。
本脚本即该次对齐流程的固化——每次部署全量打包, 从根上杜绝漂移。

用法(在 backend 目录或任意 cwd):
    python deploy/full_sync_deploy.py            # 全流程
    python deploy/full_sync_deploy.py --no-up    # 只同步不重建(调试)

流程: ①本地打包(排除依赖/缓存/assets) → ②生产时间戳备份
      → ③上传解压覆盖+清缓存 → ④build+up → ⑤公网冒烟+日志扫描
      → ⑥漂移复扫(生产 py 清单 vs tar 期望, DIFF 必须=0)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
HOST = "root@47.236.61.117"
REMOTE_BACKEND = "/opt/zhuxiang/zhuxiang-jiu/backend"
BACKUP_DIR = "/opt/zhuxiang/backup"

# 打包排除(本地测试依赖/缓存/二进制资产/开发机渲染产物, 口径一致)
TAR_EXCLUDES = [".deps", ".pip_libs", ".git", "__pycache__",
                ".pytest_cache", "assets", "sv73_videos",
                "*.tar.gz", "*.log"]


def run(cmd: list[str] | str, shell: bool = False,
        cwd: Path | None = None, check: bool = True) -> int:
    r = subprocess.run(cmd, shell=shell, cwd=cwd)
    if check and r.returncode != 0:
        sys.exit(f"❌ 命令失败(rc={r.returncode}): {cmd}")
    return r.returncode


def remote(script: str, check: bool = True) -> int:
    return run(["ssh", HOST, script], check=check)


def step(title: str) -> None:
    print(f"\n=========== {title} ===========")


def main() -> int:
    no_up = "--no-up" in sys.argv
    t0 = time.time()

    step("① 本地打包(全量·排除依赖/缓存/assets)")
    tar_path = Path(tempfile.gettempdir()) / "full_sync.tar.gz"
    if tar_path.exists():
        tar_path.unlink()
    args = ["tar", "-czf", str(tar_path)]
    args += [f"--exclude={e}" for e in TAR_EXCLUDES] + ["."]
    run(args, cwd=BACKEND)
    size_mb = tar_path.stat().st_size / 1048576
    print(f"✅ 包体 {size_mb:.1f}MB → {tar_path}")

    step("② 生产时间戳备份")
    stamp = time.strftime("%Y%m%d_%H%M")
    remote(f"mkdir -p {BACKUP_DIR} && cd /opt/zhuxiang/zhuxiang-jiu && "
           f"tar -czf {BACKUP_DIR}/backend_fs_{stamp}.tar.gz "
           f"--exclude='backend/__pycache__' "
           f"--exclude='backend/*/__pycache__' backend/ "
           f"&& echo BACKUP_OK {BACKUP_DIR}/backend_fs_{stamp}.tar.gz")

    step("③ 上传 + 解压覆盖 + 清缓存")
    run(["scp", str(tar_path), f"{HOST}:/tmp/full_sync.tar.gz"])
    remote(f"cd {REMOTE_BACKEND} && tar -xzf /tmp/full_sync.tar.gz -C . && "
           "find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; "
           "echo SYNC_OK")

    if no_up:
        print("\n--no-up: 跳过重建(文件已就位)")
        return 0

    step("④ 重建容器(import 校验兜底)")
    remote("cd /opt/zhuxiang/zhuxiang-jiu && "
           "docker compose build backend 2>&1 | tail -1 && "
           "docker compose up -d backend 2>&1 | tail -1")
    print("⏳ 等待服务就绪...")
    time.sleep(8)

    step("⑤ 公网冒烟 + 日志扫描")
    # 登录(凭证从 HKCU\Environment 读, 避免落文件)
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        phone = winreg.QueryValueEx(k, "PROD_ADMIN_PHONE")[0]
        password = winreg.QueryValueEx(k, "PROD_ADMIN_PASSWORD")[0]
    body = json.dumps({"phone": phone, "password": password}).encode()
    req = urllib.request.Request(
        "https://zyjiu.com/api/auth/login", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        login = json.loads(r.read())
    assert login.get("role") == "admin", f"登录异常: {login}"
    tok = login["accessToken"]
    print("✅ 登录 admin")
    req = urllib.request.Request(
        "https://zyjiu.com/api/douyin/status",
        headers={"Authorization": f"Bearer {tok}", "X-Role": "admin"})
    with urllib.request.urlopen(req, timeout=15) as r:
        assert json.loads(r.read())["data"]["configured"], "抖音模块异常"
    print("✅ 抖音模块 configured")
    err_n = subprocess.run(
        ["ssh", HOST, "docker logs zhuxiang-backend-1 --since 3m 2>&1 | "
         "grep -ciE 'error|traceback' || true"],
        capture_output=True, text=True).stdout.strip()
    assert err_n == "0", f"容器日志 {err_n} 条 error/traceback"
    print("✅ 容器日志零 error")

    step("⑥ 漂移复扫(生产 vs tar 期望, DIFF 须=0)")
    remote(
        "cd " + REMOTE_BACKEND + " && "
        "find . -name '*.py' -not -path './.git/*' -not -path '*__pycache__*' "
        "-not -path './.deps/*' -not -path './.pip_libs/*' "
        "| sed 's|^\\./||' | sort | xargs md5sum > /tmp/fs_prod.txt 2>/dev/null && "
        "tar -tzf /tmp/full_sync.tar.gz | grep '\\.py$' | sed 's|^\\./||' | sort "
        "> /tmp/fs_tar_list.txt && "
        "while read -r f; do [ -f \"$f\" ] && md5sum \"$f\"; done "
        "< /tmp/fs_tar_list.txt | sed 's|  \\./|  |' | sort -k2 > /tmp/fs_expect.txt && "
        "D=$(diff /tmp/fs_prod.txt /tmp/fs_expect.txt | grep -c '^[<>]' || true) && "
        "echo DRIFT_DIFF=$D && [ \"$D\" = \"0\" ] && echo DRIFT_ZERO "
        "|| { echo '❌ 漂移复扫未过'; exit 1; }")
    print("✅ DRIFT_DIFF=0")

    remote("rm -f /tmp/full_sync.tar.gz /tmp/fs_prod.txt "
           "/tmp/fs_tar_list.txt /tmp/fs_expect.txt")
    tar_path.unlink(missing_ok=True)
    print(f"\n🎉 全量部署完成(耗时 {time.time() - t0:.0f}s, 备份 backend_fs_{stamp}.tar.gz)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
