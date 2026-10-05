#!/bin/bash
echo "=== A. 07:30 后 xiaozhu 接口请求(轮次提交/唤醒判定) ==="
docker logs zhuxiang-backend-1 --since 60m 2>&1 | grep -E "POST /api/xiaozhu|GET /api/xiaozhu" | tail -20
echo ""
echo "=== B. voice48/轮次/detect 服务日志 ==="
docker logs zhuxiang-backend-1 --since 60m 2>&1 | grep -E "voice48|detect_wake|xiaozhu_turn|wake_win|not_woken" | tail -15
echo ""
echo "=== C. LLM 对话轮(若进入对话) ==="
docker logs zhuxiang-backend-1 --since 60m 2>&1 | grep -E "llm_dialog|xiaozhu_llm|intent" | tail -10
