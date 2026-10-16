# P4-10 修复续跑: pip 补装缺依赖 -> import 冒烟 -> (通过则) .env/nssm/重启/验证
# 教训修正: ①生产缺 sqlalchemy(requirements Day1 新增, 迁移日装的是旧版清单)
#           ②PS5 stderr-as-ErrorRecord 雷: python 输出一律 cmd /c 落文件再读
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }
$PyExe = 'C:\Python312\python.exe'
$Nssm = 'C:\nginx\nssm.exe'

Step '[1/6] pip 补装依赖(清华源, 幂等)'
$env:PYTHONUTF8 = '1'
$env:PIP_INDEX_URL = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
cmd /c "cd /d C:\ && C:\Python312\python.exe -m pip install -r C:\backend\requirements.txt --no-warn-script-location --progress-bar off > C:\tmp8147\pip-p410.log 2>&1"
Info ("pip 退出码: " + $LASTEXITCODE)
Get-Content C:\tmp8147\pip-p410.log -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
if ($LASTEXITCODE -ne 0) { Write-Host 'P410-FIX-FAIL pip'; exit 1 }

Step '[2/6] import 冒烟(stderr 落文件)'
cmd /c "cd /d C:\backend && C:\Python312\python.exe -c ""import sys; import main; print('IMPORT_OK routes=' + str(len(main.app.routes)))"" > C:\tmp8147\imp-diag2.log 2>&1"
$impRc = $LASTEXITCODE
Info ("import 退出码: " + $impRc)
Get-Content C:\tmp8147\imp-diag2.log -Tail 8 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
if ($impRc -ne 0) { Write-Host 'P410-FIX-FAIL import(服务未动, 仍跑旧代码)'; exit 1 }

Step '[3/6] .env 追加 DPO 块(幂等)'
$envText = Get-Content C:\backend\.env -Raw
if ($envText -notmatch '(?m)^DPO_COLLECT_AUTO=') {
    Add-Content C:\backend\.env -Value "`r`nDPO_COLLECT_AUTO=on`r`nDPO_COLLECT_INTERVAL_MIN=1440"
    Info '已追加 DPO_COLLECT_AUTO=on'
} else { Info '已存在, 跳过' }

Step '[4/6] nssm 环境注入(dump 解析保留既有项)'
$dump = (cmd /c "$Nssm dump backend8147" 2>&1) -join "`n"
$extraLine = ($dump -split "`n" | Where-Object { $_ -match 'AppEnvironmentExtra' } | Select-Object -First 1)
$current = ''
if ($extraLine -and $extraLine -match ':\s*(.+?)\s*$') { $current = $Matches[1].Trim() }
Info ("当前 AppEnvironmentExtra: [$current]")
$parts = @($current -split '\s+' | Where-Object { $_ })
foreach ($pair in @('DPO_COLLECT_AUTO=on', 'DPO_COLLECT_INTERVAL_MIN=1440')) {
    $name = ($pair -split '=')[0]
    if (-not ($parts | Where-Object { $_ -like ($name + '=*') })) { $parts += $pair }
}
$newExtra = ($parts -join ' ')
Info ("目标 AppEnvironmentExtra: [$newExtra]")
cmd /c "$Nssm set backend8147 AppEnvironmentExtra $newExtra" | Out-Null

Step '[5/6] 重启服务'
cmd /c "$Nssm restart backend8147" | Out-Null
Start-Sleep -Seconds 12
Info ("服务状态: " + (Get-Service backend8147 -ErrorAction SilentlyContinue).Status)
$code = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/health
Info ("本地 /api/health: " + $code)
if ($code -ne '200') {
    Info '!! 健康未过, stderr 尾部:'
    Get-Content C:\backend\logs\service-err.log -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
    exit 1
}

Step '[6/6] DPO 调度启动验证'
Start-Sleep -Seconds 3
$logHit = (Get-Content C:\backend\logs\service-out.log -Tail 100 -ErrorAction SilentlyContinue | Select-String 'dpo_collect_scheduler started|dpo_pairs_collected') -join ' | '
if ($logHit) { Info ("日志验证: " + $logHit) } else { Info '警告: 未捕到 dpo 日志行, stdout 尾部:'; Get-Content C:\backend\logs\service-out.log -Tail 10 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ } }
$ext = curl.exe -sk -o NUL -w '%{http_code}' -m 8 https://zxjiu.com/api/health
Info ("公网 https://zxjiu.com/api/health: " + $ext)
Write-Host 'P410-DEPLOY-DONE'
