# 修复: ①补回被覆盖的 CHAT_LLM_MODE=shadow(保留已注入的 JWT_SECRET——服务器端
# get 解析现值完整重写三变量) ②健康验证改 /api/decision/health(真存活探针匿名200;
# /api/health 匿名401系安全层预期——auth_401 同竹医 audit 口径)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($msg) { Write-Host "==> $msg" }
function Info($msg) { Write-Host "    $msg" }

Step '[1/5] 读当前环境并解析 JWT_SECRET(服务器内部, 不回传)'
$cur = (cmd /c "C:\nginx\nssm.exe get backend8147 AppEnvironmentExtra" 2>&1) -join ' '
if ($cur -match 'JWT_SECRET=([0-9A-F]{64})') {
    $secret = $Matches[1]
    Info ("JWT_SECRET 已在位: 长度 " + $secret.Length + " 指纹 " + $secret.Substring(0,4) + "****")
} else {
    throw '未解析到已注入的 JWT_SECRET——上轮注入可能未生效, 终止'
}
Info ("当前环境含 CHAT_LLM_MODE: " + [bool]($cur -match 'CHAT_LLM_MODE'))

Step '[2/5] 完整重写三变量(补回 CHAT_LLM_MODE=shadow)'
cmd /c "C:\nginx\nssm.exe set backend8147 AppEnvironmentExtra PYTHONUTF8=1 CHAT_LLM_MODE=shadow JWT_SECRET=$secret"
if ($LASTEXITCODE -ne 0) { throw 'nssm set 失败' }
Info '已完整写入 PYTHONUTF8=1 + CHAT_LLM_MODE=shadow + JWT_SECRET'

Step '[3/5] 重启服务'
cmd /c "C:\nginx\nssm.exe restart backend8147"
Start-Sleep -Seconds 10
$state = (Get-Service backend8147).Status
Info ("重启后状态: " + $state)
if ($state -ne 'Running') {
    Get-Content C:\backend\logs\service-err.log -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
    throw '服务重启失败'
}

Step '[4/5] 健康验证(decision/health 真存活探针+health 401 预期口径)'
$code = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/decision/health
Info ("本地 decision/health: " + $code)
$code2 = curl.exe -sk -o NUL -w '%{http_code}' -m 10 https://zxjiu.com/api/decision/health
Info ("公网 decision/health: " + $code2)
$h401 = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/health
Info ("本地 /api/health: " + $h401 + "(匿名 401=安全层预期, app 有响应即活)")
if ($code -ne '200' -or $code2 -ne '200') { throw '真存活探针未过' }

Step '[5/5] 环境终态核验(脱敏回读)'
$dump = (cmd /c "C:\nginx\nssm.exe get backend8147 AppEnvironmentExtra" 2>&1) -join ' '
Info ("终态: " + ($dump -replace 'JWT_SECRET=[0-9A-F]{60,64}', 'JWT_SECRET=<64hex-已脱敏>'))
$ok = ($dump -match 'PYTHONUTF8=1') -and ($dump -match 'CHAT_LLM_MODE=shadow') -and ($dump -match ('JWT_SECRET=' + $secret.Substring(0,4)))
if (-not $ok) { throw '终态三变量核验未全过' }

Write-Host ''
Write-Host 'FIX-DONE: 三变量完整(PYTHONUTF8+CHAT_LLM_MODE=shadow+JWT_SECRET), 真存活探针 200'
