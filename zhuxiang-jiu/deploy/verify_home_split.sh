#!/bin/bash
# 首页 UA 分流验证: PC裸根→官网 / 移动→SPA / 带参数→SPA
echo "--- 1) PC UA 裸根(期望官网: 含 竹奕酒+同盟臻选) ---"
curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128" \
  https://zxjiu.com/ | grep -o "竹奕酒 · 全竹发酵\|id=\"app\"" | head -2
curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128" \
  https://zxjiu.com/ | grep -c allianceSection

echo "--- 2) iPhone UA(期望商城 SPA: id=app) ---"
curl -s -A "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/605" \
  https://zxjiu.com/ | grep -o "id=\"app\"" | head -1

echo "--- 3) PC UA + 引流参数(期望商城 SPA: 保 72号归因链) ---"
curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128" \
  "https://zxjiu.com/?clickId=ZXBJ-TEST&v72" | grep -o "id=\"app\"" | head -1

echo "--- 4) 深链回归(期望 SPA 回退) ---"
curl -s -o /dev/null -w "deep /pages/products: %{http_code}\n" \
  -A "Mozilla/5.0 (Windows NT 10.0) Chrome/128" \
  "https://zxjiu.com/pages/products/index"
curl -s -o /dev/null -w "official.html 显式: %{http_code}\n" https://zxjiu.com/official.html
