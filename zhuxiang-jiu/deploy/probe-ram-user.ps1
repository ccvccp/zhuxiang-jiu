# 从 RAM 403 报错体提取密钥所属 RAM 用户名(报错含请求者 ARN)
param(
    [Parameter(Mandatory = $true)][string]$AkId,
    [Parameter(Mandatory = $true)][string]$AkSecret
)
function PercentEncode([string]$s) { return [Uri]::EscapeDataString($s) }
$params = @{
    AccessKeyId = $AkId; Action = 'ListAccessKeys'; Format = 'JSON'
    Version = '2015-05-01'; SignatureMethod = 'HMAC-SHA1'; SignatureVersion = '1.0'
    SignatureNonce = [Guid]::NewGuid().ToString('N')
    Timestamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
}
$sorted = $params.GetEnumerator() | Sort-Object Key
$canonical = ($sorted | ForEach-Object { "$(PercentEncode($_.Key))=$(PercentEncode([string]$_.Value))" }) -join '&'
$sts = "GET&$(PercentEncode('/'))&$(PercentEncode($canonical))"
$hmac = New-Object System.Security.Cryptography.HMACSHA1
$hmac.Key = [Text.Encoding]::UTF8.GetBytes($AkSecret + '&')
$sig = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($sts)))
$url = "https://ram.aliyuncs.com/?" + $canonical + '&Signature=' + [Uri]::EscapeDataString($sig)
try {
    $resp = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 30
    Write-Host ("意外成功: " + $resp.Content)
} catch {
    $body = ''
    if ($_.Exception.Response) {
        $sr = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())
        $body = $sr.ReadToEnd()
    }
    Write-Host ("RESPONSE-BODY: " + $body)
    $arn = [regex]::Match($body, 'acs:ram::\d+:user/[^"''\s]+')
    if ($arn.Success) { Write-Host ("RAM-USER-ARN: " + $arn.Value) } else { Write-Host 'ARN 未在报文中' }
}
