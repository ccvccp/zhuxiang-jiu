#!/bin/bash
# 自愈补丁部署后 401 分布: xinzhi 画像面是否仍有新增
echo "=== xinzhi 401 全部时间线 ==="
grep ' 401 ' /var/log/nginx/access.log | grep xinzhi | awk '{print $4, $7}'
echo ""
echo "=== 补丁部署(08:30:53 UTC)后全部 401 路径分布 ==="
awk '$4 >= "[18/Sep/2026:08:30:53"' /var/log/nginx/access.log | grep ' 401 ' | awk '{print $7}' | sort | uniq -c | sort -rn | head -20
echo ""
echo "=== 补丁后 401 总数 ==="
awk '$4 >= "[18/Sep/2026:08:30:53"' /var/log/nginx/access.log | grep -c ' 401 ' || true
