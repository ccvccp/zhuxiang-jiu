# v3: 诊断+重注入一体化——PS 原生 & 调用 nssm(弃 cmd /c 包装, 多变量传参更可靠),
# set 后立即 get 自证, 自证通过才 restart+健康验证
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($msg) { Write-Host "==> $msg" }
function Info($msg) { Write-Host "    $msg" }

Step '[1/6] 生成新 SECRET(256 位, 服务器端)'
$bytes = New-Object byte[] 32
[Security.Cryptography.RNGCryptoServiceProvider]::Create().GetBytes($bytes)
$secret = ([BitConverter]::ToString($bytes)) -replace '-',''
Info ("指纹: " + $secret.Substring(0,4) + "****")

Step '[2/6] PS 原生调用 nssm set 三变量'
$out1 = & C:\nginx\nssm.exe set backend8147 AppEnvironmentExtra "PYTHONUTF8=1" "CHAT_LLM_MODE=shadow" "JWT_SECRET=$secret" 2>&1
Info ("set 输出: " + ($out1 -join ' | '))
Info ("set ExitCode: " + $LASTEXITCODE)

Step '[3/6] 立即 get 自证(清洗 NUL/换行分隔后子串匹配)'
$dump = (& C:\nginx\nssm.exe get backend8147 AppEnvironmentExtra 2>&1) -join ' '
$dump = $dump -replace "[\x00\r\n\t]", ''
Info ("回读(脱敏): " + ($dump -replace 'JWT_SECRET=[0-9A-F]{60,64}', 'JWT_SECRET=<64hex>'))
$m1 = $dump.Contains('PYTHONUTF8=1')
$m2 = $dump.Contains('CHAT_LLM_MODE=shadow')
$m3 = $dump.Contains('JWT_SECRET=' + $secret.Substring(0,4))
Info ("分项: PYTHONUTF8=$m1 CHAT_LLM_MODE=$m2 JWT指纹=$m3")
$ok = $m1 -and $m2 -and $m3
if (-not $ok) { throw 'set 后 get 自证失败——注入未生效, 不重启服务(现状保持)' }
Info '自证通过: 三变量全部在位'

Step '[4/6] 重启服务(密钥轮换)'
& C:\nginx\nssm.exe restart backend8147 | Out-Null
Start-Sleep -Seconds 10
$state = (Get-Service backend8147).Status
Info ("重启后状态: " + $state)
if ($state -ne 'Running') {
    Get-Content C:\backend\logs\service-err.log -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
    throw '服务重启失败'
}

Step '[5/6] 健康验证(decision/health 真存活探针)'
$code = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/decision/health
Info ("本地 decision/health: " + $code)
$code2 = curl.exe -sk -o NUL -w '%{http_code}' -m 10 https://zxjiu.com/api/decision/health
Info ("公网 decision/health: " + $code2)
if ($code -ne '200' -or $code2 -ne '200') { throw '真存活探针未过' }

Step '[6/6] 终态脱敏回读'
$final = (& C:\nginx\nssm.exe get backend8147 AppEnvironmentExtra 2>&1) -join ' '
Info ("终态: " + ($final -replace 'JWT_SECRET=[0-9A-F]{60,64}', 'JWT_SECRET=<64hex-已注入>'))

Write-Host ''
Write-Host 'V3-DONE: JWT_SECRET 三变量注入自证+服务健康, 密钥已轮换'
