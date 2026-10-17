# 国内拉取海外归档冷备 + 核对本机 assets 体量
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }

$EP = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/overseas-archive'
$DST = 'C:\tmp8147\overseas-archive'
New-Item -ItemType Directory -Force $DST | Out-Null

Write-Host '== [1] 本机 assets/data 现状(与海外 125MB/11MB 对比) =='
foreach ($d in 'assets', 'data', 'repositories', 'media') {
    $p = "C:\backend\$d"
    if (Test-Path $p) {
        $sz = (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
        Info ("$d : " + [math]::Round($sz / 1MB, 1) + 'MB, ' + @(Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue).Count + ' 文件')
    } else { Info "$d : 无目录" }
}

Write-Host '== [2] 下载海外归档 5 件(冷备, 不导入) =='
foreach ($f in 'redis-dump-20261017.rdb', 'overseas-data-20261017.tgz', 'overseas-code-20261017.tgz', 'overseas-dist-20261017.tgz', 'overseas-certs-20261017.tgz') {
    $out = Join-Path $DST $f
    if (Test-Path $out) { Info "$f 已存在跳过"; continue }
    curl.exe -s -o $out "$EP/$f" -m 1800
    $len = (Get-Item $out -ErrorAction SilentlyContinue).Length
    Info ("$f : " + [math]::Round($len / 1MB, 2) + 'MB')
}

Write-Host '== [3] 归档清单核对 =='
Get-ChildItem $DST | ForEach-Object { Info ($_.Name + '  ' + [math]::Round($_.Length / 1MB, 2) + 'MB') }
Write-Host 'OVERSEAS-ARCHIVE-PULLED'
