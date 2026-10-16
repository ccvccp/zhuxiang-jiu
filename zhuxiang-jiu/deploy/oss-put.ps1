# OSS PUT 上传(本地执行; 需 $env:ALIYUN_AK_ID / ALIYUN_AK_SECRET)
# 用途: P4-10 生产部署——上传 backend-code-v4.tgz 到迁移基线同目录
#   OSS: zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/backend-migrate/
# 签名: OSS V1 (HMAC-SHA1), 对齐 aliyun-ecs-rpc.ps1 的手工签名风格
param(
    [string]$File = '',
    [string]$Key = 'deploy/backend-migrate/backend-code-v4.tgz',
    [string]$Bucket = 'zxjiu-videos',
    [string]$Endpoint = 'oss-cn-hangzhou.aliyuncs.com'
)
$ErrorActionPreference = 'Stop'
$ak = $env:ALIYUN_AK_ID; $sk = $env:ALIYUN_AK_SECRET
if (-not $ak -or -not $sk) { throw 'set $env:ALIYUN_AK_ID / ALIYUN_AK_SECRET first' }
if (-not $File) { $File = Join-Path $PSScriptRoot 'dist\backend-code-v4.tgz' }
if (-not (Test-Path $File)) { throw "file not found: $File" }

$date = (Get-Date).ToUniversalTime().ToString('R')
$contentType = 'application/octet-stream'
# StringToSign = VERB \n Content-MD5 \n Content-Type \n Date \n CanonicalizedResource
$sts = "PUT`n`n$contentType`n$date`n/$Bucket/$Key"
$hmac = New-Object System.Security.Cryptography.HMACSHA1
$hmac.Key = [Text.Encoding]::UTF8.GetBytes($sk)
$sig = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($sts)))
$url = "https://$Bucket.$Endpoint/$Key"

$sw = [Diagnostics.Stopwatch]::StartNew()
Invoke-WebRequest -Uri $url -Method Put -InFile $File -ContentType $contentType `
    -Headers @{ 'Date' = $date; 'Authorization' = "OSS $ak`:$sig" } -UseBasicParsing -TimeoutSec 600 | Out-Null
$sw.Stop()
$mb = [math]::Round((Get-Item $File).Length / 1MB, 1)
Write-Host ("OSS-PUT-OK $Key  ${mb}MB  " + [math]::Round($sw.Elapsed.TotalSeconds, 1) + "s")
# 回读校验(HEAD Content-Length 比对)
$head = Invoke-WebRequest -Uri $url -Method Head -UseBasicParsing -TimeoutSec 30
if ($head.Headers['Content-Length'] -eq (Get-Item $File).Length) {
    Write-Host 'OSS-HEAD-OK (大小一致)'
} else {
    Write-Host ('OSS-HEAD-MISMATCH local=' + (Get-Item $File).Length + ' remote=' + $head.Headers['Content-Length']); exit 1
}
