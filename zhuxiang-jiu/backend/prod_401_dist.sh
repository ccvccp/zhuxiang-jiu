#!/bin/bash
# strict 切换后 nginx 401 请求路径分布(真实受损面)
echo "--- 401 路径 TOP30 ---"
grep ' 401 ' /var/log/nginx/access.log 2>/dev/null | grep -oE '"[A-Z]+ [^ ?]+' | sed 's/^"//' | awk '{print $2}' | sort | uniq -c | sort -rn | head -30
echo ""
echo "--- 401 总数 / 按 UA 分类(浏览器 vs 其他) ---"
grep ' 401 ' /var/log/nginx/access.log | wc -l
echo "--- 含 Mozilla(浏览器)的 401 ---"
grep ' 401 ' /var/log/nginx/access.log | grep -c 'Mozilla' || true
