param(
    [string]$Note = ''
)
# [自动发布·推送] 本地源头一键发布: 打包站点静态 → OSS 公共读 → latest.json 原子指针
# 用法: powershell -File deploy\release-push.ps1 -Note "发布说明"
$ErrorActionPreference = 'Stop'
$AK = $env:ALIYUN_AK_ID; $SK = $env:ALIYUN_AK_SECRET
if (-not $AK -or -not $SK) { throw 'set ALIYUN_AK_ID/SECRET first' }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = Split-Path $PSScriptRoot -Parent
$BKT = 'zxjiu-videos'; $EP = "https://$BKT.oss-cn-hangzhou.aliyuncs.com"

$gitHash = (git -C $repo rev-parse --short HEAD).Trim()
$ver = "$(Get-Date -Format 'yyyyMMdd-HHmm')-$gitHash"
$stage = Join-Path $env:TEMP "zxrel-$ver"
if (Test-Path $stage) { Remove-Path 2>$null; Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null

# 打包站点静态(精确选择): 根 html(排除 .bak) + js/css/images + 根图标类
$site = "$stage\site"
New-Item -ItemType Directory -Path $site | Out-Null
Get-ChildItem $repo -File | Where-Object {
    $_.Name -match '\.(html?|ico|png|txt)$' -and $_.Name -notmatch '\.bak'
} | ForEach-Object { Copy-Item $_.FullName $site }
foreach ($d in @('js', 'css', 'images')) {
    if (Test-Path "$repo\$d") { robocopy "$repo\$d" "$site\$d" /E /XF *.bak /NFL /NDL /NJH /NJS | Out-Null }
    else { Write-Host "warn: $d 不存在, 跳过" }
}
$n = (Get-ChildItem $site -Recurse -File).Count
Write-Host ("site files: " + $n)

$zip = "$env:TEMP\zx-site-$ver.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$stage\site\*" -DestinationPath $zip -CompressionLevel Fastest
$sha = (Get-FileHash $zip -Algorithm SHA256).Hash
Write-Host ("release: {0}  {1:N1}MB  sha={2}" -f $ver, ((Get-Item $zip).Length/1MB), $sha)

function Oss-Put([string]$file, [string]$objKey, [bool]$publicRead) {
    $ctype = 'application/octet-stream'
    $date = (Get-Date).ToUniversalTime().ToString('R')
    # 自定义 x-oss-* header 必须参与签名(CanonicalizedOSSHeaders: 小写key+换行)
    $ossHdr = ''
    if ($publicRead) { $ossHdr = "x-oss-object-acl:public-read`n" }
    $str = "PUT`n`n$ctype`n$date`n$ossHdr/$BKT/$objKey"
    $h = New-Object System.Security.Cryptography.HMACSHA1
    $h.Key = [Text.Encoding]::UTF8.GetBytes($SK)
    $sig = [Convert]::ToBase64String($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($str)))
    $hdrs = @("-H", "Date: $date", "-H", "Content-Type: $ctype", "-H", "Authorization: OSS ${AK}:$sig")
    if ($publicRead) { $hdrs += @("-H", "x-oss-object-acl: public-read") }
    $code = & curl.exe -s -o NUL -w '%{http_code}' -X PUT @hdrs --data-binary "@$file" "$EP/$objKey"
    if ($code -ne '200') { throw "OSS PUT $objKey -> $code" }
    Write-Host "  PUT $objKey 200$(if ($publicRead) { ' (public-read)' })"
}

Oss-Put $zip "deploy/release/zx-site-$ver.zip" $true
$manifest = @{
    version = $ver
    zip = "deploy/release/zx-site-$ver.zip"
    sha256 = $sha
    git_commit = $gitHash
    published_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    note = $Note
} | ConvertTo-Json -Compress
$tmpJson = "$env:TEMP\zxrel-latest.json"
[IO.File]::WriteAllText($tmpJson, $manifest, (New-Object Text.UTF8Encoding $false))
Oss-Put $tmpJson 'deploy/release/latest.json' $true
Remove-Item $stage, $zip, $tmpJson -Recurse -Force -EA SilentlyContinue
Write-Host "RELEASE-PUSHED version=$ver"
