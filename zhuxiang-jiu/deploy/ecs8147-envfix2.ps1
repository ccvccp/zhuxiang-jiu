﻿﻿﻿# 急救2: 注册表直写 Environment REG_MULTI_SZ + stop/start
$ErrorActionPreference = 'Stop'
Write-Host '==> [1/3] 注册表直写 Environment'
Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\backend8147' `
    -Name Environment -Value @('PYTHONUTF8=1', 'CHAT_LLM_MODE=shadow') -Type MultiString
$e = (Get-Item 'HKLM:\SYSTEM\CurrentControlSet\Services\backend8147').GetValue('Environment')
Write-Host ("    Environment: " + ($e -join ' | '))
Write-Host '==> [2/3] stop/start'
& C:\nginx\nssm.exe stop backend8147 2>$null | Out-Null
Start-Sleep -Seconds 2
& C:\nginx\nssm.exe start backend8147 2>$null | Out-Null
Start-Sleep -Seconds 8
Write-Host '==> [3/3] 健康+mode'
$h = curl.exe -s -m 10 http://127.0.0.1:8000/api/decision/health
Write-Host ("    health: " + $h)
$m = curl.exe -s -m 10 http://127.0.0.1:8000/api/chat/mode
Write-Host ("    mode: " + $m)
if (-not $h) { Get-Content C:\backend\logs\service-err.log -Tail 6 | ForEach-Object { Write-Host "    $_" }; exit 1 }
Write-Host 'ENV-FIX2-DONE'
