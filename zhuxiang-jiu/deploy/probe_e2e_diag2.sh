#!/bin/bash
JWT_B=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_b.txt)
echo "=== B(42) episodes/me 原始响应 ==="
curl -s -w "\nhttp=%{http_code} time=%{time_total}s\n" \
  https://zxjiu.com/api/v1/dramas/drama_zhuxiang_01/episodes/me \
  -H "Authorization: Bearer $JWT_B" | head -c 800
echo ""
echo "=== A'(43) invites/me(绑定后进度) ==="
JWT_A=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_a.txt)
curl -s https://zxjiu.com/api/v1/growth/invites/me -H "Authorization: Bearer $JWT_A" \
  | python3 -c "import sys,json;d=json.load(sys.stdin)['data'];print('valid:',d['valid_count'],'total:',d['total_invited']);print('rewards:',[(r['threshold'],r['reward_type'],r['status']) for r in d['rewards']]);print('relations:',[(r['invitee_id'],r['status']) for r in d['relations']])"
echo "=== A'(43) 解锁面 ==="
docker exec zhuxiang-redis-1 redis-cli --scan --pattern 'promo:drama76_unlocks:*'
echo "=== 后端近 5 分钟异常 ==="
docker logs zhuxiang-backend-1 --since 5m 2>&1 | grep -iE "error|exception|traceback" | grep -v "Application shutdown" | tail -8
