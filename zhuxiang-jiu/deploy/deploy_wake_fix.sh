#!/bin/bash
set -e
cp /opt/zhuxiang/deploy_tmp/asr_stream_service.py /opt/zhuxiang/zhuxiang-jiu/backend/services/
docker cp /opt/zhuxiang/deploy_tmp/asr_stream_service.py zhuxiang-backend-1:/app/services/
docker exec zhuxiang-backend-1 python -m py_compile /app/services/asr_stream_service.py && echo COMPILE_OK
docker restart zhuxiang-backend-1
sleep 32
docker exec zhuxiang-backend-1 python -c "
from services.asr_stream_service import AsrStreamSession
s = AsrStreamSession(lambda d: None)
assert hasattr(s, '_pending') and hasattr(s, '_flush_pending')
print('wake_buffer_fix_live: True')"
docker logs zhuxiang-backend-1 --since 2m 2>&1 | grep -E "startup complete|ERROR" | tail -2
