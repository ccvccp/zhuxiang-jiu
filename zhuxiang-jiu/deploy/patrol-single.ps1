# 竹香酒单端巡检(78号 T2.1, 2026-10-20)
# 背景: 设计文档「库存巡检计划.md」中的 fa991160 双端巡检任务经三机实测(本地schtasks/ECS云助手/海外crontab)查无——从未落地;
#       海外 47.236.61.117 于 2026-11-11 到期退役, 单端(ECS)为常态。本脚本接替该设计, 落地为真实巡检能力。
# 内容: A6 单端预案(进化检查清单) + 业务键面探测 + 绝对阈值(外链图=0/零库存=0)
# 用法: powershell -File patrol-single.ps1
# 定时: schtasks /create /tn "竹香酒单端巡检" /tr "powershell -NoProfile -ExecutionPolicy Bypass -File <本脚本绝对路径>" /sc daily /st 08:30
$ErrorActionPreference = 'Continue'
$DEP = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:ALIYUN_AK_ID = [Environment]::GetEnvironmentVariable('ALIYUN_AK_ID', 'User')
$env:ALIYUN_AK_SECRET = [Environment]::GetEnvironmentVariable('ALIYUN_AK_SECRET', 'User')
if (-not $env:ALIYUN_AK_ID) { Write-Host '[FATAL] AK 未配置(用户级环境变量)'; exit 1 }
$drift = 0

function Parse-KV([string]$text) {
    $m = @{}
    foreach ($line in ($text -split "`n")) {
        if ($line -match '^\s*(\w+)=(\S+)') { $m[$Matches[1]] = $Matches[2] }
    }
    return $m
}

Write-Host '===== [1/4] A6 单端存活探针(ECS) ====='
$alive = @'
$ErrorActionPreference = 'Continue'
Write-Host ("health=" + (curl.exe -s -o NUL -w "%{http_code}" -H "Host: zyjiu.com" --max-time 10 http://127.0.0.1/api/decision/health))
$svc = Get-Service backend8147 -ErrorAction SilentlyContinue
Write-Host ("backend8147=" + ($(if ($svc) { $svc.Status } else { 'NOT-FOUND' })))
Set-Location C:\nginx
$nginxT = & C:\nginx\nginx.exe -t 2>&1
Write-Host ("nginx_test=" + ($(if ($LASTEXITCODE -eq 0) { 'ok' } else { 'fail' })))
$r = & C:\redis\redis-cli.exe KEYS 'zhuxiang:*' 2>$null
Write-Host ("redis_products=" + (@($r).Count))
'@
$tmpAlive = Join-Path $env:TEMP "patrol-alive-$(Get-Random).ps1"
Set-Content $tmpAlive $alive -Encoding UTF8
$sendOut = powershell -NoProfile -ExecutionPolicy Bypass -File "$DEP\aliyun-ecs-rpc.ps1" Send -CommandFile $tmpAlive -CommandName patrol-alive
$invokeId = $null
foreach ($l in $sendOut) { if ("$l" -match 'InvokeId:\s*(t-\S+)') { $invokeId = $Matches[1] } }
if (-not $invokeId) { Write-Host '[FATAL] 云助手 Send 失败'; Remove-Item $tmpAlive -Force; exit 1 }
Start-Sleep -Seconds 12
$invOut = powershell -NoProfile -ExecutionPolicy Bypass -File "$DEP\aliyun-ecs-rpc.ps1" Invoke -InvokeId $invokeId
Remove-Item $tmpAlive -Force
$aliveMap = Parse-KV ($invOut -join "`n")
foreach ($k in @('health', 'backend8147', 'nginx_test', 'redis_products')) {
    Write-Host ("  $k = " + $aliveMap[$k])
}
if ($aliveMap['health'] -ne '200') { Write-Host '  [DRIFT] decision/health 非 200'; $drift++ }
if ($aliveMap['backend8147'] -ne 'Running') { Write-Host '  [DRIFT] backend8147 服务非 Running'; $drift++ }
if ($aliveMap['nginx_test'] -ne 'ok') { Write-Host '  [DRIFT] nginx -t 失败'; $drift++ }
if ([int]$aliveMap['redis_products'] -lt 100000) { Write-Host '  [DRIFT] Redis zhuxiang:* 键数 <10万(主库可疑)'; $drift++ }

Write-Host '===== [2/4] 业务键面探测(ECS, 复用 consistency-probe-ecs) ====='
$sendOut = powershell -NoProfile -ExecutionPolicy Bypass -File "$DEP\aliyun-ecs-rpc.ps1" Send -CommandFile "$DEP\consistency-probe-ecs.ps1" -CommandName patrol-kv
$invokeId = $null
foreach ($l in $sendOut) { if ("$l" -match 'InvokeId:\s*(t-\S+)') { $invokeId = $Matches[1] } }
if (-not $invokeId) { Write-Host '[FATAL] 云助手 Send 失败'; exit 1 }
Start-Sleep -Seconds 20
$invOut = powershell -NoProfile -ExecutionPolicy Bypass -File "$DEP\aliyun-ecs-rpc.ps1" Invoke -InvokeId $invokeId
$ecs = Parse-KV ($invOut -join "`n")
foreach ($k in @('api_total', 'api_local', 'imgs', 'idx_mark', 'app_v', 'redis_products')) {
    Write-Host ("  $k = " + $ecs[$k])
}

Write-Host '===== [3/4] 绝对阈值检查 ====='
if ($ecs['api_ext'] -ne $null -and $ecs['api_ext'] -ne '0') {
    Write-Host ("  [DRIFT] 存在外链图 {0} 张(必须为 0)" -f $ecs['api_ext']); $drift++
} else { Write-Host '  [PASS] 外链图 = 0' }
if ($ecs['stock_zero'] -ne $null -and $ecs['stock_zero'] -ne '0') {
    Write-Host ("  [DRIFT] 存在零库存商品 {0} 款(必须为 0)" -f $ecs['stock_zero']); $drift++
} else { Write-Host '  [PASS] 零库存商品 = 0' }

Write-Host '===== [4/4] 汇总 ====='
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
if ($drift -eq 0) {
    $line = "[$stamp] RESULT: PASS 单端巡检一致"
    Write-Host $line -ForegroundColor Green
} else {
    $line = "[$stamp] RESULT: DRIFT x$drift 详见上方, 对照进化检查清单 A6/C 组处置"
    Write-Host $line -ForegroundColor Red
}
Add-Content (Join-Path $DEP 'patrol-single-last.log') $line -Encoding UTF8
exit $(if ($drift -eq 0) { 0 } else { 2 })
