#!/bin/bash
echo '=== ROOT / (mobile UA) headers ==='
curl -s -D - -o /dev/null -H 'Host: zyjiu.com' -A 'Mozilla/5.0 (Linux; Android 13) Mobile MicroMessenger' http://127.0.0.1/ | head -14
echo '=== /index.html headers ==='
curl -s -D - -o /dev/null -H 'Host: zyjiu.com' http://127.0.0.1/index.html | head -12
echo '=== nginx conf cache 相关 ==='
grep -n 'Cache-Control\|no-cache\|expires' /etc/nginx/sites-enabled/zxjiu.conf 2>/dev/null | head -10 || grep -rn 'Cache-Control\|no-cache' /etc/nginx/ 2>/dev/null | grep zxjiu | head -10
