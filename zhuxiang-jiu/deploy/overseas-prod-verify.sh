#!/bin/bash
echo '=== API 商品图字段 ==='
curl -s http://127.0.0.1:8000/api/product/list | python3 -c "
import sys, json
d = json.load(sys.stdin)
for p in (d.get('products') or []):
    print(p.get('product_id'), '->', str(p.get('image') or '')[:60])
" | head -13
echo '=== 容器状态 ==='
docker ps --filter name=zhuxiang-backend --format '{{.Status}}'
