﻿﻿﻿# 急救3: 服务处于 PAUSED(nssm throttle), sc.exe 原生 stop/start
$ErrorActionPreference = 'Continue'
Write-Host '==> [1/3] 状态/stop'
$s = sc.exe query backend8147 | Select-String 'STATE'
Write-Host ("    当前: " + $s)
sc.exe stop backend8147 | Out-Null
Start-Sleep -Seconds 3
Write-Host '==> [2/3] start'
$r = sc.exe start backend8147
Write-Host ("    sc start: " + ($r -join ' '))
Start-Sleep -Seconds 8
Write-Host '==> [3/3] 健康+mode'
$h = curl.exe -s -m 10 http://127.0.0.1:8000/api/decision/health
Write-Host ("    health: " + $h)
$m = curl.exe -s -m 10 http://127.0.0.1:8000/api/chat/mode
Write-Host ("    mode: " + $m)
if (-not $h) { Get-Content C:\backend\logs\service-err.log -Tail 6 | ForEach-Object { Write-Host "    $_" }; exit 1 }
Write-Host 'ENV-FIX3-DONE'
