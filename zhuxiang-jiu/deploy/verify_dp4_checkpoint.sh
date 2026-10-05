#!/bin/bash
# DP4 共鸣驱动卡点动态排序·部署+验收
set -e
T=/opt/zhuxiang/deploy_tmp/dp4
echo "=== 1. 源码同步 ==="
cp $T/agent75_resonance_service.py /opt/zhuxiang/zhuxiang-jiu/backend/services/
cp $T/drama76_series_service.py /opt/zhuxiang/zhuxiang-jiu/backend/services/
cp $T/drama76_routes.py /opt/zhuxiang/zhuxiang-jiu/backend/routes/
echo "=== 2. 容器落位 + 编译断言 ==="
docker cp $T/agent75_resonance_service.py zhuxiang-backend-1:/app/services/
docker cp $T/drama76_series_service.py zhuxiang-backend-1:/app/services/
docker cp $T/drama76_routes.py zhuxiang-backend-1:/app/routes/
docker exec zhuxiang-backend-1 python -m py_compile \
  /app/services/agent75_resonance_service.py \
  /app/services/drama76_series_service.py \
  /app/routes/drama76_routes.py && echo COMPILE_OK
echo "=== 3. 前端 + 重启 ==="
cp $T/drama.html /var/www/zxjiu/dist/drama.html
docker restart zhuxiang-backend-1
sleep 35
echo "=== A. checkpoint DP4 元数据(默认门控下回退固定序) ==="
curl -s "https://zxjiu.com/api/v1/dramas/drama_zhuxiang_01/episodes/70/checkpoint" | python3 -c "import sys,json;d=json.load(sys.stdin)['data'];print('dp4:',d['dp4']);print('order:',[m['type'] for m in d['unlock_methods']])"
echo "=== B. 带 session_id 端点可达 ==="
curl -s -o /dev/null -w "checkpoint_sid=%{http_code}\n" "https://zxjiu.com/api/v1/dramas/drama_zhuxiang_01/episodes/70/checkpoint?session_id=s_probe"
echo "=== C. 前端接线 ==="
curl -s -o /dev/null -w "drama_html=%{http_code}\n" https://zxjiu.com/drama.html
echo "dp4_elements=$(curl -s https://zxjiu.com/drama.html | grep -c -E 'reorderLockLayer|checkpoint\?session_id')"
echo "=== D. 容器导入 + 共鸣会话基数(门控进度) ==="
docker exec zhuxiang-backend-1 python -c "
from services.agent75_resonance_service import ResonanceService
from services.drama76_series_service import DramaSeriesService
n = ResonanceService().count_sessions()
g = DramaSeriesService.DP4_GATE_SESSIONS
print(f'dp4_import_ok sessions={n} gate={g} dynamic={\"ON\" if n>=g else \"OFF(gate)\"}')"
echo "=== E. 启动日志 ==="
docker logs zhuxiang-backend-1 --since 2m 2>&1 | grep -E "startup complete|ERROR" | tail -3
