﻿﻿﻿# 急救: 修正 backend8147 env(nssm 多变量须 \0 分隔)并重启
$ErrorActionPreference = 'Stop'
Write-Host '==> [1/3] 重设 env(PYTHONUTF8=1 CHAT_LLM_MODE=shadow, \0 分隔)'
& C:\nginx\nssm.exe set backend8147 AppEnvironmentExtra 'PYTHONUTF8=1\0CHAT_LLM_MODE=shadow' 2>$null | Out-Null
$e = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\backend8147' -Name Environment -ErrorAction SilentlyContinue).Environment
Write-Host ("    注册表: " + ($e -join ' | '))
Write-Host '==> [2/3] 重启'
& C:\nginx\nssm.exe restart backend8147 | Out-Null
Start-Sleep -Seconds 7
Write-Host '==> [3/3] 健康检查'
$h = curl.exe -s -m 10 http://127.0.0.1:8000/api/decision/health
Write-Host ("    health: " + $h)
$m = curl.exe -s -m 10 http://127.0.0.1:8000/api/chat/mode
Write-Host ("    mode: " + $m)
if (-not $h) { Get-Content C:\backend\logs\service-err.log -Tail 6 | ForEach-Object { Write-Host "    $_" }; exit 1 }
Write-Host 'ENV-FIX-DONE'
