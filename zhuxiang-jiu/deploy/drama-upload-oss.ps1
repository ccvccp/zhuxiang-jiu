# 剧集成片 OSS 上传脚本(固化手工 oss-put 流程, 2026-10-17)
# 用途: 84号集成片(d84_{sid}_ep{n}.mp4)/73号成片(sv73_{sid}_mix.mp4)
#       → OSS zxjiu-videos bucket 根 → CDN v.zyjiu.com 可达
# 上传后输出组剧用 video_url 清单(POST /api/v1/dramas 的 episodes[].video_url)
#
# 用法:
#   .\drama-upload-oss.ps1 -SeriesId d84_4c2d8186ccee            # 上传该剧本全部集成片
#   .\drama-upload-oss.ps1 -Files .\ep1.mp4, .\ep2.mp4           # 指定文件
#   .\drama-upload-oss.ps1 -SeriesId d84_xxx -WhatIf             # 只列清单不上传
# 凭证: $env:ALIYUN_AK_ID/ALIYUN_AK_SECRET(缺省回退用户级注册表)
param(
    [string]$SeriesId = '',
    [string[]]$Files = @(),
    [string]$Bucket = 'zxjiu-videos',
    [string]$Endpoint = 'oss-cn-hangzhou.aliyuncs.com',
    [string]$CdnBase = 'https://v.zyjiu.com',
    [string]$VideoDir = '',          # 缺省 backend/sv73_videos
    [switch]$WhatIf
)
$ErrorActionPreference = 'Stop'

# ---------- 凭证 ----------
$ak = $env:ALIYUN_AK_ID
$sk = $env:ALIYUN_AK_SECRET
if (-not $ak -or -not $sk) {
    $ak = [Environment]::GetEnvironmentVariable('ALIYUN_AK_ID', 'User')
    $sk = [Environment]::GetEnvironmentVariable('ALIYUN_AK_SECRET', 'User')
}
if (-not $ak -or -not $sk) { throw '缺少 ALIYUN_AK_ID / ALIYUN_AK_SECRET(当前进程或用户级环境变量)' }

# ---------- 待传清单 ----------
if (-not $VideoDir) {
    $VideoDir = Join-Path $PSScriptRoot '..\backend\sv73_videos'
}
$targets = @()
if ($SeriesId) {
    # 精确集成片命名 d84_{sid}_ep{N}.mp4——排除中间产物(_l1_dh/_l1_voiced/headseg/tailseg)
    $targets = Get-ChildItem $VideoDir -Filter "$SeriesId`_ep*.mp4" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "^$([regex]::Escape($SeriesId))_ep\d+\.mp4$" } |
        Sort-Object { if ($_.Name -match '_ep(\d+)\.') { [int]$Matches[1] } else { 0 } }
    if (-not $targets) { throw "sv73_videos 下未找到 $SeriesId 的集成片(命名 d84_{sid}_ep{n}.mp4)" }
}
elseif ($Files.Count) {
    foreach ($f in $Files) {
        $p = Resolve-Path $f -ErrorAction Stop
        $targets += Get-Item $p
    }
}
else { throw '须指定 -SeriesId 或 -Files' }

Write-Host ("待上传 {0} 个文件(目标: {1} bucket 根, CDN {2}):" -f $targets.Count, $Bucket, $CdnBase)
$targets | ForEach-Object { Write-Host "  - $($_.Name)  $([math]::Round($_.Length/1MB,1))MB" }
if ($WhatIf) { Write-Host '[WhatIf] 仅列清单, 未上传'; exit 0 }

# ---------- OSS V1 签名(与 oss-put.ps1 同款) ----------
function Invoke-OssPut([string]$Path, [string]$Key) {
    $date = (Get-Date).ToUniversalTime().ToString('R')
    $contentType = 'video/mp4'
    $sts = "PUT`n`n$contentType`n$date`n/$Bucket/$Key"
    $hmac = New-Object System.Security.Cryptography.HMACSHA1
    $hmac.Key = [Text.Encoding]::UTF8.GetBytes($sk)
    $sig = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($sts)))
    $url = "https://$Bucket.$Endpoint/$Key"
    Invoke-WebRequest -Uri $url -Method Put -InFile $Path -ContentType $contentType `
        -Headers @{ 'Date' = $date; 'Authorization' = "OSS $ak`:$sig" } `
        -UseBasicParsing -TimeoutSec 600 | Out-Null
    return $url
}

# ---------- 上传 + 双重校验 + video_url 输出 ----------
$ok = 0; $fail = @()
foreach ($f in $targets) {
    $key = $f.Name   # bucket 根(CDN 源站直达, 与已发布剧集同约定)
    try {
        $null = Invoke-OssPut $f.FullName $key
        # HEAD 大小比对
        $head = Invoke-WebRequest -Uri "https://$Bucket.$Endpoint/$key" -Method Head `
            -UseBasicParsing -TimeoutSec 30
        if ($head.Headers['Content-Length'] -ne $f.Length) {
            throw "OSS HEAD 大小不一致(local=$($f.Length) remote=$($head.Headers['Content-Length']))"
        }
        # CDN 可达性(200/206)
        $cdnCode = & curl.exe -s -o NUL -w '%{http_code}' -m 20 -I "$CdnBase/$key"
        if ($cdnCode -notin @('200', '206')) {
            throw "CDN HEAD $cdnCode(可能源站刷新延迟, 稍后重验)"
        }
        Write-Host "[OK] $key  OSS=大小一致  CDN=$cdnCode"
        $ok++
    }
    catch {
        Write-Host "[FAIL] $key : $($_.Exception.Message)" -ForegroundColor Red
        $fail += $key
    }
}

Write-Host ''
Write-Host "===== 组剧 video_url 清单($ok/$($targets.Count) 成功) ====="
foreach ($f in $targets) {
    if ($f.Name -notin $fail) { Write-Host "$CdnBase/$($f.Name)" }
}
if ($fail.Count) {
    Write-Host "失败 $($fail.Count) 个: $($fail -join ', ')"
    exit 1
}
Write-Host 'DRAMA-OSS-UPLOAD-DONE'
