#!/bin/bash
set -e
T=/opt/zhuxiang/deploy_tmp
echo "=== 1. 部署三件(widget/面板/SPA 壳) ==="
cp $T/voice-wake-widget.js /var/www/zxjiu/dist/js/voice-wake-widget.js
cp $T/xiaozhu-voice.html /var/www/zxjiu/dist/xiaozhu-voice.html
cp $T/spa_index.html /var/www/zxjiu/dist/index.html
echo "=== 2. 验证 ==="
grep -c 'pendingWakeReply' /var/www/zxjiu/dist/js/voice-wake-widget.js | xargs echo "widget_pending_reply:"
grep -c 'xz-panel-mic-ready' /var/www/zxjiu/dist/xiaozhu-voice.html /var/www/zxjiu/dist/js/voice-wake-widget.js
grep -o 'voice-wake-widget.js?v=[0-9]*' /var/www/zxjiu/dist/index.html | head -1
curl -s -o /dev/null -w "widget_v97=%{http_code}\n" https://zxjiu.com/js/voice-wake-widget.js?v=97
curl -s "https://zxjiu.com/js/voice-wake-widget.js?v=97" | grep -c 'xz-panel-mic-ready' | xargs echo "live_widget_micready:"
curl -s -o /dev/null -w "panel=%{http_code}\n" "https://zxjiu.com/xiaozhu-voice.html?v=97&embed=1"
