#!/bin/bash
set -e
AKID="${ALIYUN_AK_ID:?export ALIYUN_AK_ID first}"
SECRET="${ALIYUN_AK_SECRET:?export ALIYUN_AK_SECRET first}"
EP=https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/ecs8147
OBJ=/zxjiu-videos/deploy/ecs8147/key.pem
EXP=$(( $(date +%s) + 86400 ))
SIG=$(printf 'GET\n\n\n%s\n%s' "$EXP" "$OBJ" | openssl dgst -sha1 -hmac "$SECRET" -binary | base64)
ENC=$(python3 -c "import urllib.parse;print(urllib.parse.quote('$SIG'))")
URL="$EP/key.pem?OSSAccessKeyId=$AKID&Expires=$EXP&Signature=$ENC"

python3 - "$URL" << 'PYEOF'
import sys, re
url = sys.argv[1]
p = '/tmp/ecs_bundle/install-ecs.ps1'
s = open(p, encoding='utf-8').read()
s2 = re.sub(r"Invoke-WebRequest 'https://[^']+key\.pem[^']*'", "Invoke-WebRequest '%s'" % url, s)
assert s2 != s, 'placeholder not found'
open(p, 'w', encoding='utf-8').write(s2)
print('install-ecs.ps1 patched')
PYEOF

# 回传 OSS
DATE=$(date -u '+%a, %d %b %Y %H:%M:%S GMT')
PUTOBJ=/zxjiu-videos/deploy/ecs8147/install-ecs.ps1
PSIG=$(printf 'PUT\n\ntext/plain\n%s\n%s' "$DATE" "$PUTOBJ" | openssl dgst -sha1 -hmac "$SECRET" -binary | base64)
curl -s -X PUT -H "Date: $DATE" -H "Content-Type: text/plain" \
  -H "Authorization: OSS $AKID:$PSIG" --data-binary @/tmp/ecs_bundle/install-ecs.ps1 \
  "$EP/install-ecs.ps1"
echo "install re-uploaded"

# 验证 presigned
echo "presigned GET: $(curl -s -o /dev/null -w '%{http_code}' "$URL")"
