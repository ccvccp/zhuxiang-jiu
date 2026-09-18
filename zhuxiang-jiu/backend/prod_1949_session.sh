#!/bin/bash
# 19:49 会话完整序列(自愈旁证: 401 后有无紧跟 refresh)
grep '18/Sep/2026:19:49' /var/log/nginx/access.log | awk '{print $4, $9, $7}' | head -30
