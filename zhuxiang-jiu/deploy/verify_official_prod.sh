#!/bin/bash
# 官网过渡部署(official.html 批次)生产验证
echo "--- 核心页状态码 ---"
for p in official.html products.html news.html service.html cart.html \
         checkout.html login.html news-detail.html product-detail.html \
         ai-alliance-dashboard.html; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "https://zxjiu.com/$p")
    echo "$p => $code"
done
echo "--- JS 资产 ---"
for j in js/main.js js/alliance-dashboard.js js/chat-widget.js; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "https://zxjiu.com/$j")
    echo "$j => $code"
done
echo "--- API 商户名联查 ---"
curl -s "https://zxjiu.com/api/alliance/products?status=active" | python3 -c "
import json, sys
b = json.load(sys.stdin)
print('count:', b.get('count'))
for p in b['data'][:8]:
    print(p['productId'], p['category'], p['name'][:20],
          '| 商户:', p.get('merchantName', '<缺失>'),
          '| 溯源:', p.get('trace', {}).get('traceVerified'))
"
echo "--- main.js 导航 official 化 ---"
curl -s "https://zxjiu.com/js/main.js?v=20261002" | grep -c "official.html"
