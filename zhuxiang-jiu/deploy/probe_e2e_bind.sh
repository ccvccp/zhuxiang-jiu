#!/bin/bash
JWT_B=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_b.txt)
echo "=== B(42) 重放 bind(幂等或新建) ==="
curl -s -X POST https://zxjiu.com/api/v1/growth/referrals/bind \
  -H "Authorization: Bearer $JWT_B" -H 'Content-Type: application/json' \
  -d '{"invite_code":"zxcf362cc6"}'
echo ""
echo "=== referral 键复查 ==="
docker exec zhuxiang-redis-1 redis-cli --scan --pattern '*drama76_referrals*'
echo "=== A'(43) invites/me 复查 ==="
JWT_A=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_a.txt)
curl -s https://zxjiu.com/api/v1/growth/invites/me -H "Authorization: Bearer $JWT_A" \
  | python3 -c "import sys,json;d=json.load(sys.stdin)['data'];print('valid:',d['valid_count'],'total:',d['total_invited'],'next:',(d['next_ladder'] or {}).get('threshold'));print('rewards:',[(r['threshold'],r['status']) for r in d['rewards']]);print('relations:',[(r['invitee_id'],r['status']) for r in d['relations']])"
echo "=== ul_43 解锁记录内容 ==="
docker exec zhuxiang-redis-1 redis-cli GET 'zhuxiang:promo:drama76_unlocks:ul_43_drama_zhuxiang_01'
