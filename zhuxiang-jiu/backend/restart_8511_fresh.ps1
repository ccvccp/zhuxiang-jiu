# restart 8511 (kill by port + relaunch with 127 links)
$ErrorActionPreference = "Stop"
$conns = Get-NetTCPConnection -LocalPort 8511 -State Listen -ErrorAction SilentlyContinue
if ($conns) {
  $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($p in $pids) { Stop-Process -Id $p -Force; Write-Host "killed $p" }
} else { Write-Host "no listener on 8511" }
Start-Sleep -Seconds 1
$line = (Get-Content ".env.example" -Encoding UTF8 | Select-String -Pattern '^#\s*HONGGUO_SHARE_URLS=(.+)').Line
$env:HONGGUO_SHARE_URLS = ($line -replace '^#\s*HONGGUO_SHARE_URLS=', '').Trim()
$cnt = ($env:HONGGUO_SHARE_URLS -split ',').Count
Write-Host "total links: $cnt"
Start-Process -FilePath python `
  -ArgumentList 'drama85_hongguo_worker.py','serve','--port','8511' `
  -WorkingDirectory (Get-Location).Path `
  -WindowStyle Hidden `
  -RedirectStandardOutput 'hg8511-b5-out2.log' `
  -RedirectStandardError 'hg8511-b5-err2.log'
Write-Host "8511 relaunched (fresh cache)"
