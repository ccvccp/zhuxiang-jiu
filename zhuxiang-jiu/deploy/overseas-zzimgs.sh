#!/bin/bash
# 10 款商品 Redis 图片字段本地化(/images/products/zz-*.jpg)
set -e
R="docker exec zhuxiang-redis-1 redis-cli"
mkdir -p /tmp/zzimg-bak-20261019

declare -A MAP=(
  [ZX42-2026L07]=zz-classic42
  [ZX45-2026L05]=zz-classic45
  [ZX52-2026X01]=zz-premium52
  [ZX53-2026Z01]=zz-reserve53
  [ZX52-2026X02]=zz-premium52-750
  [ZX53-2026N10]=zz-age10
  [ZX52-2026L02]=zz-giftbox
  [ZX53-2026N20]=zz-age20
  [ZX52-2026X03]=zz-premium-gift
  [ZX50-2026D01]=zz-collection50
)

for pid in "${!MAP[@]}"; do
  key="zhuxiang:product:$pid"
  $R hget "$key" images > "/tmp/zzimg-bak-20261019/$pid.images.json"
  $R hset "$key" images "{\"main\":\"/images/products/${MAP[$pid]}.jpg\",\"gallery\":[]}" > /dev/null
  echo "$pid -> $($R hget "$key" images | head -c 80)"
done

echo '=== API 复核(期望 12 全本地) ==='
sleep 2
curl -s http://127.0.0.1:8000/api/product/list | python3 -c "
import sys, json
d = json.load(sys.stdin)
ext = 0; loc = 0
for p in (d.get('products') or []):
    img = (p.get('images') or {}).get('main') or p.get('image') or ''
    tag = 'LOCAL' if img.startswith('/images/') else 'EXT:' + img[:60]
    if img.startswith('/images/'): loc += 1
    else: ext += 1
    print(p.get('product_id'), tag)
print('local:', loc, 'ext:', ext)
"
