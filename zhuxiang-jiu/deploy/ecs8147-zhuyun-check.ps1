﻿﻿﻿# 竹韵话术冲突检查(下载检查器 -> redis 持久态执行 -> base64 报告)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }
$T = 'C:\tmp8147\arch'
New-Item -ItemType Directory -Force $T | Out-Null

Write-Host '== [1/2] 下载检查器+种子脚本(落 C:\backend 保 sys.path) =='
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/zhuyun/check_zhuyun_scripts.py' -OutFile "C:\backend\check_zhuyun_scripts.py" -UseBasicParsing
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/zhuyun/seed_zhuyun_scripts.py' -OutFile "C:\backend\seed_zhuyun_scripts.py" -UseBasicParsing
Info ('check py 大小: ' + (Get-Item "C:\backend\check_zhuyun_scripts.py").Length)
Info ('seed py 大小: ' + (Get-Item "C:\backend\seed_zhuyun_scripts.py").Length)

Write-Host '== [2/2] 执行检查(redis 持久态, env 对齐服务口径) =='
$env:STORE_MODE = 'redis'; $env:LOCK_MODE = 'redis'
$env:REDIS_URL = 'redis://127.0.0.1:6379/0?protocol=2'
Push-Location C:\backend
$out = & C:\Python312\python.exe "C:\backend\check_zhuyun_scripts.py" 2>&1 | Out-String
Pop-Location
Info ($out.Trim())

if (-not $out.Contains('CHECK-DONE')) { Info '!! 检查器异常退出'; exit 1 }
$olines = $out -split "`r?`n"
$mi = -1
for ($x = 0; $x -lt $olines.Count; $x++) { if ($olines[$x] -match '^REPORT-B64:$') { $mi = $x; break } }
$b64 = ''
if ($mi -ge 0) {
    $b64 = ($olines[($mi + 1)..($olines.Count - 1)] | Where-Object { $_ -match '^[A-Za-z0-9+/=]+$' }) -join ''
}
if ($b64) {
    try {
        $bytes = [Convert]::FromBase64String($b64)
        $report = [Text.Encoding]::UTF8.GetString($bytes)
        [IO.File]::WriteAllText("$T\zhuyun-check-report.txt", $report, [Text.Encoding]::UTF8)
        Info ('报告已落盘: ' + "$T\zhuyun-check-report.txt")
    } catch { Info ('!! b64 解码失败: ' + $_.Exception.Message) }
} else { Info '!! 未捕获 REPORT-B64 段' }
Remove-Item "C:\backend\check_zhuyun_scripts.py" -Force -ErrorAction SilentlyContinue
Remove-Item "C:\backend\seed_zhuyun_scripts.py" -Force -ErrorAction SilentlyContinue
Write-Host 'ZHUYUN-CHECK-DONE'
