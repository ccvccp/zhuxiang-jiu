#!/bin/bash
# auth 修复部署 + episodes/me 复测
set -e
cp /opt/zhuxiang/deploy_tmp/dp4/auth_middleware.py /opt/zhuxiang/zhuxiang-jiu/backend/core/
docker cp /opt/zhuxiang/deploy_tmp/dp4/auth_middleware.py zhuxiang-backend-1:/app/core/auth_middleware.py
docker exec zhuxiang-backend-1 python -m py_compile /app/core/auth_middleware.py && echo COMPILE_OK
cp /opt/zhuxiang/deploy_tmp/dp4/drama.html /var/www/zxjiu/dist/drama.html
docker restart zhuxiang-backend-1
sleep 35
JWT=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_a.txt)
echo "=== A'(43) episodes/me 带 Bearer ==="
curl -s https://zxjiu.com/api/v1/dramas/drama_zhuxiang_01/episodes/me \
  -H "Authorization: Bearer $JWT" | python3 -c "import sys,json;d=json.load(sys.stdin)['data'];print('is_super:',d['is_super']);print('eps:',[(e['episode_id'],e['status']) for e in d['episodes']])"
echo "=== 游客无 token(应仍可浏览公开面) ==="
curl -s -o /dev/null -w "guest_dramas=%{http_code}\n" https://zxjiu.com/api/v1/dramas
echo "=== 游客过期 token(应按游客放行不 401) ==="
curl -s -o /dev/null -w "expired_token=%{http_code}\n" -H "Authorization: Bearer expired.invalid.token" https://zxjiu.com/api/v1/dramas
