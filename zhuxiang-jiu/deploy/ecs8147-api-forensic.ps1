# 取证: ECS nginx /api 反代目标 + 本机是否有独立后端进程
Write-Host '--- nginx /api 配置:'
Select-String -Path 'C:\www\nginx\conf\nginx.conf','C:\www\nginx\conf\*.conf' -Pattern 'location /api|proxy_pass' -ErrorAction SilentlyContinue | ForEach-Object { Write-Host ($_.Filename + ':' + $_.LineNumber + ' ' + $_.Line.Trim()) }
Write-Host '--- 本机 8000/8001 监听:'
netstat -ano | Select-String ':8000|:8001' | Select-Object -First 6 | ForEach-Object { Write-Host $_.Line.Trim() }
Write-Host '--- python/uvicorn 进程:'
Get-Process | Where-Object { $_.ProcessName -match 'python|uvicorn' } | Select-Object -First 5 | ForEach-Object { Write-Host ($_.ProcessName + ' pid=' + $_.Id) }
Write-Host 'PROBE-DONE'
