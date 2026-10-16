# P4-10 终验: 确认部署后全局状态(401=鉴权语义正确, 不再误判)
$ProgressPreference = 'SilentlyContinue'
Write-Host ("backend8147: " + (Get-Service backend8147 -ErrorAction SilentlyContinue).Status)
$h1 = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/health
Write-Host ("local /api/health: " + $h1 + "  (401=鉴权层在位, 属正常)")
$h2 = curl.exe -sk -o NUL -w '%{http_code}' -m 10 https://zxjiu.com/api/health
Write-Host ("public /api/health: " + $h2)
$nssm = (cmd /c "C:\nginx\nssm.exe dump backend8147" 2>&1) -join ' '
Write-Host ("nssm env: " + ([regex]::Match($nssm, 'AppEnvironmentExtra[^\r\n]*').Value))
Write-Host '--- DPO/调度 日志证据(service-err 尾部匹配) ---'
Get-Content C:\backend\logs\service-err.log -Tail 200 -ErrorAction SilentlyContinue |
    Select-String 'dpo_collect_scheduler started|dpo_pairs_collected|Application startup complete' |
    Select-Object -Last 4 | ForEach-Object { Write-Host $_.Line }
Write-Host '--- .env DPO 段 ---'
Get-Content C:\backend\.env | Select-String 'DPO' | ForEach-Object { Write-Host $_.Line }
Write-Host 'P410-VERIFY-DONE'
