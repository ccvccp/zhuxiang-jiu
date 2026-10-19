﻿﻿﻿# 诊断: OSS tgz 下载与解压
$ProgressPreference = 'SilentlyContinue'
$code = & curl.exe -s -o C:\tmp8147\drama-rr.tgz -w '%{http_code}' "https://zxjiu-videos.oss-cn-wulanchabu.aliyuncs.com/deploy/drama-rr-20261017.tgz"
Write-Host "HTTP=$code SIZE=$((Get-Item C:\tmp8147\drama-rr.tgz -ErrorAction SilentlyContinue).Length)"
Write-Host '--- head bytes ---'
$bytes = [System.IO.File]::ReadAllBytes('C:\tmp8147\drama-rr.tgz')[0..15]
Write-Host (($bytes | ForEach-Object { $_.ToString('X2') }) -join ' ')
Write-Host '--- tar tzf ---'
tar -tzf C:\tmp8147\drama-rr.tgz
Write-Host '--- service state ---'
$s = Get-Service backend8147 -ErrorAction SilentlyContinue
if ($s) { Write-Host "svc=$($s.Status)" } else { & cmd /c 'C:\nginx\nssm.exe status backend8147' }
$h2 = & curl.exe -s -o NUL -w '%{http_code}' http://127.0.0.1:8147/api/decision/health
Write-Host "health-now=$h2"
Write-Host 'DIAG-DONE'
