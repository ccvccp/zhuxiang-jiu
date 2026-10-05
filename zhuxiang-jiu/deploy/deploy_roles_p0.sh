#!/bin/bash
# 网站角色库 P0 部署 + 听测样音包
set -e
T=/opt/zhuxiang/deploy_tmp/roles
mkdir -p $T
echo "=== 1. 服务部署 + 重启(IP_VOICE 派生重算) ==="
docker cp $T/sv73_script_service.py zhuxiang-backend-1:/app/services/
docker exec zhuxiang-backend-1 python -m py_compile /app/services/sv73_script_service.py && echo COMPILE_OK
docker restart zhuxiang-backend-1
sleep 32
echo "=== 2. web 素材同步(12 图 → dist/images/ip/roles/) ==="
mkdir -p /var/www/zxjiu/dist/images/ip/roles
cp $T/role_*.jpg /var/www/zxjiu/dist/images/ip/roles/
ls /var/www/zxjiu/dist/images/ip/roles/ | wc -l
curl -s -o /dev/null -w "roles_web=%{http_code}\n" \
  https://zxjiu.com/images/ip/roles/role_laonian_m.jpg
echo "=== 3. 容器内人设注册验证 ==="
docker cp $T/spike_roles_voices.py zhuxiang-backend-1:/tmp/
docker exec zhuxiang-backend-1 python -c "
from services.sv73_script_service import IP_PERSONAS
roles = [k for k in IP_PERSONAS if k.startswith('role_')]
print('roles_registered:', len(roles), sorted(roles)[:3], '...')
print('tong_blocked:', not IP_PERSONAS['role_tong_f']['allow_alcohol'])"
echo "=== 4. 音色听测样音包(生产容器, 老年/童声系定版用) ==="
docker exec zhuxiang-backend-1 python /tmp/spike_roles_voices.py 2>&1 | tail -30
echo "=== 5. 样音包取回 ==="
rm -rf $T/voices && mkdir -p $T/voices
docker cp zhuxiang-backend-1:/tmp/roles_voices/. $T/voices/
ls -la $T/voices/ | head -20
