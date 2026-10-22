# Aliyun DNS RPC helper: 查询/暂停 zyjiu.com 解析记录
# Usage:
#   powershell -File aliyun-dns-rpc.ps1 ListRecords
#   powershell -File aliyun-dns-rpc.ps1 DisableCname
param(
    [Parameter(Position=0)][string]$Mode = 'ListRecords',
    [string]$DomainName = 'zyjiu.com'
)
$script:AK = $env:ALIYUN_AK_ID
$script:SK = $env:ALIYUN_AK_SECRET
if (-not $script:AK -or -not $script:SK) { throw 'set ALIYUN_AK_ID / ALIYUN_AK_SECRET first' }

function ConvertTo-Pct([string]$s) {
    return [Uri]::EscapeDataString($s).Replace('+','%20').Replace('*','%2A').Replace('%7E','~')
}

function Invoke-DnsRpc([hashtable]$params) {
    $p = @{
        AccessKeyId      = $script:AK
        Action           = $params['Action']
        Format           = 'JSON'
        Version          = '2015-01-09'
        SignatureMethod  = 'HMAC-SHA1'
        SignatureVersion = '1.0'
        SignatureNonce   = [Guid]::NewGuid().ToString('N')
        Timestamp        = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    }
    foreach ($k in $params.Keys) { if ($k -ne 'Action') { $p[$k] = $params[$k] } }
    $sorted = $p.GetEnumerator() | Sort-Object Key
    $canon = ($sorted | ForEach-Object { "$(ConvertTo-Pct $_.Key)=$(ConvertTo-Pct ([string]$_.Value))" }) -join '&'
    $sts = "GET&$(ConvertTo-Pct '/')&$(ConvertTo-Pct $canon)"
    $hmac = New-Object System.Security.Cryptography.HMACSHA1
    $hmac.Key = [Text.Encoding]::UTF8.GetBytes($script:SK + '&')
    $sig = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($sts)))
    $url = "https://alidns.aliyuncs.com/?$canon&Signature=$(ConvertTo-Pct $sig)"
    $resp = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 30
    return ($resp.Content | ConvertFrom-Json)
}

switch ($Mode) {
    'ListRecords' {
        $res = Invoke-DnsRpc @{ Action='DescribeDomainRecords'; DomainName=$DomainName; PageSize='20' }
        if ($res.Code) { Write-Host ("API-ERR: " + $res.Code + " " + $res.Message); exit 1 }
        $res.DomainRecords.Record | ForEach-Object {
            Write-Host ("RR=" + $_.RR + " Type=" + $_.Type + " Value=" + $_.Value + " Status=" + $_.Status + " RecordId=" + $_.RecordId)
        }
    }
    'DisableCname' {
        $res = Invoke-DnsRpc @{ Action='DescribeDomainRecords'; DomainName=$DomainName; PageSize='20' }
        if ($res.Code) { Write-Host ("API-ERR: " + $res.Code + " " + $res.Message); exit 1 }
        $cname = $res.DomainRecords.Record | Where-Object { $_.Type -eq 'CNAME' }
        if (-not $cname) { Write-Host 'NO-CNAME-FOUND'; exit 0 }
        foreach ($r in $cname) {
            Write-Host ("disabling: " + $r.RR + " -> " + $r.Value + " (" + $r.RecordId + ")")
            $d = Invoke-DnsRpc @{ Action='SetDNSSRecordStatus'; RecordId=$r.RecordId; Status='Disable' }
            if ($d.Code) { Write-Host ("DISABLE-ERR: " + $d.Code + " " + $d.Message); exit 1 }
            Write-Host ("disabled: " + $r.RR)
        }
        # 复查
        $check = Invoke-DnsRpc @{ Action='DescribeDomainRecords'; DomainName=$DomainName; PageSize='20' }
        $check.DomainRecords.Record | ForEach-Object { Write-Host ("AFTER RR=" + $_.RR + " Type=" + $_.Type + " Status=" + $_.Status) }
    }
    default { Write-Host 'Usage: ListRecords | DisableCname' }
}