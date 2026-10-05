#!/bin/bash
F=/var/www/zxjiu/dist/index.html
echo "app_div: $(grep -c 'id="app"' $F)"
echo "app_chunk: $(grep -oE 'app\.[a-z0-9]+\.js' $F | head -1)"
echo "wake_widget: $(grep -c 'voice-wake-widget' $F)"
echo "scene_widget: $(grep -c 'scene-widget' $F)"
echo "v72: $(grep -c 'v72-variant' $F)"
echo "--- body 前几行 ---"
grep -A3 '<body' $F | head -5
echo "--- 最近备份的 index 对照(SPA 特征) ---"
for d in dist.bak-taro1017 dist.bak-p2; do
  B=/var/www/zxjiu/$d/index.html
  [ -f $B ] && echo "$d: app_div=$(grep -c 'id=\"app\"' $B) wake=$(grep -c 'voice-wake-widget' $B) size=$(stat -c%s $B)"
done
