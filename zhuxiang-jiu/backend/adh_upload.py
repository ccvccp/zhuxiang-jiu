"""项目文件上传(exec+base64 通道, 规避 AutoDL SFTP 子系统限制)
用法: python adh_upload.py
通道实证: adh_run 的 exec_command 稳定可用; SFTP open 报 ENOENT
(疑 SFTP 子系统 chroot), 故走 echo <b64> | base64 -d > dst。
"""
import base64
import glob
import os
from pathlib import Path

import paramiko

HOST = os.environ.get("ADH_HOST", "connect.weste.seetacloud.com")
PORT = int(os.environ.get("ADH_PORT", "37632"))
USER = os.environ.get("ADH_USER", "root")
PASSWORD = os.environ.get("ADH_PASSWORD", "dtFrMiCbH2qM")

SRC = Path(__file__).resolve().parent
DST = "/root/autodl-tmp"

INCLUDE_FILES = [
    "build_dh_dev.py",
    "build_sv73_dev.py",
    "adh_run.py",
    "services/sv73_digital_human_service.py",
    "services/sv73_script_service.py",
    "services/sv73_render_service.py",
    "services/sv73_pipeline_service.py",
    "services/llm_client.py",
    "services/joyvoice_service.py",
    "services/promo_service.py",
    "repositories/promo_repository.py",
    "core/helpers.py",
]


def main() -> int:
    files = []
    for name in INCLUDE_FILES:
        p = SRC / name
        if p.exists():
            files.append(p)
        else:
            print("  [skip 不存在]", name)
    for pat in ("services/sv73_*.py",):
        for p in glob.glob(str(SRC / pat)):
            files.append(Path(p))
    files = sorted(set(files))
    print(f"upload {len(files)} files -> {DST}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD,
                look_for_keys=False, allow_agent=False)
    ok = 0
    try:
        for p in files:
            rel = p.relative_to(SRC).as_posix()
            dst = f"{DST}/{rel}"
            # 铁律: 远端路径一律纯字符串 rsplit(Windows Path 会产
            # 反斜杠, bash 里 mkdir/重定向全失败——本轮实证)
            dst_dir = dst.rsplit("/", 1)[0]
            b64 = base64.b64encode(p.read_bytes()).decode()
            cmd = (f"mkdir -p '{dst_dir}' && "
                   f"echo {b64} | base64 -d > '{dst}' && "
                   f"wc -c '{dst}'")
            _, so, se = ssh.exec_command(cmd, timeout=60)
            out = so.read().decode().strip()
            so.channel.recv_exit_status()
            # wc -c 输出格式 "<size> <path>"
            remote_size = (out.split() or [""])[0]
            size_ok = remote_size == str(p.stat().st_size)
            print(f"  {'ok' if size_ok else 'MISMATCH'}:", rel)
            ok += 1 if size_ok else 0
    finally:
        ssh.close()
    print(f"upload done: {ok}/{len(files)}")
    return 0 if ok == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
