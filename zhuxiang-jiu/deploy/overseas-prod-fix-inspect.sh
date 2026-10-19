#!/bin/bash
echo '=== 容器内 _PROD_IMG_BASE 与 zxjiu 出现处 ==='
docker exec zhuxiang-backend-1 sh -c 'grep -n "_PROD_IMG_BASE\|zxjiu\.com\|zyjiu\.com" /app/repositories/product_repository.py | head -10'
echo '=== 便携两款商品的 images 定义 ==='
docker exec zhuxiang-backend-1 sh -c 'grep -n "zx42-100ml\|zx52-100ml" /app/repositories/product_repository.py | head -6'
echo '=== 持久化检查(store 落盘?) ==='
docker exec zhuxiang-backend-1 sh -c 'grep -rn "_mock_store\s*=" /app/repositories/*.py | head -3; grep -rn "save\|dump\|write" /app/repositories/backend.py | head -5'
