#!/bin/bash
# 智谱包月配置检查(生产机执行, key 不出服务器)
KEY=$(grep "^LLM_API_KEY=" /opt/zhuxiang/.env | cut -d= -f2)
echo "KEY 前缀: ${KEY:0:8}...  长度: ${#KEY}"
echo "--- 完整 LLM_MODEL 配置 ---"
grep -E "^LLM_MODEL" /opt/zhuxiang/.env

req() {  # req <url> <model>
    curl -s --max-time 20 "$1/chat/completions" \
      -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
      -d "{\"model\":\"$2\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":8}" \
      | head -c 260
    echo
}

echo "--- 1) 普通端点 glm-4-flash(当前生产默认档, 基础可用性) ---"
req https://open.bigmodel.cn/api/paas/v4 glm-4-flash
echo "--- 2) 普通端点 GLM-4.6(付费模型权益探测) ---"
req https://open.bigmodel.cn/api/paas/v4 glm-4.6
echo "--- 3) 普通端点 GLM-5.3(36号 promo 注释提及档) ---"
req https://open.bigmodel.cn/api/paas/v4 glm-5.3
echo "--- 4) Coding 端点 GLM-4.6(GLM Coding Plan 专属轨) ---"
req https://open.bigmodel.cn/api/coding/paas/v4 glm-4.6
