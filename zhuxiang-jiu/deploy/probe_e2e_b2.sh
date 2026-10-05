#!/bin/bash
echo "=== B 重新登录(新 token) ==="
R=$(curl -s -X POST https://zxjiu.com/api/auth/login -H 'Content-Type: application/json' \
  -d '{"phone":"13900009002","password":"zxtest2"}')
echo "$R" | head -c 200
echo ""
echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin)['accessToken'])" > /opt/zhuxiang/deploy_tmp/dp4/jwt_b2.txt
echo "=== 新 token 立即验证 episodes/me ==="
JWT=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_b2.txt)
curl -s -w "\nhttp=%{http_code}\n" https://zxjiu.com/api/v1/dramas/drama_zhuxiang_01/episodes/me \
  -H "Authorization: Bearer $JWT" | tail -2
echo "=== 旧 token 再验(对照——是否失效) ==="
JWT_OLD=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_b.txt)
curl -s -o /dev/null -w "old_token=%{http_code}\n" https://zxjiu.com/api/v1/dramas/drama_zhuxiang_01/episodes/me \
  -H "Authorization: Bearer $JWT_OLD"
