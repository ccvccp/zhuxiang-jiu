#!/bin/bash
echo "===ENV==="
docker exec zhuxiang-backend-1 env | grep -E 'XIAOZHU_JOYVOICE|GUARD_AUTO|DASHSCOPE_API_KEY' | sed 's/=sk-.*/=sk-***/'
echo "===LOG(2h asr/wake/guard/voice)==="
docker logs --since 2h zhuxiang-backend-1 2>&1 | grep -iE 'asr|wake|guard|joyvoice|voice|xiaozhu' | tail -30
echo "===WIDGET-REF==="
curl -s https://zxjiu.com/ | grep -o 'voice-wake-widget.js[^">]*' | head -3
echo "===WIDGET-HTTP==="
curl -s -o /dev/null -w '%{http_code}\n' "https://zxjiu.com/js/voice-wake-widget.js"
echo "===GUARD-STATUS==="
docker exec zhuxiang-backend-1 python3 -c "
import asyncio, json
from services.xiaozhu_mode_service import XiaozhuModeService
r = asyncio.run(XiaozhuModeService().current_status())
print(json.dumps(r, ensure_ascii=False)[:400])
" 2>&1 | tail -5
