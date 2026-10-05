#!/bin/bash
# 小竹语音唤醒迟缓排查·日志面
echo "=== A. 近 2h 唤醒/会话事件 ==="
docker logs zhuxiang-backend-1 --since 2h 2>&1 | grep -iE "wake|voice48|session_open|ws_asr" | tail -40
echo ""
echo "=== B. 近 2h ASR/唤醒异常 ==="
docker logs zhuxiang-backend-1 --since 2h 2>&1 | grep -iE "hub_asr_exception|asr.*(fail|error|timeout)|wake.*(fail|miss)" | tail -20
echo ""
echo "=== C. voice48_timing 抽样(延迟形态) ==="
docker logs zhuxiang-backend-1 --since 2h 2>&1 | grep "voice48_timing" | tail -8
