#!/bin/bash
# 精确时间定性: venue/site-theme 补丁后 401 的发生时刻 vs 部署时刻
echo "=== venue/partners 401 全部时间线 ==="
grep ' 401 ' /var/log/nginx/access.log | grep 'venue/partners' | awk '{print $4, $7}'
echo ""
echo "=== site-theme 401 全部时间线 ==="
grep ' 401 ' /var/log/nginx/access.log | grep 'site-theme' | awk '{print $4, $7}'
echo ""
echo "=== location/stores/nearby 401 时间线 ==="
grep ' 401 ' /var/log/nginx/access.log | grep 'location/stores' | awk '{print $4, substr($7,1,50)}'
echo ""
echo "=== 补丁后(08:30:53+)的'我的'面401时间线(自愈终态判定) ==="
awk '$4 >= "[18/Sep/2026:08:30:53"' /var/log/nginx/access.log | grep ' 401 ' | grep -E 'member/profile|wallet/info|points|credit' | awk '{print $4, $7}'
echo ""
echo "=== 部署时刻参考: 文件 mtime ==="
stat -c '%y %n' /opt/zhuxiang/zhuxiang-jiu/backend/core/auth_middleware.py
docker exec zhuxiang-backend-1 printenv AUTH_MODE 2>/dev/null
