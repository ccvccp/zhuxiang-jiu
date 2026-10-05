#!/bin/bash
# DP4+ 技能化·部署+验收
set -e
cp /opt/zhuxiang/deploy_tmp/dp4/drama76_series_service.py /opt/zhuxiang/zhuxiang-jiu/backend/services/
docker cp /opt/zhuxiang/deploy_tmp/dp4/drama76_series_service.py zhuxiang-backend-1:/app/services/
docker exec zhuxiang-backend-1 python -m py_compile /app/services/drama76_series_service.py && echo COMPILE_OK
docker restart zhuxiang-backend-1
sleep 35
echo "=== A. 触发排序(checkpoint) → 自动注册技能 ==="
curl -s "https://zxjiu.com/api/v1/dramas/drama_zhuxiang_01/episodes/70/checkpoint" \
  | python3 -c "import sys,json;d=json.load(sys.stdin)['data'];print('dp4:',d['dp4']);print('order:',[m['type'] for m in d['unlock_methods']])"
echo "=== B. 技能注册与影子(容器内) ==="
docker exec zhuxiang-backend-1 python -c "
from services.agent75_skill_service import SkillRuntime
from services.drama76_series_service import DramaSeriesService
rt = SkillRuntime()
s = rt.get(DramaSeriesService.DP4_SKILL_ID)
print('skill:', s.skill_id if s else None, '| status:', s.status.value if s else None,
      '| source:', s.source if s else None)
print('shadow_runs:', s.metrics['shadow_runs'] if s else 0)"
echo "=== C. 排序正常性(内置序不受 testing 影响) ==="
curl -s "https://zxjiu.com/api/v1/dramas/drama_zhuxiang_01/episodes/70/checkpoint" \
  | python3 -c "import sys,json;d=json.load(sys.stdin)['data'];print('order2:',[m['type'] for m in d['unlock_methods']])"
