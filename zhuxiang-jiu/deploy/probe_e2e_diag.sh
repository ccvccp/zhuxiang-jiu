#!/bin/bash
JWT=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_a.txt)
echo "=== A'(43) episodes/me ==="
curl -s https://zxjiu.com/api/v1/dramas/drama_zhuxiang_01/episodes/me \
  -H "Authorization: Bearer $JWT" | head -c 700
echo ""
echo "=== B(42) episodes/me(对照) ==="
JWT_B=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_b.txt)
curl -s https://zxjiu.com/api/v1/dramas/drama_zhuxiang_01/episodes/me \
  -H "Authorization: Bearer $JWT_B" | python3 -c "import sys,json;d=json.load(sys.stdin)['data'];print('is_super:',d['is_super'],'eps:',[(e['episode_id'],e['status']) for e in d['episodes']])"
echo "=== unlock 表现存键 ==="
docker exec zhuxiang-redis-1 redis-cli --scan --pattern 'promo:drama76_unlocks:*' | head -5
echo "=== referrals/super/rewards 键 ==="
docker exec zhuxiang-redis-1 redis-cli --scan --pattern 'promo:drama76_*' | grep -v unlocks | head -8
