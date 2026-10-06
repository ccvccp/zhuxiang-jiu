#!/bin/bash
# 令牌桶版部署后综合验收(08:03 起)
echo "=== 一、部署后 final 形态(空转写占比应趋零) ==="
docker logs zhuxiang-backend-1 --since 40m 2>&1 | grep "ws_asr_final" | awk -F'[ .]' '$1 >= "2026-10-06" && $2 >= "08:03"' | tee /tmp/f2.txt | wc -l | xargs echo "final_total:"
grep -c "text=None" /tmp/f2.txt | xargs echo "  empty(None):"
grep -oP "text='[^']*'" /tmp/f2.txt | grep -c "小竹" | xargs echo "  with_xiaozhu:"
echo "--- 明细 ---"; cat /tmp/f2.txt | tail -15
echo ""
echo "=== 二、对话轮次(voice48 + LLM) ==="
docker logs zhuxiang-backend-1 --since 40m 2>&1 | grep -E "voice48_timing|llm_dialog" | awk -F'[ .]' '$1 >= "2026-10-06" && $2 >= "08:03"' | tail -12
echo ""
echo "=== 三、异常面 ==="
docker logs zhuxiang-backend-1 --since 40m 2>&1 | grep -E "stream_flush_failed|stream_feed_failed|await' expression|not_woken" | awk -F'[ .]' '$1 >= "2026-10-06" && $2 >= "08:03"' | tail -6
echo "(空=无异常)"
