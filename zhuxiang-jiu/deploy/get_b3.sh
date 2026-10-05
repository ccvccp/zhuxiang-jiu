#!/bin/bash
curl -s -X POST https://zxjiu.com/api/auth/login -H 'Content-Type: application/json' \
  -d '{"phone":"13900009002","password":"zxtest2"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['accessToken'])"
