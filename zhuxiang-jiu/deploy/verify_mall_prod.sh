#!/bin/bash
# 同盟商城 C 端上线验证
echo "--- shops API(公开, 商户目录) ---"
curl -s https://zxjiu.com/api/alliance/shops | python3 -c "
import json, sys
b = json.load(sys.stdin)
print('count:', b.get('count'))
for s in (b.get('data') or []):
    print(' ', s['merchantId'], s['shopName'], s['category'],
          '★' + str(s['ratingAvg']), '在售', s['productCount'])
"
echo "--- mall 页面 ---"
curl -s -o /dev/null -w "alliance-mall.html: %{http_code}\n" https://zxjiu.com/alliance-mall.html
echo "--- 店内商品(merchantId 取 shops 首家) ---"
MID=$(curl -s https://zxjiu.com/api/alliance/shops | python3 -c "
import json, sys
b = json.load(sys.stdin)
d = b.get('data') or []
print(d[0]['merchantId'] if d else '')")
if [ -n "$MID" ]; then
  curl -s "https://zxjiu.com/api/alliance/products?status=active&merchantId=$MID" | python3 -c "
import json, sys
b = json.load(sys.stdin)
print('merchant', '$MID', '商品数:', b.get('count'))
for p in (b.get('data') or [])[:3]:
    print(' ', p['name'][:24], '¥' + str(p['price']))
"
fi
echo "--- 首页区块改接 ---"
curl -s https://zxjiu.com/official.html | grep -c "alliance-mall.html"
