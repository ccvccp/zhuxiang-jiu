"""查小竹护栏当前状态(服务器侧)"""
import json
import subprocess

raw = subprocess.run(
    ["docker", "exec", "zhuxiang-redis-1", "redis-cli",
     "get", "zhuxiang:xiaozhu:mode_state"],
    capture_output=True, text=True, timeout=30).stdout
d = json.loads(raw)
ms = d.get("metrics") or []
print("paused =", d.get("paused"))
print("reason =",
      (d.get("pausedReason") or "-")[:80])
if ms:
    last = ms[-1]
    print("lastCheck =",
          last.get("checkedAt", "")[:19],
          "asrFailRate =",
          (last.get("metrics") or {})
          .get("asrFailRate"))
