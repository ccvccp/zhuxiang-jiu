#!/bin/bash
echo "=== map 与根分流完整段 ==="
nginx -T 2>/dev/null | grep -B2 -A12 "map .*http_user_agent\|location = / " | head -50
