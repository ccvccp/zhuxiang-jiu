#!/bin/bash
# 78号生产端到端验收: login → /voices → /tts 双音色对比
set -e
BASE=http://localhost:8000
TOKEN=$(curl -s -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000001","password":"test123456"}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("accessToken") or d.get("data",{}).get("accessToken") or "")')
echo "TOKEN_LEN=${#TOKEN}"
echo "--- /api/xiaozhu/voices (JWT) ---"
curl -s $BASE/api/xiaozhu/voices -H "Authorization: Bearer $TOKEN" | head -c 500
echo
echo "--- /tts voice=chuichui vs 默认 (同文本尺寸应不同) ---"
curl -s -o /tmp/tts_chui.bin -w "chuichui HTTP=%{http_code} size=%{size_download} ct=%{content_type}\n" \
  "$BASE/api/xiaozhu/tts?text=%E6%82%A8%E5%A5%BD&voice=chuichui" \
  -H "Authorization: Bearer $TOKEN" -H 'X-Member-Id: 1'
curl -s -o /tmp/tts_def.bin -w "default  HTTP=%{http_code} size=%{size_download} ct=%{content_type}\n" \
  "$BASE/api/xiaozhu/tts?text=%E6%82%A8%E5%A5%BD" \
  -H "Authorization: Bearer $TOKEN" -H 'X-Member-Id: 1'
S1=$(stat -c%s /tmp/tts_chui.bin)
S2=$(stat -c%s /tmp/tts_def.bin)
if [ "$S1" != "$S2" ]; then echo "VOICE_DIFF_OK $S1 vs $S2"; else echo "VOICE_SAME_BAD $S1"; fi
