$h1 = curl.exe -s -o NUL -w '%{http_code}' -m 15 http://127.0.0.1:8000/api/health
Write-Host ("local /api/health: " + $h1)
$h2 = curl.exe -sk -o NUL -w '%{http_code}' -m 15 https://zxjiu.com/api/health
Write-Host ("public /api/health: " + $h2)
$h3 = curl.exe -sk -o NUL -w '%{http_code}' -m 15 https://zxjiu.com/
Write-Host ("public /: " + $h3)
Get-Content C:\backend\logs\service-err.log -Tail 15 -ErrorAction SilentlyContinue | Select-String 'GET /api/health|health' | Select-Object -Last 3 | ForEach-Object { Write-Host $_.Line }
Write-Host 'RECHECK-DONE'
