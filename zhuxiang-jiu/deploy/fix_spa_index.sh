#!/bin/bash
set -e
TS=$(date +%m%d-%H%M)
echo "=== 1. 备份误放的官网页(保留内容) ==="
cp /var/www/zxjiu/dist/index.html /var/www/zxjiu/dist.bak-official-misplaced-$TS.html
ls -la /var/www/zxjiu/dist.bak-official-misplaced-$TS.html
echo "=== 2. 恢复 SPA 壳 ==="
cp /opt/zhuxiang/deploy_tmp/spa_index.html /var/www/zxjiu/dist/index.html
echo "=== 3. 验证: SPA 特征 + 配套 chunk 在位 ==="
F=/var/www/zxjiu/dist/index.html
echo "app_div: $(grep -c 'id=\"app\"' $F)"
echo "app_chunk: $(grep -oE 'app\.[a-z0-9]+\.js' $F)"
echo "wake_widget: $(grep -c 'voice-wake-widget' $F)"
echo "chunk_exists: $(ls /var/www/zxjiu/dist/js/app.b984e24b.js /var/www/zxjiu/dist/js/taro.6f3e5fe0.js /var/www/zxjiu/dist/css/app.b241c438098a04dbd156.css >/dev/null 2>&1 && echo YES || echo NO)"
echo "=== 4. UA 分流双端验证 ==="
curl -s -o /dev/null -w "mobile_ua(/)=%{http_code} " -A "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)" https://zxjiu.com/
curl -s -A "Mozilla/5.0 (iPhone)" https://zxjiu.com/ | grep -c 'voice-wake-widget' | xargs echo "wake_in_html:"
curl -s -o /dev/null -w "pc_ua(/)=%{http_code} " -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" https://zxjiu.com/
curl -s -A "Mozilla/5.0 (Windows NT 10.0)" https://zxjiu.com/ | grep -o '<title>[^<]*' | head -1
echo ""
echo "=== 5. widget 资产可达 ==="
curl -s -o /dev/null -w "voice-wake-widget.js=%{http_code}\n" https://zxjiu.com/js/voice-wake-widget.js
