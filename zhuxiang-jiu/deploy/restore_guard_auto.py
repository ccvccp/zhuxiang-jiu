"""护栏自动恢复(09-28 09:00 crontab, 幂等+安全检查)

背景: 2026-09-24 临时 XXIAOZHU_GUARD_AUTO=off(滑窗
数据老化期)。09-20 失败轮于 09-28 00:13(CST)全部
出窗, 指标自愈 ~0.073。本脚本在 09-28/09-29 09:00
运行: 窗口化 asrFailRate < 0.103(基线0.10×恶化线
1.03)才置 on——防"恢复即再暂停"(暂停为粘性需人工
resume); 未达标留 off 待次日重试(留痕)。

用法:
    python3 restore_guard_auto.py --dry-run  # 只看指标
    python3 restore_guard_auto.py --install  # 装 crontab
    python3 restore_guard_auto.py            # 恢复动作
"""
import json
import subprocess
import sys

ENV_FILE = "/opt/zhuxiang/.env"
THRESHOLD = 0.103
CRON_LINE = ("0 9 28-29 9 * python3 "
             "/opt/zhuxiang/restore_guard_auto.py "
             ">> /opt/zhuxiang/restore_guard.log "
             "2>&1 # guard-restore(窗口达标才on)")


def sh(cmd: str):
    return subprocess.run(
        cmd, shell=True, capture_output=True,
        text=True, timeout=300)


def current_auto() -> str:
    for ln in open(ENV_FILE, encoding="utf-8"):
        if ln.startswith("XXIAOZHU_GUARD_AUTO="):
            return ln.strip().split("=", 1)[1]
    return "(unset)"


def window_rate() -> dict:
    r = sh("docker cp /opt/zhuxiang/window_rate.py "
           "zhuxiang-backend-1:/tmp/ >/dev/null && "
           "docker exec zhuxiang-backend-1 "
           "python /tmp/window_rate.py")
    try:
        return json.loads(
            r.stdout.strip().splitlines()[-1])
    except Exception:
        print("rate-parse-fail",
              r.stdout[:150], r.stderr[:150])
        sys.exit(1)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    if mode == "--install":
        cur = sh("crontab -l").stdout
        if "restore_guard_auto" in cur:
            print("cron-already-installed")
            return
        line = ("(crontab -l; echo '"
                + CRON_LINE + "') | crontab -")
        r = sh(line)
        print("cron-installed" if r.returncode == 0
              else f"install-fail {r.stderr[:150]}")
        return

    auto = current_auto()
    d = window_rate()
    print(f"GUARD_AUTO={auto} windowed={d}")

    if mode == "--dry-run":
        ok = d["rate"] < THRESHOLD
        print(("would-restore" if ok
               else "would-skip")
              + f" (阈值 {THRESHOLD})")
        return

    if auto == "on":
        print("already-on")
        return
    if d["rate"] >= THRESHOLD:
        print(f"SKIP: rate {d['rate']} >= "
              f"{THRESHOLD} 留 off 待下日重试")
        return

    r = sh("sed -i 's/^XXIAOZHU_GUARD_AUTO=.*/"
           "XXIAOZHU_GUARD_AUTO=on/' " + ENV_FILE
           + " && cd /opt/zhuxiang && "
             "docker compose up -d backend "
             "2>&1 | tail -1")
    print("restore:", r.stdout.strip())
    v = sh("sleep 8; docker exec "
           "zhuxiang-backend-1 env "
           "| grep XXIAOZHU_GUARD_AUTO")
    print("verify:", v.stdout.strip())


main()
