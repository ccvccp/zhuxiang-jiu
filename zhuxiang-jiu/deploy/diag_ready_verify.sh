#!/bin/bash
# 应答即就绪+防重唤醒 部署后验收
echo "=== 一、final 形态(部署 08:15 后) ==="
docker logs zhuxiang-backend-1 --since 45m 2>&1 | grep "ws_asr_final" | awk -F'[ .]' '$1 >= "2026-10-06" && $2 >= "08:15"' | tee /tmp/f3.txt | wc -l | xargs echo "total:"
grep -c "text=None" /tmp/f3.txt | xargs echo "  empty:"
grep -oP "text='[^']*'" /tmp/f3.txt | grep -c "小竹" | xargs echo "  wake_word_hits:"
echo "--- 明细 ---"; cat /tmp/f3.txt | tail -12
echo ""
echo "=== 二、对话轮(连续性验证) ==="
docker logs zhuxiang-backend-1 --since 45m 2>&1 | grep -E "voice48_timing|llm_dialog|session_open" | awk -F'[ .]' '$1 >= "2026-10-06" && $2 >= "08:15"' | tail -12
echo ""
echo "=== 三、异常面 ==="
docker logs zhuxiang-backend-1 --since 45m 2>&1 | grep -E "stream_flush_failed|stream_feed_failed|not_woken" | awk -F'[ .]' '$1 >= "2026-10-06" && $2 >= "08:15"' | tail -5
echo "(空=无异常)"
