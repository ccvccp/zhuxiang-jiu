# 恢复 zxjiu.com A 记录解析(@/www 被 DISABLE 暂停 → Enable)
param(
    [Parameter(Mandatory = $true)][string]$AccessKeyId,
    [Parameter(Mandatory = $true)][string]$AccessKeySecret,
    [string]$DomainName = "zxjiu.com"
)
$ErrorActionPreference = "Stop"
function PercentEncode([string]$s) { return [Uri]::EscapeDataString($s) }
function Invoke-AliyunApi([string]$Action, [hashtable]$BizParams) {
    $params = @{
        AccessKeyId = $AccessKeyId; Action = $Action; Format = "JSON"
        Version = "2015-01-09"; SignatureMethod = "HMAC-SHA1"; SignatureVersion = "1.0"
        SignatureNonce = [Guid]::NewGuid().ToString("N")
        Timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    foreach ($k in $BizParams.Keys) { $params[$k] = $BizParams[$k] }
    $sorted = $params.GetEnumerator() | Sort-Object Key
    $canonical = ($sorted | ForEach-Object { "$(PercentEncode($_.Key))=$(PercentEncode([string]$_.Value))" }) -join "&"
    $stringToSign = "GET&$(PercentEncode('/'))&$(PercentEncode($canonical))"
    $hmac = New-Object System.Security.Cryptography.HMACSHA1
    $hmac.Key = [Text.Encoding]::UTF8.GetBytes($AccessKeySecret + "&")
    $sig = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($stringToSign)))
    $resp = Invoke-WebRequest ("https://alidns.aliyuncs.com/?" + $canonical + "&Signature=" + [Uri]::EscapeDataString($sig)) -UseBasicParsing -TimeoutSec 30
    $body = $resp.Content
    if ($body -match '"Code"') { throw "API 调用失败 [$Action]: $body" }
    return ($body | ConvertFrom-Json)
}
$existing = Invoke-AliyunApi "DescribeDomainRecords" @{ DomainName = $DomainName }
foreach ($r in @($existing.DomainRecords.Record | Where-Object { $_.Type -eq "A" -and $_.Status -eq "DISABLE" })) {
    Write-Host ("启用: [" + $r.RR + "] " + $r.Value + " (RecordId=" + $r.RecordId + ")")
    $null = Invoke-AliyunApi "SetDomainRecordStatus" @{ RecordId = $r.RecordId; Status = "Enable" }
}
$final = Invoke-AliyunApi "DescribeDomainRecords" @{ DomainName = $DomainName }
$final.DomainRecords.Record | Where-Object { $_.Type -eq "A" } | Format-Table RR, Type, Value, TTL, Status -AutoSize
Write-Host 'DNS-ENABLE-DONE'
