#!/bin/bash
# DP2b 支付回调发放·部署+验收
set -e
T=/opt/zhuxiang/deploy_tmp/dp2b
echo "=== 1. 源码同步 ==="
cp $T/drama76_series_service.py /opt/zhuxiang/zhuxiang-jiu/backend/services/
cp $T/payment_service.py /opt/zhuxiang/zhuxiang-jiu/backend/services/
cp $T/ai_scoring_service.py /opt/zhuxiang/zhuxiang-jiu/backend/services/
cp $T/drama76_routes.py /opt/zhuxiang/zhuxiang-jiu/backend/routes/
echo "=== 2. 容器落位 + 编译断言 ==="
docker cp $T/drama76_series_service.py zhuxiang-backend-1:/app/services/
docker cp $T/payment_service.py zhuxiang-backend-1:/app/services/
docker cp $T/ai_scoring_service.py zhuxiang-backend-1:/app/services/
docker cp $T/drama76_routes.py zhuxiang-backend-1:/app/routes/
docker exec zhuxiang-backend-1 python -m py_compile \
  /app/services/drama76_series_service.py /app/services/payment_service.py \
  /app/services/ai_scoring_service.py /app/routes/drama76_routes.py && echo COMPILE_OK
echo "=== 3. 前端 + 重启 ==="
cp $T/drama.html /var/www/zxjiu/dist/drama.html
docker restart zhuxiang-backend-1
sleep 10
echo "=== A. purchase 端点守卫(无 token 应 401) ==="
curl -s -o /dev/null -w "purchase_noauth=%{http_code}\n" -X POST \
  -H 'Content-Type: application/json' -d '{"plan_id":"super_forever"}' \
  https://zxjiu.com/api/v1/memberships/purchase
echo "=== B. 前端接线 ==="
curl -s -o /dev/null -w "drama_html=%{http_code}\n" https://zxjiu.com/drama.html
echo "purchase_elements=$(curl -s https://zxjiu.com/drama.html | grep -c -E 'superBox|memberships/purchase|planForever')"
echo "=== C. 容器导入与分发分支 ==="
docker exec zhuxiang-backend-1 python -c "
from services.payment_service import SUPPORTED_SCENES
from services.drama76_series_service import DramaSeriesService
from services.ai_scoring_service import _SCENE_TYPE_FIT
assert 'drama_super' in SUPPORTED_SCENES, 'scene missing'
assert 'drama_super' in _SCENE_TYPE_FIT, 'routing matrix missing'
svc = DramaSeriesService()
assert svc._plan_of('super_forever')['price'] == 199.0
assert svc._plan_of('super_month')['duration_days'] == 30
print('dp2b_import_ok scenes+matrix+plans')"
echo "=== D. 启动日志 ==="
docker logs zhuxiang-backend-1 --since 2m 2>&1 | grep -E "Started|ERROR" | tail -3
