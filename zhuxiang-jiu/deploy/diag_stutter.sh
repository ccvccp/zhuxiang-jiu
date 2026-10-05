#!/bin/bash
echo "=== A. 最近 15 分钟 轮次时序(final + timing 交错) ==="
docker logs zhuxiang-backend-1 --since 15m 2>&1 | grep -E "ws_asr_final|voice48_timing|voice48_session_open|llm_dialog|not_woken" | tail -40
echo ""
echo "=== B. LLM 轮次与意图(答非所问/兜底轮?) ==="
docker logs zhuxiang-backend-1 --since 15m 2>&1 | grep -iE "xiaozhu.*(intent|reply|turn|fallback|general)" | tail -15
