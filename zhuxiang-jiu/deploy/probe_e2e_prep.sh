#!/bin/bash
# 生产闭环实测·前置探测(注册可用性/支付渠道模式/账号预备)
set -e
echo "=== A. 支付渠道模式 ==="
grep -E '^PAY60_CHANNEL_MODE' /opt/zhuxiang/.env 2>/dev/null || echo "PAY60_CHANNEL_MODE 未设(默认 mock)"
echo "=== B. 注册接口探测(账号 A) ==="
curl -s -X POST https://zxjiu.com/api/auth/register -H 'Content-Type: application/json' \
  -d '{"phone":"13900009001","password":"zxtest1","nickname":"闭环A"}' | head -c 300
echo ""
echo "=== C. 注册接口探测(账号 B) ==="
curl -s -X POST https://zxjiu.com/api/auth/register -H 'Content-Type: application/json' \
  -d '{"phone":"13900009002","password":"zxtest2","nickname":"闭环B"}' | head -c 300
echo ""
echo "=== D. 登录拿 token(A/B) ==="
TA=$(curl -s -X POST https://zxjiu.com/api/auth/login -H 'Content-Type: application/json' \
  -d '{"phone":"13900009001","password":"zxtest1"}')
echo "A_login: $(echo $TA | head -c 260)"
TB=$(curl -s -X POST https://zxjiu.com/api/auth/login -H 'Content-Type: application/json' \
  -d '{"phone":"13900009002","password":"zxtest2"}')
echo "B_login: $(echo $TB | head -c 260)"
echo "$TA" > /opt/zhuxiang/deploy_tmp/dp4/token_a.json
echo "$TB" > /opt/zhuxiang/deploy_tmp/dp4/token_b.json
echo "=== E. 邀请码(A) ==="
JWT_A=$(echo $TA | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('data',{}).get('token') or d.get('token',''))")
curl -s -X POST https://zxjiu.com/api/v1/growth/invite-code -H "Authorization: Bearer $JWT_A" | head -c 200
echo ""
echo "$JWT_A" > /opt/zhuxiang/deploy_tmp/dp4/jwt_a.txt
