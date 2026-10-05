#!/bin/bash
JWT=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_b2.txt)
echo "=== B purchase 下单 ==="
curl -s -X POST https://zxjiu.com/api/v1/memberships/purchase \
  -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' \
  -d '{"plan_id":"super_month"}' | head -c 500
echo ""
echo "=== 后端近 3 分钟异常 ==="
docker logs zhuxiang-backend-1 --since 3m 2>&1 | grep -iE "traceback|critical|purchase|dispatch" | tail -8
