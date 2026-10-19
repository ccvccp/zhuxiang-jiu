#!/bin/bash
echo '=== backend.py 头部 60 行 ==='
docker exec zhuxiang-backend-1 sh -c 'sed -n "1,60p" /app/repositories/backend.py'
echo '=== 持久化关键词 ==='
docker exec zhuxiang-backend-1 sh -c 'grep -rln "sqlite3\|json.dump\|redis" /app/repositories/*.py | head -5'
