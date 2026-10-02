#!/bin/bash
# 注册功能生产验证
echo "--- 未成年(期望 409 + 中文错误) ---"
curl -s -w "\nHTTP %{http_code}\n" -X POST https://zxjiu.com/api/entry/register \
  -H "Content-Type: application/json" \
  -d '{"phone":"13900000009","password":"test123456","birthdate":"2012-01-01"}'
echo "--- 成功注册(期望 200 + token) ---"
curl -s -X POST https://zxjiu.com/api/entry/register \
  -H "Content-Type: application/json" \
  -d '{"phone":"13900000009","password":"test123456","ageConfirmed":true,"nickname":"注册验证"}' \
  | head -c 220
echo
echo "--- 重复注册(期望 409) ---"
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST https://zxjiu.com/api/entry/register \
  -H "Content-Type: application/json" \
  -d '{"phone":"13900000009","password":"test123456","ageConfirmed":true}'
echo "--- 页面页签 ---"
curl -s https://zxjiu.com/login.html | grep -c "form-reg\|注册会员"
curl -s "https://zxjiu.com/js/entry-login.js" | grep -c "doRegister"
