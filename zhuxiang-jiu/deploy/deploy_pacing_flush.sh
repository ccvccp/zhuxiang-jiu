#!/bin/bash
set -e
cp /opt/zhuxiang/deploy_tmp/asr_stream_service.py /opt/zhuxiang/zhuxiang-jiu/backend/services/
docker cp /opt/zhuxiang/deploy_tmp/asr_stream_service.py zhuxiang-backend-1:/app/services/
docker exec zhuxiang-backend-1 python -m py_compile /app/services/asr_stream_service.py && echo COMPILE_OK
docker restart zhuxiang-backend-1
sleep 32
docker exec zhuxiang-backend-1 python -c "
import inspect
from services import asr_stream_service as m
src = inspect.getsource(m._flush_pending if hasattr(m, '_flush_pending') else m.AsrStreamSession._flush_pending)
assert '_FLUSH_GAP' in src
print('pacing_flush_live: True')"
echo "=== 端到端复验(面板时序: arm 前积压 2s+正常段) ==="
docker exec -w /app zhuxiang-backend-1 python /tmp/e2e_panel_gap.py 2>&1 | grep -E "final=|RESULT|gap"
