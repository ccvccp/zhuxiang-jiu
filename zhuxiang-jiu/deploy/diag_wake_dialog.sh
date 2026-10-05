#!/bin/bash
echo "=== A. 近 40 分钟 final 形态(用户刚实测) ==="
docker logs zhuxiang-backend-1 --since 40m 2>&1 | grep "ws_asr_final" | tail -25
echo ""
echo "=== B. 同窗异常 ==="
docker logs zhuxiang-backend-1 --since 40m 2>&1 | grep -E "stream_flush|stream_feed|await' expression|task-failed|not_woken" | tail -10
echo ""
echo "=== C. 语音轮次(xiaozhu 轮次/唤醒窗) ==="
docker logs zhuxiang-backend-1 --since 40m 2>&1 | grep -iE "xiaozhu.*(turn|wake|轮|not_woken)|llm_dialog|detect" | tail -15
