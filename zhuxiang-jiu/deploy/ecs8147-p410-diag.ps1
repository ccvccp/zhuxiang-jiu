# P4-10 诊断: 直接跑 import 冒烟, stderr 落文件规避 PS5 ErrorRecord 雷
$ProgressPreference = 'SilentlyContinue'
$env:PYTHONUTF8 = '1'
cmd /c "cd /d C:\backend && C:\Python312\python.exe -c ""import sys; import main; print('IMPORT_OK routes=' + str(len(main.app.routes)))"" > C:\tmp8147\imp-diag.log 2>&1"
Write-Host ("EXIT=" + $LASTEXITCODE)
Write-Host '--- imp-diag.log tail 40 ---'
Get-Content C:\tmp8147\imp-diag.log -Tail 40 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
Write-Host '--- service status ---'
Write-Host ("backend8147: " + (Get-Service backend8147 -ErrorAction SilentlyContinue).Status)
$code = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/health
Write-Host ("health: " + $code)
Write-Host 'DIAG-DONE'
