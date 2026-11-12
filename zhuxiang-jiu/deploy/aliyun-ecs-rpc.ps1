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
    [string]$CommandName = 'cmd',
    [string]$SecurityGroupId = '',
    [string]$IpProtocol = 'tcp',
    [string]$PortRange = '3389/3389',
    [string]$SourceCidrIp = '',
    [string]$NicType = 'intranet',
    [string]$Priority = '1'
)

# credentials via env (never hardcode): set ALIYUN_AK_ID / ALIYUN_AK_SECRET before running
$script:AK = $env:ALIYUN_AK_ID
$script:SK = $env:ALIYUN_AK_SECRET
if (-not $script:AK -or -not $script:SK) { throw 'set $env:ALIYUN_AK_ID / $env:ALIYUN_AK_SECRET first' }

function ConvertTo-Pct([string]$s) {
    # PS5.1/.NET Framework EscapeDataString leaves RFC2396 mark chars !*'() unescaped;
    # Aliyun POP signature requires RFC3986 unreserved set only (A-Za-z0-9-_.~)
    return [Uri]::EscapeDataString($s).Replace('+','%20').Replace('*','%2A').Replace('%7E','~').Replace('!','%21').Replace("'","%27").Replace('(','%28').Replace(')','%29')
}

function Invoke-Rpc([hashtable]$params, [string]$version = '2014-05-26', [string]$method = 'GET') {
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
    $verb = $method.ToUpper()
    $sts = "$verb&$(ConvertTo-Pct '/')&$(ConvertTo-Pct $canon)"

    $hmac = New-Object System.Security.Cryptography.HMACSHA1
    $hmac.Key = [Text.Encoding]::UTF8.GetBytes($script:SK + '&')
    $sig = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($sts)))

    # RunCommand payloads are ~13KB, past the comfort zone of an HTTP request line, so
    # Send posts them as a form body instead of a querystring.
    # NOTE: the earlier IncompleteSignature failures were NOT a GET-vs-POST problem --
    # they were ConvertTo-Pct leaving ! ' ( ) * literal, so our canonical query differed
    # from the server's while the server still decoded CommandContent correctly. Fixed in
    # ConvertTo-Pct; pcttest.ps1 asserts it against a byte-exact RFC3986 encoder.
    if ($verb -eq 'POST') {
        $resp = Invoke-WebRequest "https://$hostEp/" -Method Post -UseBasicParsing -TimeoutSec 60 `
            -ContentType 'application/x-www-form-urlencoded' `
            -Body "$canon&Signature=$(ConvertTo-Pct $sig)"
    } else {
        $resp = Invoke-WebRequest "https://$hostEp/?$canon&Signature=$(ConvertTo-Pct $sig)" -UseBasicParsing -TimeoutSec 30
    }
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
        # SIZE LIMIT (learned the hard way, twice): the cap is on the SCRIPT ITSELF, not on
        # whatever string we hand the API. Aliyun allows ~18 KB of script content; with
        # ContentEncoding=Base64 the parameter may carry up to 24 KB of base64 (which decodes
        # back to those same ~18 KB). Two previous attempts blew this:
        #   1) the documented 194-line runner  -> script 10477 chars, sent as base64(UTF-16LE)
        #      = 27940 chars -> CmdContent.ExceedLimit
        #   2) the comment-stripped 151-line runner -> script 8565 chars, but STILL sent as
        #      base64(UTF-16LE) wrapped in iex(...) = 22920 chars -> CmdContent.ExceedLimit
        #      (the wrapper was pure overhead: it made the wire payload 2.7x the script)
        # So: encode the script as base64(UTF-8) -- 8565 chars -> 11420 -- and tell the API it
        # is Base64. No iex wrapper. RunPowerShellScript already runs the decoded content
        # inside powershell, so wrapping it in "powershell -EncodedCommand" or iex-of-base64
        # only ever added nesting (the nested spawn is refused on this box while the agent's
        # own powershell still works, and RunCommand then reports ExitCode:0/Status:Success --
        # a silent no-op).
        $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($body))
        if ($b64.Length -gt 24576) { throw "CommandContent base64 $($b64.Length) exceeds 24576 (script cap ~18KB)" }
        Write-Host "Script: $CommandFile ($($body.Length) chars -> b64 $($b64.Length))  Timeout: ${TimeoutSec}s"
        $res = Invoke-Rpc @{
            Action='RunCommand'; RegionId=$(if ($RegionId) { $RegionId } else { 'cn-wulanchabu' })
            "InstanceId.1"=$InstanceId
            Type='RunPowerShellScript'
            ContentEncoding='Base64'
            CommandContent=$b64
            Timeout="$TimeoutSec"
            Name=$CommandName
        } '2014-05-26' 'POST'
        if ($res.InvokeId) { Write-Host "InvokeId: $($res.InvokeId)  CommandId: $($res.CommandId)" }
        else { Write-Host "FAILED: $($res | ConvertTo-Json -Depth 5)" }
    }
    'Invoke' {
        if (-not $InvokeId) { Write-Host 'Need -InvokeId'; exit 1 }
        $res = Invoke-Rpc @{ Action='DescribeInvocationResults'; InvokeId=$InvokeId; RegionId=$(if ($RegionId) { $RegionId } else { 'cn-wulanchabu' }) }
        if ($res.Invocation) {
            Write-Host "InvokeId: $InvokeId  Status: $($res.Invocation.InvokeStatus)  OK:$($res.Invocation.SuccessCount) FAIL:$($res.Invocation.FailedCount) RUNNING:$($res.Invocation.RunCount)"
        }
        foreach ($inv in $res.Invocation.InvocationResults.InvocationResult) {
            Write-Host ""
            Write-Host "===== Instance $($inv.InstanceId)  ExitCode: $($inv.ExitCode)  Status: $($inv.InvocationStatus) ====="
            if ($inv.Output) {
                # $otxt, NOT $raw: PS variable names are case-insensitive, and a [switch]$Raw
                # in the param block silently turned the old `$raw = <string>` assignment into
                # an ArgumentTransformationMetadataException -- Output was there, ExitCode was 0,
                # and all we saw was "False". Never name a local after a declared parameter.
                $otxt = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$inv.Output))
                if ($otxt -match 'CLIXML|<Objs') {
                    $msgs = [regex]::Matches($otxt, '<S(?: N="Message"| S="Error")?>((?s).*?)</S>') | ForEach-Object { $_.Groups[1].Value }
                    if ($msgs) { $clean = $msgs } else { $clean = @($otxt) }
                } else {
                    $clean = @($otxt)
                }
                Write-Host (($clean -join "`n") -replace '&gt;','>' -replace '&lt;','<' -replace '&quot;','"')
            }
            if ($inv.ErrorInfo) { Write-Host "ERROR: $($inv.ErrorInfo)" -ForegroundColor Red }
        }
    }
    'SGQuery' {
        # query security group of an instance (and its inbound rules if -SecurityGroupId given)
        if (-not $RegionId) { Write-Host 'Need -RegionId'; exit 1 }
        if ($SecurityGroupId) {
            $res = Invoke-Rpc @{ Action='DescribeSecurityGroupAttribute'; RegionId=$RegionId; SecurityGroupId=$SecurityGroupId; Direction='ingress' }
            $res.Permissions.Permission | ForEach-Object {
                Write-Host ("  " + $_.IpProtocol + "/" + $_.PortRange + "  src=" + $_.SourceCidrIp + "  policy=" + $_.Policy + "  prio=" + $_.Priority + "  id=" + $_.SecurityGroupRuleId + "  desc=" + $_.Description)
            }
            if (-not $res.Permissions.Permission) { Write-Host '  (no ingress rules)' }
        } else {
            $res = Invoke-Rpc @{ Action='DescribeInstances'; RegionId=$RegionId; "InstanceId.1"=$InstanceId }
            $ins = $res.Instances.Instance[0]
            if ($ins) {
                Write-Host ("Instance: " + $ins.InstanceId + "  SG: " + ($ins.SecurityGroupIds.SecurityGroupId -join ', '))
            } else { Write-Host 'instance not found' }
        }
    }
    'SGAuth' {
        # authorize one inbound rule: SGAuth -SecurityGroupId sg-xxx -SourceCidrIp 1.2.3.4/32 -PortRange 3389/3389
        if (-not $RegionId -or -not $SecurityGroupId -or -not $SourceCidrIp) { Write-Host 'Need -RegionId -SecurityGroupId -SourceCidrIp'; exit 1 }
        $res = Invoke-Rpc @{
            Action='AuthorizeSecurityGroup'; RegionId=$RegionId; SecurityGroupId=$SecurityGroupId
            'Permissions.1.IpProtocol'=$IpProtocol
            'Permissions.1.PortRange'=$PortRange
            'Permissions.1.SourceCidrIp'=$SourceCidrIp
            'Permissions.1.Policy'='Accept'
            'Permissions.1.Priority'='1'
            'Permissions.1.NicType'=$NicType
            'Permissions.1.Description'='RDP-from-pc'
        }
        if ($res.RequestId) { Write-Host ("SG rule authorized: " + $IpProtocol + "/" + $PortRange + " <- " + $SourceCidrIp + "  RequestId=" + $res.RequestId) }
        else { Write-Host ("FAILED: " + ($res | ConvertTo-Json -Depth 5)) }
    }
    'SGRevoke' {
        # revoke one inbound rule: SGRevoke -SecurityGroupId sg-xxx -SourceCidrIp 0.0.0.0/0 -PortRange 3389/3389
        #
        # RevokeSecurityGroup locates the rule by its FULL tuple (protocol, port range, source
        # cidr, nic type, policy, priority). Leaving Priority out makes the API default it to 1,
        # so revoking a rule that really carries prio=100 -- which is every Aliyun-generated
        # "System created rule." -- matched nothing and STILL returned HTTP 200 with a RequestId.
        # A RequestId is therefore not evidence of deletion. Pass -Priority explicitly, and trust
        # only the read-back assertion below.
        if (-not $RegionId -or -not $SecurityGroupId -or -not $SourceCidrIp) { Write-Host 'Need -RegionId -SecurityGroupId -SourceCidrIp'; exit 1 }
        $res = Invoke-Rpc @{
            Action='RevokeSecurityGroup'; RegionId=$RegionId; SecurityGroupId=$SecurityGroupId
            'Permissions.1.IpProtocol'=$IpProtocol
            'Permissions.1.PortRange'=$PortRange
            'Permissions.1.SourceCidrIp'=$SourceCidrIp
            'Permissions.1.NicType'=$NicType
            'Permissions.1.Policy'='Accept'
            'Permissions.1.Priority'=$Priority
        }
        if (-not $res.RequestId) { Write-Host ("FAILED: " + ($res | ConvertTo-Json -Depth 5)); exit 1 }
        Write-Host ("SG revoke accepted: " + $IpProtocol + "/" + $PortRange + " <- " + $SourceCidrIp + " nic=" + $NicType + " prio=" + $Priority + "  RequestId=" + $res.RequestId)
        $chk = Invoke-Rpc @{ Action='DescribeSecurityGroupAttribute'; RegionId=$RegionId; SecurityGroupId=$SecurityGroupId; Direction='ingress' }
        $left = @($chk.Permissions.Permission | Where-Object {
                $_.IpProtocol -eq $IpProtocol -and $_.PortRange -eq $PortRange -and
                $_.SourceCidrIp -eq $SourceCidrIp -and [string]$_.Priority -eq $Priority
            })
        if ($left.Count -eq 0) {
            Write-Host ("VERIFIED: rule is gone (proto=" + $IpProtocol + " port=" + $PortRange + " src=" + $SourceCidrIp + " prio=" + $Priority + ")")
        } else {
            Write-Host ("STILL-PRESENT: " + $left.Count + " rule(s) still match -- the revoke was a NO-OP. Re-check the tuple (priority / policy / nicType).")
            exit 1
        }
    }
    default { Write-Host "Unknown mode: $Mode (FindInstance|Invoke|Send|SGQuery|SGAuth|SGRevoke)" }
}
