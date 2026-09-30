"""二进制/目录 tar 分块上传(base64 over exec 通道)
用法: python adh_put.py <本地目录> <远端父目录>
tar.gz → base64 → 分块 printf >> 拼接 → base64 -d → tar 解包
(b64 字母表 A-Za-z0-9+/= 在单引号 printf 参数中安全)
"""
import base64
import io
import os
import sys
import tarfile

import paramiko

HOST = os.environ.get("ADH_HOST", "connect.weste.seetacloud.com")
PORT = int(os.environ.get("ADH_PORT", "37632"))
USER = os.environ.get("ADH_USER", "root")
PASSWORD = os.environ.get("ADH_PASSWORD", "dtFrMiCbH2qM")

CHUNK = 60000


def main() -> int:
    local_dir = sys.argv[1]
    remote_parent = sys.argv[2]
    # tar 打包(仅 .py, 排除 __pycache__/数据/测试产物)
    buf = io.BytesIO()
    count = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for root, dirs, files in os.walk(local_dir):
            dirs[:] = [d for d in dirs
                       if d not in ("__pycache__", ".git", "node_modules",
                                    "venv", ".pytest_cache")]
            for f in files:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(root, f)
                arc = os.path.relpath(p, local_dir).replace("\\", "/")
                tf.add(p, arcname=arc)
                count += 1
    payload = buf.getvalue()
    print(f"packed {count} py files, {len(payload)} bytes gz")
    b64 = base64.b64encode(payload).decode()

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD,
                look_for_keys=False, allow_agent=False)
    remote_b64 = "/tmp/_up.b64"
    _, so, _ = ssh.exec_command(f"rm -f {remote_b64}")
    so.channel.recv_exit_status()
    for i in range(0, len(b64), CHUNK):
        piece = b64[i:i + CHUNK]
        _, so, se = ssh.exec_command(
            f"printf '%s' '{piece}' >> {remote_b64}")
        so.channel.recv_exit_status()
    cmd = (f"mkdir -p '{remote_parent}' && "
           f"base64 -d {remote_b64} > /tmp/_up.tar.gz && "
           f"md5sum /tmp/_up.tar.gz | cut -d' ' -f1")
    _, so, se = ssh.exec_command(cmd, timeout=300)
    out = so.read().decode().strip()
    so.channel.recv_exit_status()
    import hashlib
    local_md5 = hashlib.md5(payload).hexdigest()
    print("md5 local :", local_md5)
    print("md5 remote:", out)
    if out != local_md5:
        ssh.close()
        print("PUT FAIL: tar md5 mismatch")
        return 1
    cmd2 = (f"tar -xzf /tmp/_up.tar.gz -C '{remote_parent}' && "
            f"find '{remote_parent}' -name '*.py' | wc -l")
    _, so, se = ssh.exec_command(cmd2, timeout=300)
    out2 = so.read().decode().strip()
    so.channel.recv_exit_status()
    print("remote py count:", out2)
    err = se.read().decode("utf-8", "replace")[:500]
    if err.strip():
        print("[stderr]", err)
    ssh.close()
    ok = out2.isdigit() and int(out2) >= count * 0.9
    print("PUT", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
