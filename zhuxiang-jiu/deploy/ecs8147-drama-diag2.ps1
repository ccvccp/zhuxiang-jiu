﻿﻿﻿# 诊断: backend8147 启动失败根因
$ProgressPreference = 'SilentlyContinue'
Write-Host '--- nssm dump(关键行) ---'
$d = & cmd /c 'C:\nginx\nssm.exe dump backend8147' 2>&1
$d | Select-String -Pattern 'AppStdout|AppStderr|Application|AppDirectory|AppParameters|AppExit' | ForEach-Object { Write-Host $_.Line }
Write-Host '--- 端口监听 ---'
netstat -ano | Select-String ':8000|:8147' | Select-Object -First 6 | ForEach-Object { Write-Host $_.Line }
Write-Host '--- 进程 ---'
Get-Process python* -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime | Format-Table -AutoSize | Out-String | Write-Host
Write-Host '--- 服务状态 ---'
& cmd /c 'C:\nginx\nssm.exe status backend8147'
Write-Host '--- 日志尾部(找 nssm stdout/stderr 常见路径) ---'
foreach ($p in @('C:\nginx\logs\backend8147-out.log', 'C:\nginx\logs\backend8147-err.log', 'C:\backend\logs\out.log', 'C:\backend\logs\err.log')) {
    if (Test-Path $p) {
        Write-Host "[$p]"
        Get-Content $p -Tail 25
    }
}
Write-Host 'DIAG2-DONE'
