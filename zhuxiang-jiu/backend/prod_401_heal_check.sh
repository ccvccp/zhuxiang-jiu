#!/bin/bash
# 验证 401 自愈链: 401 后是否紧跟 refresh 调用与原请求 200 重试
echo "=== refresh 调用统计(状态码分布) ==="
grep '/api/auth/refresh' /var/log/nginx/access.log | awk '{print $9}' | sort | uniq -c
echo ""
echo "=== member/profile 请求状态分布 ==="
grep '/api/member/profile' /var/log/nginx/access.log | awk '{print $9}' | sort | uniq -c
echo ""
echo "=== 同 IP 时间线抽样(最近一条 401 前后 6 条) ==="
LINE=$(grep -n ' 401 ' /var/log/nginx/access.log | grep 'member/profile' | tail -1 | cut -d: -f1)
if [ -n "$LINE" ]; then
  START=$((LINE-2)); [ $START -lt 1 ] && START=1
  sed -n "${START},$((LINE+5))p" /var/log/nginx/access.log | awk '{print $4, $9, $7}' | head -8
fi
echo ""
echo "=== site-theme/active 401 段落抽样(补录前) ==="
grep ' 401 ' /var/log/nginx/access.log | grep 'site-theme' | tail -2 | awk '{print $4, $7}'
