#!/bin/bash
# 36号P2 步骤5-6 一键执行: 验证token → 填入.env → rebuild → 验收
# 用法: bash deploy_weibo_token.sh <access_token>
# (验证阶段会真实发布一条测试微博, 可在发布账号上手动删除)
set -e
TOKEN="${1:?用法: bash $0 <access_token>}"

echo "== 1. 验证 token: 调 share.json 发测试微博 =="
RESP=$(curl -s -X POST https://api.weibo.com/2/statuses/share.json \
  -d "access_token=$TOKEN" \
  --data-urlencode "status=竹香酒发布通道测试(可删除) 🎋")
echo "  微博响应: ${RESP:0:200}"
if ! echo "$RESP" | grep -q 'idstr'; then
  echo "  ✗ token 验证失败(报错对照: 21301=无效/过期回步骤3-4重授权,"
  echo "    21332=应用信息不匹配, test users over limit=非创建者账号)"
  exit 1
fi
echo "  ✓ token 有效(测试微博已真实发布, 可删除)"

echo "== 2. 填入 /opt/zhuxiang/.env =="
sed -i "s|^PROMO_CHANNEL_WEIBO_KEY=.*|PROMO_CHANNEL_WEIBO_KEY=$TOKEN|" \
  /opt/zhuxiang/.env
grep -c '^PROMO_CHANNEL_WEIBO_KEY=.' /opt/zhuxiang/.env \
  && echo "  ✓ 已写入"

echo "== 3. rebuild backend =="
cd /opt/zhuxiang && docker compose up -d --build backend 2>&1 | tail -1
sleep 14

echo "== 4. 验收: channel_status =="
docker exec zhuxiang-backend-1 python -c "
from services.promo_channel_service import PromoChannelService
for r in PromoChannelService().channel_status():
    if r['platform'] == 'weibo':
        print('  weibo:', 'keyConfigured=', r['keyConfigured'],
              '| effectiveMode=', r['effectiveMode'])
        assert r['keyConfigured'] and r['effectiveMode'] == 'real'
print('  ✓ P2 真发布通道就绪')
"
echo "== 完成: 人工 review 通过的内容将真实发布到微博 =="
