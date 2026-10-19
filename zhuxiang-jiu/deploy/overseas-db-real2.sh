#!/bin/bash
echo '=== 容器 /app 结构 ==='
docker exec zhuxiang-backend-1 sh -c 'ls /app | head; ls /app/backend 2>/dev/null | head'
echo '=== 定位 db.py 并打印 DB_URL ==='
docker exec zhuxiang-backend-1 sh -c 'cd /app/backend 2>/dev/null || cd /app; python -c "import db; print(db.DB_URL)"' 2>&1 | tail -1
