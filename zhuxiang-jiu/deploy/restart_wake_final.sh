#!/bin/bash
set -e
docker restart zhuxiang-backend-1
sleep 32
docker exec zhuxiang-backend-1 python -c "
import inspect
from services import asr_stream_service as m
src = inspect.getsource(m.AsrStreamSession._read_loop)
assert 'while self._pending' in src, 'final fix not live'
print('final_fix_live: True')"
docker logs zhuxiang-backend-1 --since 2m 2>&1 | grep "startup complete" | tail -1
