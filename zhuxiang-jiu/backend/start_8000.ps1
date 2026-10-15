# start main site on 8000
$ErrorActionPreference = "Stop"
$wd = (Get-Location).Path
Start-Process -FilePath python `
  -ArgumentList '-m','uvicorn','main:app','--host','127.0.0.1','--port','8000' `
  -WorkingDirectory $wd `
  -WindowStyle Hidden `
  -RedirectStandardOutput 'uvi-out.log' `
  -RedirectStandardError 'uvi-err.log'
Write-Host "8000 starting..."
Start-Sleep -Seconds 6
try {
  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/activity/list' -UseBasicParsing -TimeoutSec 8
  Write-Host ("8000 up: " + $r.StatusCode)
} catch { Write-Host ("8000 probe: " + $_.Exception.Message) }
