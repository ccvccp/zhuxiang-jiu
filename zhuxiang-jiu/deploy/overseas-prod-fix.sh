#!/bin/bash
set -e
echo '=== [1/4] 改容器内代码为相对路径 ==='
docker exec zhuxiang-backend-1 sh -c "sed -i 's|_PROD_IMG_BASE = \"https://zyjiu.com/images/products\"|_PROD_IMG_BASE = \"/images/products\"|' /app/repositories/product_repository.py"
docker exec zhuxiang-backend-1 grep -n '_PROD_IMG_BASE =' /app/repositories/product_repository.py

echo '=== [2/4] 备份并重启容器 ==='
docker exec zhuxiang-backend-1 cp /app/repositories/product_repository.py /tmp/product_repository.py.bak 2>/dev/null || true
docker restart zhuxiang-backend-1
sleep 6

echo '=== [3/4] API 验证 ==='
sleep 4
curl -s http://127.0.0.1:8000/api/product/list | python3 -c "
import sys, json
d = json.load(sys.stdin)
for p in (d.get('products') or [])[:12]:
    img = p.get('image') or (p.get('images') or {}).get('main', '')
    if 'zx4' in p.get('product_id','').lower() or 'zx5' in p.get('product_id','').lower() or 'zx42' in str(p.get('product_id','')):
        pass
    print(p.get('product_id'), '->', img[:70])
" 2>/dev/null | head -14

echo '=== [4/4] 容器状态 ==='
docker ps --filter name=zhuxiang-backend --format '{{.Names}} {{.Status}}'
