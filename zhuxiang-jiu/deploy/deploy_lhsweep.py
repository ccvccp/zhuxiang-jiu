"""localhost 扫除·生产部署: 仅覆盖生产 dist 已存在的同名 js"""
import subprocess

HOST = "root@47.236.61.117"
FILES = [
    "ai-governance-dashboard.js", "blogger-dashboard.js",
    "asset-dashboard.js", "invoice-dashboard.js",
    "alliance-dashboard.js", "api-dashboard.js",
    "xiaozhu-dashboard.js", "security-dashboard.js",
    "zyh-dashboard.js", "synapse-dashboard.js",
    "ride-dashboard.js", "pdm-dashboard.js",
    "zjian-dashboard.js", "chat-widget.js",
    "xiaozhu-widget.js", "ai-hub-widget.js",
    "auth.js",
]

list_cmd = "ls /var/www/zxjiu/dist/js/"
r = subprocess.run(["ssh", HOST, list_cmd],
                   capture_output=True, text=True, timeout=60)
remote = set((r.stdout or "").split())
deploy = [f for f in FILES if f in remote]
skipped = [f for f in FILES if f not in remote]
print("生产已存在(将更新):", len(deploy), deploy)
print("生产未部署(跳过, 随各模块后续部署):", skipped)

subprocess.run(["ssh", HOST,
                "mkdir -p /opt/zhuxiang/deploy_tmp/lhsweep"],
               capture_output=True, text=True, timeout=60)
for f in deploy:
    subprocess.run(
        ["scp", f"js/{f}",
         f"{HOST}:/opt/zhuxiang/deploy_tmp/lhsweep/"],
        capture_output=True, text=True, timeout=120)
    rc = subprocess.run(
        ["ssh", HOST,
         f"cp /opt/zhuxiang/deploy_tmp/lhsweep/{f}"
         f" /var/www/zxjiu/dist/js/{f}"],
        capture_output=True, text=True, timeout=60)
    print(("OK  " if rc.returncode == 0 else "FAIL") + " " + f)

# 验证: 生产 js 目录 localhost 硬默认计数
v = subprocess.run(
    ["ssh", HOST,
     "grep -l \\|\\| 'http://localhost:8000'"
     " /var/www/zxjiu/dist/js/*.js 2>/dev/null | wc -l"],
    capture_output=True, text=True, timeout=60)
print("生产残留 localhost 硬默认文件数:", v.stdout.strip())
