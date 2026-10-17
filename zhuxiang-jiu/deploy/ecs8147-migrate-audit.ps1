# 国内 ECS 现状摸底: 后端服务/Redis 指向/数据量/nginx api 指向/媒体目录
$ProgressPreference = 'SilentlyContinue'
function Info($m) { Write-Host "    $m" }

Write-Host '== [1] backend8147 服务 =='
$svc = Get-Service backend8147 -ErrorAction SilentlyContinue
Info ("服务状态: " + $(if ($svc) { $svc.Status } else { 'NOT_FOUND' }))
$h = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/health
Info ("本机 8000 /api/health: $h")

Write-Host '== [2] .env 关键指向(REDIS/DB/媒体) =='
Select-String -Path C:\backend\.env -Pattern 'REDIS|DB_|MEDIA|S3|OSS' -ErrorAction SilentlyContinue | ForEach-Object {
    $ln = $_.Line
    if ($ln -match 'SECRET|PASSWORD|KEY=') { $ln = $ln -replace '=.*$', '=***' }
    Info $ln
}

Write-Host '== [3] 本机 Redis? =='
$redisProc = Get-Process redis-server -ErrorAction SilentlyContinue
Info ("redis-server 进程: " + $(if ($redisProc) { $redisProc.Id } else { '无' }))
Test-NetConnection 127.0.0.1 -Port 6379 -WarningAction SilentlyContinue | ForEach-Object { Info ("127.0.0.1:6379 可达: " + $_.TcpTestSucceeded) }

Write-Host '== [4] 数据量(本机8000) =='
$prod = curl.exe -s -m 8 'http://127.0.0.1:8000/api/product/list?page=1&page_size=1'
Info ("product/list: " + $prod.Substring(0, [Math]::Min(100, $prod.Length)))
$comm = curl.exe -s -m 8 'http://127.0.0.1:8000/api/community/posts?page=1&page_size=1'
Info ("community/posts: " + $comm.Substring(0, [Math]::Min(100, $comm.Length)))

Write-Host '== [5] nginx /api 指向 =='
$conf = Get-Content C:\nginx\conf\site-proxy.conf -Raw -ErrorAction SilentlyContinue
if (-not $conf) { $conf = (Get-Content C:\nginx\conf\nginx.conf -Raw) }
Info ($conf -split "`n" | Where-Object { $_ -match 'proxy_pass|location' } | ForEach-Object { $_.Trim() }) 

Write-Host '== [6] 媒体/数据目录 =='
Info ("C:\backend\media: " + $(if (Test-Path C:\backend\media) { (Get-ChildItem C:\backend\media -Recurse -File | Measure-Object Length -Sum).Sum / 1MB; 'MB' } else { '无' }))
Info ("C:\backend data 类: " + ((Get-ChildItem C:\backend -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name) -join ','))
Write-Host 'MIGRATION-AUDIT-DONE'
