# 探针: 生产存储模式确认(.env 关键行/Redis DBSIZE/近期 chat 键)
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }
Write-Host '==> .env 存储相关行'
Select-String -Path C:\backend\.env -Pattern 'STORE_MODE|REDIS_URL|LOCK_MODE' | ForEach-Object { Info ($_.Line -replace '=.*KEY.*', '=***') }
Write-Host '==> Redis DBSIZE(0/1 库)'
Info ('db0: ' + (& C:\redis\redis-cli.exe -n 0 DBSIZE))
Info ('db1: ' + (& C:\redis\redis-cli.exe -n 1 DBSIZE))
Write-Host '==> 全前缀键样例(前10)'
& C:\redis\redis-cli.exe --scan --pattern '*' 2>$null | Select-Object -First 10 | ForEach-Object { Info $_ }
Write-Host '==> chat 相关键(任意前缀)'
& C:\redis\redis-cli.exe --scan --pattern '*chat*' 2>$null | Select-Object -First 10 | ForEach-Object { Info $_ }
Write-Host 'PROBE-DONE'
