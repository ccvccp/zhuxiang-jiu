#!/bin/bash
# 返回钮批量注入部署: public 基座 + zx 覆盖
set -e
DIST=/var/www/zxjiu/dist
B1=https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/backbtn-public-20261019.tgz
B2=https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/backbtn-zx-20261019.tgz
mkdir -p /tmp/backbtn-bak-20261019
cp -f $DIST/zjian.html $DIST/alliance-mall.html $DIST/login.html /tmp/backbtn-bak-20261019/ 2>/dev/null || true
curl -sSL -o /tmp/bb1.tgz "$B1"
curl -sSL -o /tmp/bb2.tgz "$B2"
cd $DIST
tar -xzf /tmp/bb1.tgz
tar -xzf /tmp/bb2.tgz
n=$(grep -l globalBackBtn $DIST/*.html | wc -l)
echo "含返回钮页面数: $n"
echo "zjian.html: $(grep -c globalBackBtn $DIST/zjian.html)"
echo "抽查 200: $(curl -s -o /dev/null -w '%{http_code}' -H 'Host: zyjiu.com' -k https://127.0.0.1/zjian.html --resolve zyjiu.com:443:127.0.0.1 2>/dev/null || echo n/a)"
echo BACKBTN-OVERSEAS-DONE
