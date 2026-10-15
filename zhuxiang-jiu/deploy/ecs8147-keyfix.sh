#!/bin/bash
set -e
AKID="${ALIYUN_AK_ID:?export ALIYUN_AK_ID first}"
SECRET="${ALIYUN_AK_SECRET:?export ALIYUN_AK_SECRET first}"
EP=https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com
OBJ=/zxjiu-videos/deploy/ecs8147/key.pem
F=/tmp/ecs_bundle/key.pem
DATE=$(date -u '+%a, %d %b %Y %H:%M:%S GMT')
SIG=$(printf 'PUT\n\ntext/plain\n%s\nx-oss-object-acl:private\n%s' "$DATE" "$OBJ" \
  | openssl dgst -sha1 -hmac "$SECRET" -binary | base64)
RESP=$(curl -s -X PUT -H "Date: $DATE" -H "Content-Type: text/plain" \
  -H "x-oss-object-acl: private" -H "Authorization: OSS $AKID:$SIG" \
  --data-binary "@$F" "$EP/deploy/ecs8147/key.pem")
echo "resp: ${RESP:-<empty=ok>}"
echo "anon HEAD: $(curl -s -o /dev/null -w '%{http_code}' -I "$EP/deploy/ecs8147/key.pem")"
echo "size: $(stat -c%s "$F")"
