#!/bin/bash
# 海外 Redis 导出同盟目录数据(商家/商品/分类)
set -e
R="docker exec zhuxiang-redis-1 redis-cli"
OUT=/tmp/alliance-export.json
python3 - "$OUT" <<'PYEOF'
import subprocess, json, sys
R = ["docker", "exec", "zhuxiang-redis-1", "redis-cli"]
tables = ["alliance_merchants", "alliance_products", "alliance_categories"]
out = {}
for t in tables:
    keys = subprocess.run(R + ["keys", f"zhuxiang:alliance:{t}:*"], capture_output=True, text=True).stdout.split()
    out[t] = {}
    for k in keys:
        if k.endswith(":seq"):
            continue
        v = subprocess.run(R + ["get", k], capture_output=True, text=True).stdout.strip()
        if v:
            out[t][k] = v
json.dump(out, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
print("exported:", {t: len(v) for t, v in out.items()})
PYEOF
wc -c "$OUT"
