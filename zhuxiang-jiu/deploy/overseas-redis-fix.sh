#!/bin/bash
set -e
R="docker exec zhuxiang-redis-1 redis-cli"
mkdir -p /tmp/redis-img-bak-20261019

for pid in ZX42-2026B01 ZX52-2026B01; do
  key="zhuxiang:product:$pid"
  old=$($R hget "$key" images)
  echo "$old" > "/tmp/redis-img-bak-20261019/$pid.images.json"
  new=$(echo "$old" | sed 's|https://zxjiu.com||g; s|https://zyjiu.com||g')
  $R hset "$key" images "$new" > /dev/null
  echo "$pid 已更新:"
  $R hget "$key" images | head -c 200
  echo
done

echo '=== API 复核 ==='
sleep 2
curl -s http://127.0.0.1:8000/api/product/list | python3 -c "
import sys, json
d = json.load(sys.stdin)
for p in (d.get('products') or []):
    if p.get('product_id') in ('ZX42-2026B01', 'ZX52-2026B01'):
        print(p.get('product_id'), '->', p.get('image'))
"
