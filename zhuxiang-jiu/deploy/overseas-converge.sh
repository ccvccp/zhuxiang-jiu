#!/bin/bash
# 全量前端收敛: SPA 基座 + 静态覆盖 -> /var/www/zxjiu/dist (只覆盖不删除)
set -e
DIST=/var/www/zxjiu/dist
SPA=https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/converge-20261019-spa.tgz
STATIC=https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/converge-20261019-static.tgz

echo '== [1/5] 备份关键文件 =='
mkdir -p /tmp/converge-bak-20261019
cp -f $DIST/index.html $DIST/official.html $DIST/my-card.html $DIST/css/style.css /tmp/converge-bak-20261019/ 2>/dev/null || true
ls /tmp/converge-bak-20261019/

echo '== [2/5] 下载两个包 =='
curl -sSL -o /tmp/converge-spa.tgz "$SPA"
curl -sSL -o /tmp/converge-static.tgz "$STATIC"
ls -la /tmp/converge-spa.tgz /tmp/converge-static.tgz

echo '== [3/5] 解压: 先 SPA 基座, 后静态覆盖 =='
cd $DIST
tar -xzf /tmp/converge-spa.tgz
tar -xzf /tmp/converge-static.tgz
echo '解压完成'

echo '== [4/5] 清理调试遗留 =='
rm -f $DIST/tmp_diag.html
rm -rf $DIST/tmp_debug
echo '清理完成'

echo '== [5/5] 验证 =='
echo "html总数: $(ls -1 $DIST/*.html | wc -l)"
for f in my-card.html xiaozhu-voice.html alliance-mall.html wine-advisor.html index.html official.html; do
  [ -f $DIST/$f ] && echo "$f: YES" || echo "$f: NO"
done
echo "index kk-ball: $(grep -c kk-ball $DIST/index.html)"
echo "index jpg残留: $(grep -c 'kk-.*jpg' $DIST/index.html || true)"
echo "js文件数: $(ls -1 $DIST/js/*.js | wc -l)"
echo CONVERGE-OVERSEAS-DONE
