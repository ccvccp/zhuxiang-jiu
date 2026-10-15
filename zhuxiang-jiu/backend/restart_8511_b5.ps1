# batch5 feed: restart 8511 (97+30=127 links)
$ErrorActionPreference = "Stop"
$line = (Get-Content ".env.example" -Encoding UTF8 | Select-String -Pattern '^#\s*HONGGUO_SHARE_URLS=(.+)').Line
if (-not $line) { throw "HONGGUO_SHARE_URLS not found in env.example" }
$old = ($line -replace '^#\s*HONGGUO_SHARE_URLS=', '').Trim()
$codes = @(
  'VNqwTNR5Br0','sM6wBaxyDuQ','VCWlJ90zwtY','07PvIPkedrI','o4Lxjo0-E8I',
  'czpF_TlBi1U','F4HVoC3rD_0','RXlMAb_hfZg','A5O5b-173Rc','4hJw6bKtPWY',
  '-6HNExWpC3w','ai-HSo9XgGc','JPKwJ8U37MU','kpvyEDUOA8A','TgPu06HZ2vw',
  'Nh89IJY5DM8','KdkJISXJXFc','2UyzkmTHCgo','Ly-mATadcG4','ZbayjhthPTM',
  '0nLusoYNM3o','1vAo_UGHKPI','6L5mBkl4GZE','u8eSTK5opFg','K8WiUB57eF8',
  '5MY4hYrZ_7k','Vmb7LViyYSs','HGF66zS9OYw','g5d0McpSZfQ','0WqJ__ai1zc'
)
$new = ($codes | ForEach-Object { "https://novelquickapp.com/s/$_/" }) -join ','
$env:HONGGUO_SHARE_URLS = "$old,$new"
$cnt = ($env:HONGGUO_SHARE_URLS -split ',').Count
Write-Host "total links: $cnt"
try { Stop-Process -Id 53392 -Force -ErrorAction Stop; Write-Host "stopped 53392" } catch { Write-Host "53392 already gone" }
Start-Sleep -Seconds 1
Start-Process -FilePath python `
  -ArgumentList 'drama85_hongguo_worker.py','serve','--port','8511' `
  -WorkingDirectory (Get-Location).Path `
  -WindowStyle Hidden `
  -RedirectStandardOutput 'hg8511-b5-out.log' `
  -RedirectStandardError 'hg8511-b5-err.log'
Write-Host "8511 started, logs: hg8511-b5-out/err.log"
Start-Sleep -Seconds 6
try {
  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8511/health' -UseBasicParsing -TimeoutSec 5
  Write-Host "health: $($r.StatusCode)"
} catch {
  try {
    $r2 = Invoke-WebRequest -Uri 'http://127.0.0.1:8511/' -UseBasicParsing -TimeoutSec 5
    Write-Host "root: $($r2.StatusCode)"
  } catch { Write-Host "probe failed: $($_.Exception.Message)" }
}
