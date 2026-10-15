# Aliyun ECS RPC helper: find instance / query cloud-assistant invocation result
# Usage:
#   powershell -File aliyun-ecs-rpc.ps1 FindInstance -Ip 8.147.66.237
#   powershell -File aliyun-ecs-rpc.ps1 Invoke -InvokeId t-xxxx
param(
    [Parameter(Position=0)][string]$Mode = 'Invoke',
    [string]$Ip = '8.147.66.237',
    [string]$InvokeId = '',
    [string]$RegionId = '',
    [string]$CommandFile = '',
    [string]$InstanceId = 'i-0jl2lh29xbe4d0sownat',
    [int]$TimeoutSec = 3600,
    [string]$CommandName = 'cmd'
)

# credentials via env (never hardcode): set ALIYUN_AK_ID / ALIYUN_AK_SECRET before running
$script:AK = $env:ALIYUN_AK_ID
$script:SK = $env:ALIYUN_AK_SECRET
if (-not $script:AK -or -not $script:SK) { throw 'set $env:ALIYUN_AK_ID / $env:ALIYUN_AK_SECRET first' }

function ConvertTo-Pct([string]$s) {
    return [Uri]::EscapeDataString($s).Replace('+','%20').Replace('*','%2A').Replace('%7E','~')
}

function Invoke-Rpc([hashtable]$params, [string]$version = '2014-05-26') {
    $region = $params['RegionId']
    $hostEp = if ($region -and $region -ne 'cn-hangzhou') { "ecs.$region.aliyuncs.com" } else { 'ecs.aliyuncs.com' }
    $p = @{
        AccessKeyId      = $script:AK
        Action           = $params['Action']
        Format           = 'JSON'
        Version          = $version
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
    $url = "https://$hostEp/?$canon&Signature=$(ConvertTo-Pct $sig)"

    $resp = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 30
    return ($resp.Content | ConvertFrom-Json)
}

switch ($Mode) {
    'FindInstance' {
        $regions = if ($RegionId) { @($RegionId) } else { @('cn-wulanchabu','cn-hangzhou','cn-shanghai','cn-beijing','cn-shenzhen','cn-qingdao','cn-zhangjiakou','cn-hongkong') }
        foreach ($r in $regions) {
            $res = Invoke-Rpc @{ Action='DescribeInstances'; RegionId=$r }
            $hit = $res.Instances.Instance | Where-Object { $_.PublicIpAddress.IpAddress -contains $Ip -or $_.EipAddress.IpAddress -eq $Ip }
            if ($hit) {
                Write-Host "Region: $r"
                $hit | ForEach-Object { Write-Host "Instance: $($_.InstanceId)  $($_.InstanceName)  $($_.OSType)/$($_.OSName)  Status:$($_.Status)" }
                return
            }
        }
        Write-Host "Instance with IP=$Ip not found"
    }
    'Send' {
        if (-not $CommandFile) { Write-Host 'Need -CommandFile'; exit 1 }
        $body = [IO.File]::ReadAllText($CommandFile)
        $b64 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
        $wrapped = "powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand $b64; exit `$LASTEXITCODE"
        Write-Host "Script: $CommandFile ($($body.Length) chars)  Timeout: ${TimeoutSec}s"
        $res = Invoke-Rpc @{
            Action='RunCommand'; RegionId='cn-wulanchabu'
            "InstanceId.1"=$InstanceId
            Type='RunPowerShellScript'
            CommandContent=$wrapped
            Timeout="$TimeoutSec"
            Name=$CommandName
        }
        if ($res.InvokeId) { Write-Host "InvokeId: $($res.InvokeId)  CommandId: $($res.CommandId)" }
        else { Write-Host "FAILED: $($res | ConvertTo-Json -Depth 5)" }
    }
    'Invoke' {
        if (-not $InvokeId) { Write-Host 'Need -InvokeId'; exit 1 }
        $res = Invoke-Rpc @{ Action='DescribeInvocationResults'; InvokeId=$InvokeId; RegionId='cn-wulanchabu' }
        if ($res.Invocation) {
            Write-Host "InvokeId: $InvokeId  Status: $($res.Invocation.InvokeStatus)  OK:$($res.Invocation.SuccessCount) FAIL:$($res.Invocation.FailedCount) RUNNING:$($res.Invocation.RunCount)"
        }
        foreach ($inv in $res.Invocation.InvocationResults.InvocationResult) {
            Write-Host ""
            Write-Host "===== Instance $($inv.InstanceId)  ExitCode: $($inv.ExitCode)  Status: $($inv.InvocationStatus) ====="
            if ($inv.Output) {
                $raw = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($inv.Output))
                if ($raw -match 'CLIXML|<Objs') {
                    $msgs = [regex]::Matches($raw, '<S(?: N="Message"| S="Error")?>((?s).*?)</S>') | ForEach-Object { $_.Groups[1].Value }
                    if ($msgs) { $clean = $msgs } else { $clean = @($raw) }
                } else {
                    $clean = @($raw)
                }
                Write-Host (($clean -join "`n") -replace '&gt;','>' -replace '&lt;','<' -replace '&quot;','"')
            }
            if ($inv.ErrorInfo) { Write-Host "ERROR: $($inv.ErrorInfo)" -ForegroundColor Red }
        }
    }
    default { Write-Host "Unknown mode: $Mode (FindInstance|Invoke)" }
}
