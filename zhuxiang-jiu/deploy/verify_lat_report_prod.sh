#!/bin/bash
# 78号P2·H2 观察期上报链生产验收: login → POST /lat → GET /lat/stats
set -e
BASE=http://localhost:8000
TOKEN=$(curl -s -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000001","password":"test123456"}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("accessToken") or d.get("data",{}).get("accessToken") or "")')
H1="Authorization: Bearer $TOKEN"
H2="X-Member-Id: 1"
echo "--- 上报两条(正常轮 + s2>5s 异常样本) ---"
curl -s -X POST $BASE/api/xiaozhu/lat -H "$H1" -H "$H2" \
  -H 'Content-Type: application/json' \
  -d '{"s1":538,"s2":393,"s3":99,"tt":1030,"proto":"h2","mood":""}'
echo
curl -s -X POST $BASE/api/xiaozhu/lat -H "$H1" -H "$H2" \
  -H 'Content-Type: application/json' \
  -d '{"s1":586,"s2":21000,"s3":82,"tt":21668,"proto":"h2","mood":"care"}'
echo
echo "--- 聚合 /lat/stats?days=7 ---"
curl -s "$BASE/api/xiaozhu/lat/stats?days=7" -H "$H1" -H "$H2" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); t=d.get("total",{}); print("total:", json.dumps(t, ensure_ascii=False)[:400])'
