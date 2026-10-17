# 竹韵话术知识库生产入库(下载 seed py -> redis 持久态执行 -> 冒烟)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }
$T = 'C:\tmp8147\arch'
New-Item -ItemType Directory -Force $T | Out-Null

Write-Host '== [1/3] 下载种子脚本(落 C:\backend 保 sys.path) =='
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/zhuyun/seed_zhuyun_scripts.py' -OutFile "C:\backend\seed_zhuyun_scripts.py" -UseBasicParsing
Info ('py 大小: ' + (Get-Item "C:\backend\seed_zhuyun_scripts.py").Length)

Write-Host '== [2/3] 入库(redis 持久态, env 对齐服务口径) =='
$env:STORE_MODE = 'redis'; $env:LOCK_MODE = 'redis'; $env:SEED_DRYRUN = '0'
$env:REDIS_URL = 'redis://127.0.0.1:6379/0?protocol=2'
Push-Location C:\backend
$out = & C:\Python312\python.exe "C:\backend\seed_zhuyun_scripts.py" 2>&1 | Out-String
Pop-Location
Info ($out.Trim())

Write-Host '== [3/3] 生产库计数核验 =='
$pub = & C:\redis\redis-cli.exe --raw SCARD 'zhuxiang:knowledge:entry:index:published'
Info ('published 条目数: ' + $pub)
$k = & C:\redis\redis-cli.exe KEYS 'zhuxiang:knowledge:entry:*'
Info ('knowledge entry 键数: ' + @($k).Count)
if ($out -notmatch 'PUBLISHED \d+ / FAILED \d+') { Info '!! 输出异常'; exit 1 }
if ($out -match 'SMOKE') { Info 'SMOKE 段已在上方输出' }
Remove-Item "C:\backend\seed_zhuyun_scripts.py" -Force -ErrorAction SilentlyContinue
Write-Host 'ZHUYUN-SEED-DONE'
