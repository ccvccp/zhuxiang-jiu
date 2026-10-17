# 急救4: 修 nssm Parameters\AppEnvironmentExtra(nssm 参数真实存储位)
$ErrorActionPreference = 'Continue'
$paramKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\backend8147\Parameters'
Write-Host '==> [1/4] Parameters 现状'
$k = Get-Item $paramKey
$v = @($k.GetValue('AppEnvironmentExtra'))
Write-Host ("    旧值({0}): [{1}]" -f $k.GetValueKind('AppEnvironmentExtra'), ($v -join '] ['))
Write-Host '==> [2/4] 删坏值 + 写 REG_MULTI_SZ'
Remove-ItemProperty -Path $paramKey -Name AppEnvironmentExtra -ErrorAction SilentlyContinue
New-ItemProperty -Path $paramKey -Name AppEnvironmentExtra -Value @('PYTHONUTF8=1', 'CHAT_LLM_MODE=shadow') -PropertyType MultiString -Force | Out-Null
$k2 = Get-Item $paramKey
Write-Host ("    修复后: [" + ($k2.GetValue('AppEnvironmentExtra') -join '] [') + ']')
Write-Host '==> [3/4] stop/start'
sc.exe stop backend8147 | Out-Null
Start-Sleep -Seconds 3
sc.exe start backend8147 | Out-Null
Start-Sleep -Seconds 8
Write-Host '==> [4/4] 健康+mode'
$h = curl.exe -s -m 10 http://127.0.0.1:8000/api/decision/health
Write-Host ("    health: " + $h)
$m = curl.exe -s -m 10 http://127.0.0.1:8000/api/chat/mode
Write-Host ("    mode: " + $m)
if (-not $h) { Get-Content C:\backend\logs\service-err.log -Tail 6 | ForEach-Object { Write-Host "    $_" }; exit 1 }
Write-Host 'ENV-FIX4-DONE'
