"""AutoDL 实例 SSH 执行器(数字人 GPU 轨部署配套)
用法: python adh_run.py "<远端命令>"
连接信息走 ADH_* env 或默认(用户实例 connect.weste.seetacloud.com:37632)
"""
import os
import sys

import paramiko

HOST = os.environ.get("ADH_HOST", "connect.weste.seetacloud.com")
PORT = int(os.environ.get("ADH_PORT", "37632"))
USER = os.environ.get("ADH_USER", "root")
PASSWORD = os.environ.get("ADH_PASSWORD", "dtFrMiCbH2qM")


def run(cmd: str, timeout: int = 300) -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=PORT, username=USER,
                   password=PASSWORD, timeout=30,
                   look_for_keys=False, allow_agent=False)
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        if out:
            print(out)
        if err.strip():
            print("[stderr]", err[-2000:], file=sys.stderr)
        return code
    finally:
        client.close()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "whoami && hostname"
    if arg == "-f":
        # 从文件读命令(规避 PowerShell 引号转义地狱)
        with open(sys.argv[2], encoding="utf-8") as f:
            arg = f.read()
    sys.exit(run(arg))
