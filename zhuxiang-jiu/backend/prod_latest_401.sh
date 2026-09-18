#!/bin/bash
# location 补丁(约 20:0x +0800)部署后的最新 401 增量分布
echo "=== location 补丁部署后 401 路径分布 ==="
awk '$4 >= "[18/Sep/2026:20:05"' /var/log/nginx/access.log | grep ' 401 ' | awk '{print $7}' | grep -v 'refresh' | sort | uniq -c | sort -rn | head -20
echo ""
echo "=== xinzhi/location 最新 401(应零新增) ==="
awk '$4 >= "[18/Sep/2026:20:05"' /var/log/nginx/access.log | grep ' 401 ' | grep -E 'xinzhi|location' || echo "(零新增)"
echo ""
echo "=== 自愈链抽样: 401 后紧跟 refresh 的会话数 ==="
awk '$4 >= "[18/Sep/2026:20:05"' /var/log/nginx/access.log | grep -E ' 401 |auth/refresh' | awk '{print $4, $9, $7}' | tail -20
