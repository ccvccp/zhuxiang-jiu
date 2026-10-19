#!/bin/bash
echo '=== data 目录 ==='
docker exec zhuxiang-backend-1 sh -c 'ls -la /app/data | head -15'
echo '=== repositories 列表 ==='
docker exec zhuxiang-backend-1 sh -c 'ls /app/repositories | head -20'
echo '=== product repo 中 image 字段/表名 ==='
docker exec zhuxiang-backend-1 sh -c 'grep -rn "products" /app/repositories/product*.py 2>/dev/null | head -5; grep -rn "image" /app/repositories/product*.py 2>/dev/null | head -5'
