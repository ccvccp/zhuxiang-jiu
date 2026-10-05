#!/bin/bash
# 商城 H5 入口定位 + nginx 配置
echo "=== A. 官网 index 里的商城跳转 ==="
grep -oE '(href|location)[^,;]{0,80}(mall|shop|app)' /var/www/zxjiu/dist/index.html | head -6
grep -oE 'goToMall[^;]{0,60}' /var/www/zxjiu/dist/index.html /var/www/zxjiu/dist/js/main.js 2>/dev/null | head -4
echo ""
echo "=== B. Taro 构建产物特征(app 容器/chunk) ==="
ls /var/www/zxjiu/dist/js/ | grep -E "^app|^chunk|^main" | head -6
ls /var/www/zxjiu/dist/ | grep -vE "\.html$|^js$|^images$|^css$|^videos$|^assets$" | head -10
echo ""
echo "=== C. nginx 配置文件定位 ==="
nginx -T 2>/dev/null | grep -E "server_name|location|root|index " | head -30
