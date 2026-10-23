# sv73 自动发布完整性验证(新机侧) —— 明日 18:05 后运行
# 用法(PC): powershell -File deploy\aliyun-ecs-rpc.ps1 Send -CommandFile deploy\autopublish_verify.ps1 -CommandName auto-verify -RegionId cn-beijing -InstanceId i-2zebz5dnx9903xdsbi7m
$ErrorActionPreference = 'Continue'
Write-Host '== 1. autopublish.log 今日段(编排器) =='
if (Test-Path D:\channels-bot\autopublish.log) {
    Get-Content D:\channels-bot\autopublish.log | Where-Object { $_ -match (Get-Date -Format 'yyyy-MM-dd') } | Select-Object -Last 25 | ForEach-Object { Write-Host ("  " + $_) }
} else { Write-Host '  文件不存在' }

Write-Host '== 2. auto\run_*.log(bot 实跑) =='
Get-ChildItem D:\channels-bot\auto -Filter 'run_*.log' -EA SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 4 | ForEach-Object {
    Write-Host ("  --- " + $_.Name + " ---")
    Get-Content $_.FullName -Tail 6 | ForEach-Object { Write-Host ("  " + $_) }
}

Write-Host '== 3. 任务状态 =='
foreach ($tn in @('zx-sv73-publish-process','zx-sv73-autopublish')) {
    $i = Get-ScheduledTask -TaskName $tn | Get-ScheduledTaskInfo
    Write-Host ("  " + $tn + " 上次:" + $i.LastRunTime + " 结果:" + $i.LastTaskResult + " 下次:" + $i.NextRunTime)
}

Write-Host '== 4. 登录态 =='
foreach ($f in @('login_state.json','douyin_login_state.json')) {
    $j = Get-Content "D:\channels-bot\$f" -Raw | ConvertFrom-Json
    $t = if ($j.at) { [DateTimeOffset]::FromUnixTimeMilliseconds($j.at).LocalDateTime.ToString('MM-dd HH:mm') } else { '-' }
    Write-Host ("  " + $f + " ok=" + $j.ok + " at=" + $t)
}

Write-Host '== 5. 队列余量(API) =='
try {
    $login = Invoke-RestMethod -Uri 'https://zyjiu.com/api/auth/login' -Method Post -ContentType 'application/json' -Body (@{ phone='13800000002'; password='test123456' } | ConvertTo-Json) -TimeoutSec 20
    $h = @{ Authorization = "Bearer $($login.accessToken)"; 'X-Role' = 'admin' }
    $pend = Invoke-RestMethod -Uri 'https://zyjiu.com/api/promo/rpa/pending' -Headers $h -TimeoutSec 30
    $items = @($pend.data)
    Write-Host ("  剩余待发: " + $items.Count)
    $items | ForEach-Object { Write-Host ("    " + $_.contentId + " " + $_.platform + " " + $_.shortCode) }
} catch { Write-Host ("  API fail: " + $_.Exception.Message) }

Write-Host '== 6. 告警与供给 =='
foreach ($f in @('PUBLISH_FAIL_ALERT.flag','PENDING_NO_VIDEO.flag')) { Write-Host ("  " + $f + " : " + (Test-Path "D:\channels-bot\$f")) }
Get-ChildItem D:\channels-bot\videos -EA SilentlyContinue | ForEach-Object { Write-Host ("  [video] " + $_.Name + " " + [math]::Round($_.Length/1MB,2) + "MB") }
Write-Host 'AUTO-VERIFY-DONE'
