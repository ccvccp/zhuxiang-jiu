#!/bin/bash
set -e
docker cp /opt/zhuxiang/deploy_tmp/roles/sv73_script_service.py zhuxiang-backend-1:/app/services/
docker exec zhuxiang-backend-1 python -m py_compile /app/services/sv73_script_service.py && echo COMPILE_OK
docker restart zhuxiang-backend-1
sleep 32
cat > /tmp/chk_drama.py <<'EOF'
from services.sv73_script_service import lint_drama
dud = [{'role': 'drama_oral',
        'voiceover': '三年来非遗馆门可罗雀，可今天，终于火了！'}]
print('entertainment_caliber_ok:', lint_drama(dud) == [])
worst = [{'role': 'drama_oral', 'voiceover': '今天聊酒'}]
print('structure_gate_kept:', len(lint_drama(worst)) == 3)
EOF
docker cp /tmp/chk_drama.py zhuxiang-backend-1:/tmp/
docker exec -w /app zhuxiang-backend-1 python /tmp/chk_drama.py
