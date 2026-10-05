#!/bin/bash
# 小竹唤醒不中诊断·final 转写形态分布(24h)
echo "=== A. ws_asr_final 全量(24h, 唤醒不中诊断埋点) ==="
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep "ws_asr_final" | tail -50
echo ""
echo "=== B. 形态统计 ==="
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep "ws_asr_final" | wc -l | xargs echo "final_total:"
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep "ws_asr_final" | grep -c "text=''" | xargs echo "empty_final:"
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep "ws_asr_final" | grep -oP "text='[^']*'" | grep -c "小竹" | xargs echo "with_xiaozhu:"
echo ""
echo "=== C. 建流失败/段开始(24h) ==="
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep -cE "ws_asr_v2_stream_unavailable|ws_asr_error" | xargs echo "stream_fail_or_err:"
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep "ws_asr_v2_arm" | tail -6
echo ""
echo "=== D. armed/就绪与 auth_failed(24h) ==="
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep -c "ws_asr_v2_armed" | xargs echo "armed:"
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep "auth_failed" | tail -5
