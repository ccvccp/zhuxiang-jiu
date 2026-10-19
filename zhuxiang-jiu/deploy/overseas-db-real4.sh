#!/bin/bash
echo '=== product_repository 140-200 行 ==='
docker exec zhuxiang-backend-1 sh -c 'sed -n "140,200p" /app/repositories/product_repository.py'
echo '=== backend.py 持久化方式 ==='
docker exec zhuxiang-backend-1 sh -c 'grep -n "sqlite\|\.db\|json\|persist\|PATH" /app/repositories/backend.py | head -10'
