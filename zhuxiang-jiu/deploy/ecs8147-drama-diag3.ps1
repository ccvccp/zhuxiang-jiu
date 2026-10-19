﻿﻿﻿﻿﻿# 诊断+恢复: 注册表读 nssm 配置, 手动 start, 看真实日志
$ProgressPreference = 'SilentlyContinue'
Write-Host '--- registry params ---'
$reg = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\backend8147\Parameters' -ErrorAction SilentlyContinue
$errf = ''
if ($reg) {
    Write-Host ("App=" + [string]$reg.Application)
    Write-Host ("Dir=" + [string]$reg.AppDirectory)
    Write-Host ("Params=" + [string]$reg.AppParameters)
    Write-Host ("Stdout=" + [string]$reg.AppStdout)
    $errf = [string]$reg.AppStderr
    Write-Host ("Stderr=" + $errf)
} else {
    Write-Host 'no registry params'
}
Write-Host '--- service state via sc ---'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$st = Get-Service backend8147 -ErrorAction SilentlyContinue
if ($st) { Write-Host ("svc=" + $st.Status) } else { Write-Host 'svc=notfound' }
Write-Host '--- manual start ---'
if ($st -and $st.Status -ne 'Running') {
    $r = & sc.exe start backend8147 2>&1
    Write-Host (($r -join ' | ').Substring(0, [Math]::Min(200, ($r -join ' | ').Length)))
}
Start-Sleep -Seconds 15
$h = & curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/decision/health
Write-Host ("health-after-start=" + $h)
Write-Host '--- stderr log tail ---'
if ($errf -and (Test-Path $errf)) {
    Get-Content $errf -Tail 30
} elseif (Test-Path 'C:\backend\err.log') {
    Get-Content 'C:\backend\err.log' -Tail 30
}
Write-Host 'DIAG3-DONE'
