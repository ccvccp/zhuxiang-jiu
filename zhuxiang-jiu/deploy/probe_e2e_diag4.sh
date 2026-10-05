#!/bin/bash
echo "=== 全模糊 drama76 键 ==="
docker exec zhuxiang-redis-1 redis-cli --scan --pattern '*drama76*' | head -20
echo "=== invites/unlock/referral 相关 ==="
docker exec zhuxiang-redis-1 redis-cli --scan --pattern '*invite*' | head -10
docker exec zhuxiang-redis-1 redis-cli --scan --pattern '*unlock*' | head -10
docker exec zhuxiang-redis-1 redis-cli --scan --pattern '*referral*' | head -10
echo "=== 键总数抽样(_k 分隔符侦察) ==="
docker exec zhuxiang-redis-1 redis-cli --scan --pattern 'promo:*' | head -8
