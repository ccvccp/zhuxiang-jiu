#!/bin/bash
# compose 追加 XIAOZHU_WS_DUMP 开关(诊断期可临时 on)
set -e
F=/opt/zhuxiang/docker-compose.yml
if grep -q "XIAOZHU_WS_DUMP" "$F"; then
    echo "already-present"
else
    python3 - <<'PYEOF'
p = "/opt/zhuxiang/docker-compose.yml"
s = open(p, encoding="utf-8").read()
anchor = "      - XIAOZHU_PROACTIVE_MODE=${XIAOZHU_PROACTIVE_MODE:-off}"
add = (anchor + "\n"
       "      # WS 推流音频落盘(诊断期临时 on: X5 间歇坏流分析, 常态 off 零开销)\n"
       "      - XIAOZHU_WS_DUMP=${XIAOZHU_WS_DUMP:-off}")
assert anchor in s, "anchor not found"
open(p, "w", encoding="utf-8").write(s.replace(anchor, add, 1))
print("inserted")
PYEOF
fi
grep -n "XIAOZHU_WS_DUMP" "$F"
