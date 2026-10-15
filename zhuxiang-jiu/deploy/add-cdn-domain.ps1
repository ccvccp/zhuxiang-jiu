﻿# ============================================================
# add-cdn-domain.ps1 · 阿里云 CDN API 直调: 添加加速域名 + 自动配 CNAME
# ============================================================
# 用途: 绕过控制台向导(渲染 bug), 通过 OpenAPI 完成:
#   1. AddCdnDomain        v.zyjiu.com (视音频点播/stream, 仅中国内地, OSS 源站)
#   2. DescribeUserDomains 轮询拿分配的 CNAME 目标 + 域名状态
#   3. AddDomainRecord     zxjiu.com 加 v 的 CNAME 记录 (alidns)
#
# 用法:
#   .\add-cdn-domain.ps1 -AccessKeyId <AK_ID> -AccessKeySecret <AK_SECRET>
#
# 前置: AK 需具备 CDN(AddCdnDomain/DescribeUserDomains) + DNS(AddDomainRecord) 权限
# 幂等: 域名已存在则跳过添加; CNAME 记录已存在且值一致则跳过
param(
    [Parameter(Mandatory = $true)][string]$AccessKeyId,
    [Parameter(Mandatory = $true)][string]$AccessKeySecret,
    [string]$DnsAccessKeyId = "",
    [string]$DnsAccessKeySecret = "",
    [string]$DomainName = "v.zyjiu.com",
    [string]$RootDomain = "zxjiu.com",
    [string]$OssEndpoint = "zxjiu-videos.oss-cn-hangzhou.aliyuncs.com",
    [string]$VerifyTxt = "verify_6c4e027ed162c10bb7e93f0bb4ce116a",
    [switch]$ListRecordsOnly
)
# DNS 解析若在另一账号, 用 -DnsAccessKeyId/-DnsAccessKeySecret 单独指定; 缺省与主 AK 相同
if (-not $DnsAccessKeyId)     { $DnsAccessKeyId = $AccessKeyId }
if (-not $DnsAccessKeySecret) { $DnsAccessKeySecret = $AccessKeySecret }

$ErrorActionPreference = "Stop"

function PercentEncode([string]$s) {
    return [Uri]::EscapeDataString($s)
}

# 通用阿里云 RPC 签名调用 (HMAC-SHA1 · V1)
function Invoke-AliyunApi([string]$Endpoint, [string]$ApiVersion, [string]$Action, [hashtable]$BizParams, [string]$AkId, [string]$AkSecret) {
    if (-not $AkId)     { $AkId = $AccessKeyId }
    if (-not $AkSecret) { $AkSecret = $AccessKeySecret }
    $params = @{
        AccessKeyId      = $AkId
        Action           = $Action
        Format           = "JSON"
        Version          = $ApiVersion
        SignatureMethod  = "HMAC-SHA1"
        SignatureVersion = "1.0"
        SignatureNonce   = [Guid]::NewGuid().ToString("N")
        Timestamp        = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    foreach ($k in $BizParams.Keys) { $params[$k] = $BizParams[$k] }

    $sorted = $params.GetEnumerator() | Sort-Object Key
    $canonical = ($sorted | ForEach-Object {
        "$(PercentEncode($_.Key))=$(PercentEncode([string]$_.Value))"
    }) -join "&"

    $stringToSign = "GET&$(PercentEncode('/'))&$(PercentEncode($canonical))"

    $hmac = New-Object System.Security.Cryptography.HMACSHA1
    $hmac.Key = [Text.Encoding]::UTF8.GetBytes($AkSecret + "&")
    $hash = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($stringToSign))
    $signature = [Convert]::ToBase64String($hash)

    $uri = "https://$Endpoint/?$canonical&Signature=$(PercentEncode($signature))"
    try {
        return Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 30
    }
    catch {
        $body = $_.ErrorDetails.Message
        if ($body) { throw "API 调用失败 [$Action]: $body" }
        throw
    }
}

function Invoke-CdnApi([string]$Action, [hashtable]$BizParams) {
    return Invoke-AliyunApi "cdn.aliyuncs.com" "2018-05-10" $Action $BizParams
}
function Invoke-DnsApi([string]$Action, [hashtable]$BizParams) {
    return Invoke-AliyunApi "alidns.aliyuncs.com" "2015-01-09" $Action $BizParams $DnsAccessKeyId $DnsAccessKeySecret
}

# 源站: OSS 域名 · 主源站(priority 20) · 权重 10 · 端口 80
$sourcesJson = '[{"content":"' + $OssEndpoint + '","type":"oss","priority":"20","weight":"10","port":80}]'

# 调试开关: 仅列出 DNS 账号 API 侧的 zone 记录后退出
if ($ListRecordsOnly) {
    $r = Invoke-DnsApi "DescribeDomainRecords" @{ DomainName = $RootDomain; PageSize = "100" }
    $r.DomainRecords.Record | Format-Table RR, Type, Line, Value, TTL, Status, RecordId -AutoSize
    exit 0
}

Write-Host "==> [1/5] 确保根域名归属验证 TXT 记录存在 (verification.${RootDomain})" -ForegroundColor Cyan
$recs = Invoke-DnsApi "DescribeDomainRecords" @{ DomainName = $RootDomain; RRKeyWord = "verification" }
$txtRec = @($recs.DomainRecords.Record) | Where-Object { $_.RR -eq "verification" -and $_.Type -eq "TXT" }
if ($txtRec | Where-Object { $_.Value -eq $VerifyTxt }) {
    Write-Host "    TXT 验证记录已存在, 跳过" -ForegroundColor Yellow
} else {
    foreach ($old in @($txtRec)) {
        $null = Invoke-DnsApi "DeleteDomainRecord" @{ RecordId = $old.RecordId }
        Write-Host "    删除旧 TXT $($old.Value)" -ForegroundColor Yellow
    }
    $null = Invoke-DnsApi "AddDomainRecord" @{
        DomainName = $RootDomain; RR = "verification"; Type = "TXT"; Value = $VerifyTxt
    }
    Write-Host "    TXT 已添加: verification.${RootDomain} = $VerifyTxt" -ForegroundColor Green
    Start-Sleep -Seconds 10   # 同服务商权威 DNS 即时生效, 留缓冲
}

Write-Host "==> [2/5] 查询域名 ${DomainName} 是否已在 CDN" -ForegroundColor Cyan
$existing = $null
try {
    $q = Invoke-CdnApi "DescribeUserDomains" @{ DomainName = $DomainName; PageSize = "50" }
    $existing = @($q.Domains.PageData) | Where-Object { $_.DomainName -eq $DomainName }
} catch {
    Write-Host "    查询失败(继续尝试添加): $($_.Exception.Message)" -ForegroundColor Yellow
}

if ($existing) {
    Write-Host "    已存在: 状态=$($existing.DomainStatus) CNAME=$($existing.Cname) — 跳过添加" -ForegroundColor Yellow
} else {
    Write-Host "==> [2/4] AddCdnDomain (stream/仅中国内地/OSS 源站)" -ForegroundColor Cyan
    $addParams = @{
        DomainName = $DomainName
        CdnType    = "video"
        Sources    = $sourcesJson
    }
    $r = $null
    try {
        $r = Invoke-CdnApi "AddCdnDomain" $addParams
        Write-Host "    添加请求已受理" -ForegroundColor Green
    } catch {
        $msg = $_.Exception.Message
        if ($msg -match "Scope|InvalidParameter") {
            Write-Host "    首次尝试被拒($msg), 降级不带附加参数重试..." -ForegroundColor Yellow
            $r = Invoke-CdnApi "AddCdnDomain" $addParams
            Write-Host "    重试成功" -ForegroundColor Green
        } else { throw }
    }
}

Write-Host "==> [3/4] 轮询域名状态与 CNAME 分配 (最多 6 次 × 10s)" -ForegroundColor Cyan
$cname = $existing.Cname
$status = $existing.DomainStatus
for ($i = 1; $i -le 6; $i++) {
    if (-not $cname -or -not $status) {
        Start-Sleep -Seconds 10
        try {
            $q = Invoke-CdnApi "DescribeUserDomains" @{ DomainName = $DomainName; PageSize = "50" }
            $d = @($q.Domains.PageData) | Where-Object { $_.DomainName -eq $DomainName } | Select-Object -First 1
            if ($d) { $cname = $d.Cname; $status = $d.DomainStatus }
        } catch { Write-Host "    第 $i 次查询失败: $($_.Exception.Message)" -ForegroundColor Yellow }
        Write-Host "    [$i] status=$status cname=$cname"
    } else { break }
}

if (-not $cname) {
    Write-Host "    未取到 CNAME(可能仍在配置中), 请稍后手动执行:" -ForegroundColor Yellow
    Write-Host "    DescribeUserDomains DomainName=${DomainName} 查看 Cname 字段" -ForegroundColor Yellow
    Write-Host "    DNS CNAME 记录需在拿到 CNAME 目标后补配" -ForegroundColor Yellow
    exit 1
}

Write-Host "==> [4/4] 配置 DNS: ${DomainName} CNAME → $cname" -ForegroundColor Cyan
$rr = ($DomainName -replace "\.$([Regex]::Escape($RootDomain))$", "").TrimEnd('.')
if (-not $rr) { $rr = "@" }
$recs = Invoke-DnsApi "DescribeDomainRecords" @{ DomainName = $RootDomain; RRKeyWord = $rr }
$mine = @($recs.DomainRecords.Record) | Where-Object { $_.RR -eq $rr -and $_.Type -eq "CNAME" }
if ($mine | Where-Object { $_.Value -eq $cname }) {
    Write-Host "    CNAME 记录已存在且一致, 跳过" -ForegroundColor Yellow
} else {
    foreach ($old in @($mine | Where-Object { $_.Value -ne $cname })) {
        $null = Invoke-DnsApi "DeleteDomainRecord" @{ RecordId = $old.RecordId }
        Write-Host "    删除旧 CNAME $($old.Value)" -ForegroundColor Yellow
    }
    $res = Invoke-DnsApi "AddDomainRecord" @{
        DomainName = $RootDomain; RR = $rr; Type = "CNAME"; Value = $cname
    }
    Write-Host "    CNAME 添加成功 (RecordId=$($res.RecordId))" -ForegroundColor Green
}

Write-Host ""
Write-Host "完成: ${DomainName} → ${cname} (状态 $status)"
Write-Host "CDN 配置生效约需 10 分钟; 验证: curl.exe -I https://${DomainName}/d84_4c2d8186ccee_ep1.mp4"
