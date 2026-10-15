# 排期核验: 国内库 content 75-88 状态 + rpa 队列 + scheduler 日志
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host ("  " + $m) }

Write-Host '== data 目录结构(一层) =='
Get-ChildItem C:\backend\data -Directory -ErrorAction SilentlyContinue | ForEach-Object { Info ("DIR " + $_.Name) }
Get-ChildItem C:\backend\data -File -ErrorAction SilentlyContinue | Select-Object -First 25 | ForEach-Object { Info ($_.Name + " " + [math]::Round($_.Length/1KB,1) + "KB " + $_.LastWriteTime.ToString('MM-dd HH:mm')) }

Write-Host '== content 存储(找 promo/content JSON) =='
$cands = Get-ChildItem C:\backend\data -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'content|promo' -and $_.Length -lt 10MB }
$cands | Select-Object -First 10 | ForEach-Object { Info ($_.FullName.Replace('C:\backend\','') + " " + [math]::Round($_.Length/1KB,1) + "KB") }

Write-Host '== 用 python 直接查内存态 content 75-88(走 HTTP 语义近似:读本机服务) =='
# 直接打本地 API 公开端点探测队列不可行(需token), 改查后端日志近期 rpa/publish 相关行
Get-Content C:\backend\logs\service-out.log -Tail 300 -ErrorAction SilentlyContinue | Select-String -Pattern 'rpa|publish|promo|PROMO_PUBLISH|queued' | Select-Object -Last 12 | ForEach-Object { Info ($_.Line.Substring(0, [Math]::Min(150, $_.Line.Length))) }
Write-Host 'PENDING-CHECK-DONE'
