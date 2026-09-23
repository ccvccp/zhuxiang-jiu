#!/bin/bash
# 78号P2·H2 连接池生产实证: conn_ms 复用序列
docker logs zhuxiang-backend-1 --since 5m 2>&1 \
  | grep 'voice78_tts_timing' | grep -v 'stream=' \
  | sed 's/.*timing //' | awk '{print $1}' | tail -9
