#!/bin/bash
echo "=== Redis 持久化配置 ==="
docker exec zhuxiang-redis-1 redis-cli CONFIG GET appendonly | tail -1
docker exec zhuxiang-redis-1 redis-cli CONFIG GET save | tail -1
echo "=== drama76 全部键 ==="
docker exec zhuxiang-redis-1 redis-cli --scan --pattern 'promo:drama76_*'
echo "=== drama76_series 键 ==="
docker exec zhuxiang-redis-1 redis-cli --scan --pattern 'promo:drama76_series:*'
echo "=== redis 启动以来时长/重启历史 ==="
docker exec zhuxiang-redis-1 redis-cli INFO server | grep -E 'uptime_in_seconds|uptime_in_days'
echo "=== ImportError 上下文 ==="
docker logs zhuxiang-backend-1 --since 30m 2>&1 | grep -B3 "get_hit_logger" | head -12
echo "=== A'(43) 再取邀请码(确定性重算验证) ==="
JWT_A=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_a.txt)
curl -s -X POST https://zxjiu.com/api/v1/growth/invite-code -H "Authorization: Bearer $JWT_A"
echo ""
