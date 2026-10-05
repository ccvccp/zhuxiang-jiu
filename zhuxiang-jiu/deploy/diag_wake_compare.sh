#!/bin/bash
# 唤醒修复效果对比: 24h 全窗 + 部署后窗口分离
echo "=== 一、24h 全窗(基线+修复混合, diag_wake_slow2 原版口径) ==="
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep "ws_asr_final" | wc -l | xargs echo "final_total:"
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep "ws_asr_final" | grep -oP "text='[^']*'" | grep -c "小竹" | xargs echo "with_xiaozhu:"
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep "ws_asr_final" | grep -oP "text=None" | wc -l | xargs echo "empty_final(None):"
echo ""
echo "=== 二、部署后窗口(修复版 07:23 起, 只看新数据) ==="
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep "ws_asr_final" | awk -F'[ .]' '$1 >= "2026-10-06" && $2 >= "07:23"' | tee /tmp/final_after.txt | wc -l | xargs echo "final_after_deploy:"
grep -oP "text='[^']*'" /tmp/final_after.txt | grep -c "小竹" | xargs echo "  with_xiaozhu:"
grep -c "text=None" /tmp/final_after.txt | xargs echo "  empty_final(None):"
cat /tmp/final_after.txt | tail -10
echo ""
echo "=== 三、部署后异常面(建流失败/read_loop 崩/并发 send) ==="
docker logs zhuxiang-backend-1 --since 24h 2>&1 | grep -E "stream_flush_failed|stream_feed_failed|stream_finish_failed|await' expression|stream_unavailable" | awk -F'[ .]' '$1 >= "2026-10-06" && $2 >= "07:23"' | tail -6
echo "(空=无异常)"
