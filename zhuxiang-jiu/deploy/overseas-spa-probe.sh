#!/bin/bash
echo '--- app.html scripts:'
grep -oE 'src="[^"]+\.js"' /var/www/zxjiu/dist/app.html | head
echo '--- dist 根下 app/chunk/page 相关:'
ls /var/www/zxjiu/dist/ | grep -iE '^app|chunk|page' | head -20
echo '--- 含 product-detail 路由的 js:'
grep -l 'pages/product-detail' /var/www/zxjiu/dist/*.js 2>/dev/null | head -3
echo '--- router mode(hash?):'
grep -o 'hashRouter[^,}]*' /var/www/zxjiu/dist/app.*.js 2>/dev/null | head -2
