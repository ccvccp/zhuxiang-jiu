#!/bin/bash
set -e
docker restart zhuxiang-backend-1
sleep 32
docker exec zhuxiang-backend-1 python -c "
import inspect
from services import asr_stream_service as m
assert hasattr(m, '_PACING_MAX_RATE') and '_pacing_wait' in inspect.getsource(
    m.AsrStreamSession.feed)
print('pacing_direct_path_live: True')"
docker logs zhuxiang-backend-1 --since 2m 2>&1 | grep "startup complete" | tail -1
