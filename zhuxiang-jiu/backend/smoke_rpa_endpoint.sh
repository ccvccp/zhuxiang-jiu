#!/bin/bash
# RPA pending 端点冒烟(admin Bearer)
set -e
TOKEN=$(curl -s -X POST https://zxjiu.com/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000002","password":"admin123456"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("accessToken",""))')
echo "token_len: ${#TOKEN}"
curl -s -H "Authorization: Bearer $TOKEN" -H 'X-Role: admin' \
  https://zxjiu.com/api/promo/rpa/pending
echo
