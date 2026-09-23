#!/bin/bash
# 78号P1 生产验收: 本站流式 TTS 端点 SSE 首块延迟
# B: 后端直连(localhost:8000)  C: 经 nginx 域名(缓冲生死门)
set -e
BASE=http://localhost:8000
TOKEN=$(curl -s -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000001","password":"test123456"}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("accessToken") or d.get("data",{}).get("accessToken") or "")')
echo "--- B. 本站流式端点(后端直连, 13字) ---"
python3 - "$TOKEN" <<'PYEOF'
import http.client, sys, time, urllib.parse
token = sys.argv[1]
qs = urllib.parse.urlencode({"text": "好的，我为您推荐竹香经典。", "speed": "1", "voice": "tongtong"})
conn = http.client.HTTPConnection("localhost:8000", timeout=30)
t0 = time.monotonic()
conn.request("GET", "/api/xiaozhu/tts/stream?" + qs,
             headers={"Authorization": "Bearer " + token,
                      "X-Member-Id": "1"})
r = conn.getresponse()
print("HTTP", r.status, "ct:", r.getheader("Content-Type"))
n_chunk = 0
while True:
    line = r.readline()
    if not line:
        break
    if line.startswith(b"data: ") and b"content" in line:
        n_chunk += 1
        if n_chunk <= 4:
            print(f"  audio#{n_chunk} at {round((time.monotonic()-t0)*1000)}ms len={len(line)}")
print("total audio chunks:", n_chunk, "done at", round((time.monotonic()-t0)*1000), "ms")
PYEOF
echo "--- C. 经 nginx 域名 SSE(38字长句——缓冲生死门) ---"
python3 - "$TOKEN" <<'PYEOF'
import http.client, ssl, sys, time, urllib.parse
token = sys.argv[1]
qs = urllib.parse.urlencode({"text": "好的——我为您推荐竹奕·竹香经典 52度 500ml，您看这款怎么样？", "speed": "1", "voice": "tongtong"})
ctx = ssl.create_default_context()
conn = http.client.HTTPSConnection("zxjiu.com", 443, timeout=30, context=ctx)
t0 = time.monotonic()
conn.request("GET", "/api/xiaozhu/tts/stream?" + qs,
             headers={"Authorization": "Bearer " + token,
                      "X-Member-Id": "1"})
r = conn.getresponse()
print("HTTP", r.status, "ct:", r.getheader("Content-Type"))
n = 0
while True:
    line = r.readline()
    if not line:
        break
    if line.startswith(b"data: ") and b"content" in line:
        n += 1
        print(f"  audio#{n} at {round((time.monotonic()-t0)*1000)}ms len={len(line)}")
print("chunks:", n, "done at", round((time.monotonic()-t0)*1000), "ms")
PYEOF
