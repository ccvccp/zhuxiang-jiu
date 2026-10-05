#!/bin/bash
# 生产闭环实测·账号预备 v2
set -e
echo "=== 注册 A'(13900009003) ==="
curl -s -X POST https://zxjiu.com/api/auth/register -H 'Content-Type: application/json' \
  -d '{"phone":"13900009003","password":"zxtest3","nickname":"闭环A2"}' | head -c 160
echo ""
echo "=== 登录 A' / B → token 落盘 ==="
TA=$(curl -s -X POST https://zxjiu.com/api/auth/login -H 'Content-Type: application/json' \
  -d '{"phone":"13900009003","password":"zxtest3"}')
TB=$(curl -s -X POST https://zxjiu.com/api/auth/login -H 'Content-Type: application/json' \
  -d '{"phone":"13900009002","password":"zxtest2"}')
JWT_A=$(echo $TA | python3 -c "import sys,json;print(json.load(sys.stdin)['accessToken'])")
JWT_B=$(echo $TB | python3 -c "import sys,json;print(json.load(sys.stdin)['accessToken'])")
echo "$JWT_A" > /opt/zhuxiang/deploy_tmp/dp4/jwt_a.txt
echo "$JWT_B" > /opt/zhuxiang/deploy_tmp/dp4/jwt_b.txt
echo "A' memberId: $(echo $TA | python3 -c "import sys,json;print(json.load(sys.stdin)['memberId'])")"
echo "B  memberId: $(echo $TB | python3 -c "import sys,json;print(json.load(sys.stdin)['memberId'])")"
echo "=== A' 邀请码 ==="
curl -s -X POST https://zxjiu.com/api/v1/growth/invite-code -H "Authorization: Bearer $JWT_A"
echo ""
echo "=== B 当前会员/解锁态(基线) ==="
curl -s https://zxjiu.com/api/v1/memberships/check -H "Authorization: Bearer $JWT_B" \
  -H "X-Member-Id: 42" -G -d drama_id=drama_zhuxiang_01 | head -c 200
echo ""
