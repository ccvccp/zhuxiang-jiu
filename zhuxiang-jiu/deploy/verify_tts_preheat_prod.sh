#!/bin/bash
# 78号P1.5 生产验证: TTS 缓存命中(服务端预合成) vs 冷合成 耗时对比
set -e
BASE=http://localhost:8000
TOKEN=$(curl -s -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000001","password":"test123456"}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("accessToken") or d.get("data",{}).get("accessToken") or "")')
H1="Authorization: Bearer $TOKEN"
H2="X-Member-Id: 1"
echo "--- A. 命中(服务端预合成已写: 兜底轮首块) ---"
curl -s -o /tmp/hit.bin -w "预合成文本 HTTP=%{http_code} time=%{time_total}s size=%{size_download}\n" \
  "$BASE/api/xiaozhu/tts?text=%E8%BF%99%E4%B8%AA%E6%88%91%E8%BF%98%E5%9C%A8%E5%AD%A6%E7%9D%80%E5%91%A2%E2%80%94&voice=tongtong&speed=1" \
  -H "$H1" -H "$H2"
curl -s -o /tmp/hit2.bin -w "同文本二连(应同快) HTTP=%{http_code} time=%{time_total}s\n" \
  "$BASE/api/xiaozhu/tts?text=%E8%BF%99%E4%B8%AA%E6%88%91%E8%BF%98%E5%9C%A8%E5%AD%A6%E7%9D%80%E5%91%A2%E2%80%94&voice=tongtong&speed=1" \
  -H "$H1" -H "$H2"
echo "--- B. 未命中(随机冷文本, 走真实合成) ---"
curl -s -o /tmp/cold.bin -w "冷文本 HTTP=%{http_code} time=%{time_total}s size=%{size_download}\n" \
  "$BASE/api/xiaozhu/tts?text=%E9%AA%91%E9%A9%AC%E6%89%BE%E9%A9%B4%E9%AA%A1%E7%8C%B6%E9%9A%B9%E6%89%BE%E4%B8%AA%E7%99%BD%E8%8F%9C%E5%9C%86%E7%9C%BE%E8%84%9A&voice=tongtong&speed=1" \
  -H "$H1" -H "$H2"
echo "判读: A 显著快于 B(亚秒 vs 1-2s) → 预合成命中链生效"
