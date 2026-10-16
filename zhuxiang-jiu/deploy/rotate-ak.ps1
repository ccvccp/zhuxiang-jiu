# AK 自轮换: RAM UpdateAccessKey 自禁用(Status=Inactive, 可逆, 控制台可再删)
# 用法: powershell -File rotate-ak.ps1 <AK_ID> <AK_SECRET>
param(
    [Parameter(Mandatory = $true)][string]$AkId,
    [Parameter(Mandatory = $true)][string]$AkSecret,
    [string]$UserName = 'power-application-user'
)
$ErrorActionPreference = 'Stop'
function PercentEncode([string]$s) { return [Uri]::EscapeDataString($s) }
function Invoke-Ram([string]$Action, [hashtable]$BizParams) {
    $params = @{
        AccessKeyId = $AkId; Action = $Action; Format = 'JSON'
        Version = '2015-05-01'; SignatureMethod = 'HMAC-SHA1'; SignatureVersion = '1.0'
        SignatureNonce = [Guid]::NewGuid().ToString('N')
        Timestamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    }
    foreach ($k in $BizParams.Keys) { $params[$k] = $BizParams[$k] }
    $sorted = $params.GetEnumerator() | Sort-Object Key
    $canonical = ($sorted | ForEach-Object { "$(PercentEncode($_.Key))=$(PercentEncode([string]$_.Value))" }) -join '&'
    $sts = "GET&$(PercentEncode('/'))&$(PercentEncode($canonical))"
    $hmac = New-Object System.Security.Cryptography.HMACSHA1
    $hmac.Key = [Text.Encoding]::UTF8.GetBytes($AkSecret + '&')
    $sig = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($sts)))
    $resp = Invoke-WebRequest ("https://ram.aliyuncs.com/?" + $canonical + '&Signature=' + [Uri]::EscapeDataString($sig)) -UseBasicParsing -TimeoutSec 30
    return ($resp.Content | ConvertFrom-Json)
}
# 1) 自省: ListAccessKeys 带 UserName(阿里云该接口 UserName 必填, 见 InvalidParameter.UserName.Empty)
try {
    $keys = Invoke-Ram 'ListAccessKeys' @{ UserName = $UserName }
    Write-Host ("RAM 用户: " + $userName + "  密钥数: " + @($keys.AccessKeys.AccessKey).Count)
} catch {
    Write-Host ("ListAccessKeys 失败: " + $_.Exception.Message)
    exit 1
}
# 2) 自禁用
$null = Invoke-Ram 'UpdateAccessKey' @{ UserAccessKeyId = $AkId; Status = 'Inactive'; UserName = $userName }
Write-Host ("已禁用: " + $AkId + " (Status=Inactive, 用户: " + $userName + ")")
# 3) 失效验证: 再调一次应被拒
try {
    $null = Invoke-Ram 'ListAccessKeys' @{}
    Write-Host '!! 禁用后仍可调用(异常), 请控制台手动处理'
    exit 1
} catch {
    Write-Host '失效验证: 二次调用被拒(符合预期)'
    Write-Host 'ROTATE-OK'
}
