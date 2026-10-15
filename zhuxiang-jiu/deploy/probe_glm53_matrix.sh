#!/bin/bash
# glm-5.3 端点兼容矩阵实测(排查 Trae 1211)——生产机执行, key 不出服务器
KEY=$(grep "^LLM_API_KEY=" /opt/zhuxiang/.env | cut -d= -f2)

req() {  # req <base> <model>
    curl -s --max-time 30 "$1/chat/completions" \
      -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
      -d "{\"model\":\"$2\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":8}" \
      | head -c 200
    echo
}

echo "== 普通端点 paas/v4 + glm-5.3 =="
req https://open.bigmodel.cn/api/paas/v4 glm-5.3
echo "== Coding端点 coding/paas/v4 + glm-5.3 =="
req https://open.bigmodel.cn/api/coding/paas/v4 glm-5.3
echo "== Coding端点 coding/paas/v4 + glm-5.3-flash =="
req https://open.bigmodel.cn/api/coding/paas/v4 glm-5.3-flash
echo "== Coding端点 coding/paas/v4 + glm-4.6(基线) =="
req https://open.bigmodel.cn/api/coding/paas/v4 glm-4.6
