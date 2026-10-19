#!/bin/bash
echo '=== backend 容器 env (DB 相关) ==='
docker exec zhuxiang-backend-1 sh -c 'env | grep -iE "db|database|mysql|sqlite"' 2>/dev/null
echo '=== 容器内 .env / 配置 ==='
docker exec zhuxiang-backend-1 sh -c 'cat /app/.env 2>/dev/null | grep -iE "db|database|mysql" ; ls /app/*.db /app/data/*.db 2>/dev/null'
