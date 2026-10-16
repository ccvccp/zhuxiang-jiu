# P4-10 生产部署脚本(在 ECS 上经云助手执行; 由 aliyun-ecs-rpc.ps1 Send 投递)
# 流程: 备份 -> 下载新代码 -> 解压覆盖 -> import 冒烟(失败自动回滚, 服务不停)
#       -> .env 追加 DPO 块(幂等) -> nssm 环境注入(保留既有项) -> 重启 -> 健康与日志验证
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }

$OSS = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/backend-migrate'
$TGZ = 'C:\tmp8147\backend-code-v4.tgz'
$BAK = 'C:\tmp8147\backend-code-bak-p410.tgz'
$PyExe = 'C:\Python312\python.exe'
$Nssm = 'C:\nginx\nssm.exe'

Step '[1/8] 备份 .env 与 py 代码树'
Copy-Item C:\backend\.env C:\backend\.env.bak-p410 -Force
Info ('.env 备份: ' + (Get-Item C:\backend\.env.bak-p410).Length + 'B')
Push-Location C:\backend
try { cmd /c 'tar -czf C:\tmp8147\backend-code-bak-p410.tgz main.py requirements.txt *.py core routes services repositories scripts 2>nul' } finally { Pop-Location }
Info ('代码备份: ' + [math]::Round((Get-Item $BAK).Length / 1KB, 0) + 'KB')

Step '[2/8] 下载新代码包'
Invoke-WebRequest "$OSS/backend-code-v4.tgz" -OutFile $TGZ -UseBasicParsing
Info ('下载: ' + [math]::Round((Get-Item $TGZ).Length / 1MB, 1) + 'MB')

Step '[3/8] 解压覆盖 C:\backend(仅 py/requirements, 数据与媒体不动)'
Push-Location C:\backend
try { cmd /c "tar -xzf `"$TGZ`" -C C:\backend 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Info '!! 解压失败'; exit 1 }
Info ('services py 数: ' + (Get-ChildItem C:\backend\services -Filter *.py | Measure-Object).Count)

Step '[4/8] import 冒烟(失败自动回滚, 不动运行中服务)'
$env:PYTHONUTF8 = '1'
Push-Location C:\backend
$imp = 'import sys; import main; print("IMPORT_OK routes=" + str(len(main.app.routes)))'
$out = & $PyExe -c $imp 2>&1
$impRc = $LASTEXITCODE
Pop-Location
Info ("import 退出码: $impRc  $out")
if ($impRc -ne 0) {
    Info '!! import 失败, 回滚代码备份(服务未受影响)'
    Push-Location C:\backend
    try { cmd /c "tar -xzf `"$BAK`" -C C:\backend 2>&1" } finally { Pop-Location }
    Get-Content C:\backend\logs\service-err.log -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
    exit 1
}

Step '[5/8] .env 追加 DPO 块(幂等)'
$envText = Get-Content C:\backend\.env -Raw
if ($envText -notmatch '(?m)^DPO_COLLECT_AUTO=') {
    Add-Content C:\backend\.env -Value "`r`nDPO_COLLECT_AUTO=on`r`nDPO_COLLECT_INTERVAL_MIN=1440"
    Info '已追加 DPO_COLLECT_AUTO=on'
} else {
    Info 'DPO_COLLECT_AUTO 已存在, 跳过'
}

Step '[6/8] nssm 环境注入(解析 dump 保留既有项, 并入 DPO 变量)'
$dump = cmd /c "$Nssm dump backend8147" 2>&1 | Out-String
$extraLine = ($dump -split "`n" | Where-Object { $_ -match 'AppEnvironmentExtra' } | Select-Object -First 1)
$current = ''
if ($extraLine -and $extraLine -match ':\s*(.+)$') { $current = $Matches[1].Trim() }
Info ("当前 AppEnvironmentExtra: [$current]")
$parts = @($current -split '\s+' | Where-Object { $_ })
foreach ($pair in @('DPO_COLLECT_AUTO=on', 'DPO_COLLECT_INTERVAL_MIN=1440')) {
    $name = ($pair -split '=')[0]
    if (-not ($parts | Where-Object { $_ -like "$name=*" })) { $parts += $pair }
}
$newExtra = ($parts -join ' ')
Info ("目标 AppEnvironmentExtra: [$newExtra]")
cmd /c "$Nssm set backend8147 AppEnvironmentExtra $newExtra" | Out-Null

Step '[7/8] 重启服务'
cmd /c "$Nssm restart backend8147" | Out-Null
Start-Sleep -Seconds 12
$svcState = (Get-Service backend8147 -ErrorAction SilentlyContinue).Status
Info ("服务状态: $svcState")
$code = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/health
Info ("本地 /api/health: $code")
if ($code -ne '200') {
    Info '!! 健康检查未过, stderr 尾部:'
    Get-Content C:\backend\logs\service-err.log -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
    exit 1
}

Step '[8/8] DPO 调度启动验证'
Start-Sleep -Seconds 3
$logHit = (Get-Content C:\backend\logs\service-out.log -Tail 100 -ErrorAction SilentlyContinue | Select-String 'dpo_collect_scheduler started|dpo_pairs_collected') -join ' | '
if ($logHit) { Info ("日志验证: $logHit") } else { Info '警告: 未捕到 dpo 日志行(stdout 尾部):'; Get-Content C:\backend\logs\service-out.log -Tail 10 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ } }
Write-Host 'P410-DEPLOY-DONE'
