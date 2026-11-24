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
# ---- 生产 API 凭据(禁止硬编码; 依据 .trae/rules/security-hard-coded.md 一.2) ----
# 环境变量 ZX_API_PHONE / ZX_API_PASSWORD, 或 ZX_CRED_FILE 指向的仓外 JSON。
# 缺失即 throw, 由下方 catch 记为 "API fail"(fail-closed, 不打印口令)。
# 读取顺序(2026-11-24 fix placeholder incident):
#   (1) ZX_CRED_FILE -> external JSON with locked ACL (highest priority)
#   (2) env ZX_API_PHONE / ZX_API_PASSWORD (only when neither is a template placeholder)
# Background: services.exe caches the process env block; after clearing the registry,
#   SYSTEM scheduled tasks could still read placeholders ("your phone/password"), which
#   caused 12 consecutive days of login 404. File-first plus a placeholder guard removes
#   that failure mode. fail-closed: never hardcode a secret; throw when no valid credential.
function Get-Sv73Cred {
    $ph = $null; $pw = $null
    # Placeholder guard: empty value or template wording is treated as invalid
    $isPlaceholder = { param($v) (-not $v) -or ($v -match '你的|手机号|密码|xxx|XXX') }
    if ($env:ZX_CRED_FILE -and (Test-Path $env:ZX_CRED_FILE)) {
        $o = ([Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($env:ZX_CRED_FILE)) | ConvertFrom-Json)
        $ph = $o.phone; $pw = $o.password
    }
    if (-not ($ph -and $pw) -and -not (& $isPlaceholder $env:ZX_API_PHONE) -and -not (& $isPlaceholder $env:ZX_API_PASSWORD)) {
        $ph = $env:ZX_API_PHONE; $pw = $env:ZX_API_PASSWORD
    }
    if (-not ($ph -and $pw)) { throw 'ZX_CRED_FILE missing or credential invalid (env is placeholder), fail-closed' }
    return @{ phone = $ph; password = $pw }
}
try {
    $login = Invoke-RestMethod -Uri 'https://zyjiu.com/api/auth/login' -Method Post -ContentType 'application/json' -Body ((Get-Sv73Cred) | ConvertTo-Json) -TimeoutSec 20
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
