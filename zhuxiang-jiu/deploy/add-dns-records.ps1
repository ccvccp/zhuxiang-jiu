# ============================================================
# add-dns-records.ps1 · 阿里云 DNS API 自动添加 A 记录
# ============================================================
# 用途: 通过阿里云 Alidns OpenAPI 为 zxjiu.com 添加 @/www 两条 A 记录
#       (RPC 签名 V1 · HMAC-SHA1, PowerShell 原生实现, 零外部依赖)
#
# 用法:
#   .\add-dns-records.ps1 -AccessKeyId <AK_ID> -AccessKeySecret <AK_SECRET>
#
# 幂等性: 先查现有记录, 已存在且值一致则跳过
param(
    [Parameter(Mandatory = $true)][string]$AccessKeyId,
    [Parameter(Mandatory = $true)][string]$AccessKeySecret,
    [string]$DomainName = "zxjiu.com",
    [string]$Ip = "47.236.61.117"
)

$ErrorActionPreference = "Stop"

# RFC3986 百分号编码(.NET EscapeDataString 行为: + → %2B, * → %2A, ~ 保留)
function PercentEncode([string]$s) {
    return [Uri]::EscapeDataString($s)
}

# 阿里云 RPC API 签名调用
function Invoke-AliyunApi([string]$Action, [hashtable]$BizParams) {
    $params = @{
        AccessKeyId      = $AccessKeyId
        Action           = $Action
        Format           = "JSON"
        Version          = "2015-01-09"
        SignatureMethod  = "HMAC-SHA1"
        SignatureVersion = "1.0"
        SignatureNonce   = [Guid]::NewGuid().ToString("N")
        Timestamp        = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    foreach ($k in $BizParams.Keys) { $params[$k] = $BizParams[$k] }

    # 1. 参数按键名排序 → 规范化查询串
    $sorted = $params.GetEnumerator() | Sort-Object Key
    $canonical = ($sorted | ForEach-Object {
        "$(PercentEncode($_.Key))=$(PercentEncode([string]$_.Value))"
    }) -join "&"

    # 2. 构造待签名字符串: GET&%2F&<encoded canonical>
    $stringToSign = "GET&$(PercentEncode('/'))&$(PercentEncode($canonical))"

    # 3. HMAC-SHA1(密钥 = Secret + "&") → Base64
    $hmac = New-Object System.Security.Cryptography.HMACSHA1
    $hmac.Key = [Text.Encoding]::UTF8.GetBytes($AccessKeySecret + "&")
    $hash = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($stringToSign))
    $signature = [Convert]::ToBase64String($hash)

    # 4. 发起请求
    $uri = "https://alidns.aliyuncs.com/?$canonical&Signature=$(PercentEncode($signature))"
    try {
        return Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 30
    }
    catch {
        $body = $_.ErrorDetails.Message
        if ($body) { throw "API 调用失败 [$Action]: $body" }
        throw
    }
}

Write-Host "==> [1/3] 查询 ${DomainName} 现有解析记录" -ForegroundColor Cyan
$existing = Invoke-AliyunApi "DescribeDomainRecords" @{ DomainName = $DomainName }
$records = @($existing.DomainRecords.Record)
Write-Host "    现有 $($records.Count) 条记录"

Write-Host "==> [2/3] 添加 A 记录(@ / www → $Ip)" -ForegroundColor Cyan
foreach ($rr in @("@", "www")) {
    $dup = $records | Where-Object { $_.RR -eq $rr -and $_.Type -eq "A" -and $_.Value -eq $Ip }
    if ($dup) {
        Write-Host "    [$rr] 已存在且指向 $Ip, 跳过 (RecordId=$($dup.RecordId))" -ForegroundColor Yellow
        continue
    }
    # 若存在指向其他 IP 的同类记录, 先删除
    $conflict = $records | Where-Object { $_.RR -eq $rr -and $_.Type -eq "A" -and $_.Value -ne $Ip }
    if ($conflict) {
        foreach ($c in @($conflict)) {
            $null = Invoke-AliyunApi "DeleteDomainRecord" @{ RecordId = $c.RecordId }
            Write-Host "    [$rr] 删除旧记录 $($c.Value) (RecordId=$($c.RecordId))"
        }
    }
    $result = Invoke-AliyunApi "AddDomainRecord" @{
        DomainName = $DomainName
        RR         = $rr
        Type       = "A"
        Value      = $Ip
    }
    Write-Host "    [$rr] → $Ip 添加成功 (RecordId=$($result.RecordId))" -ForegroundColor Green
}

Write-Host "==> [3/3] 复核解析记录" -ForegroundColor Cyan
$final = Invoke-AliyunApi "DescribeDomainRecords" @{ DomainName = $DomainName }
$final.DomainRecords.Record | Where-Object { $_.Type -eq "A" } |
    Format-Table RR, Type, Value, TTL, Status -AutoSize

Write-Host ""
Write-Host "DNS 记录添加完成。解析生效通常需 1~10 分钟(TTL 600s)。"
Write-Host "验证: Resolve-DnsName zxjiu.com -Type A -Server 47.118.199.220"
