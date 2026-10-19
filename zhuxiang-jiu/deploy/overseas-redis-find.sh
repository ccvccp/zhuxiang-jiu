#!/bin/bash
echo '=== product 相关 key ==='
docker exec zhuxiang-redis-1 redis-cli --scan --pattern 'zhuxiang:*product*' | head -20
echo '=== ZX42-2026B01 数据(截断) ==='
k=$(docker exec zhuxiang-redis-1 redis-cli --scan --pattern '*ZX42-2026B01*' | head -1)
echo "key=$k type=$(docker exec zhuxiang-redis-1 redis-cli type "$k")"
docker exec zhuxiang-redis-1 redis-cli hgetall "$k" 2>/dev/null | grep -iE 'image|^https' | head -8
