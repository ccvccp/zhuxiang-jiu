# CDN static domain: s.zyjiu.com (origin=old ECS 8.147.66.237:80, web, cn-mainland)
# Modes: rebuild(delete+add) | status | cache | dns
param(
    [ValidateSet('rebuild','status','cache','dns')]
    [string]$Mode = 'status',
    [string]$CdnDomain = 's.zyjiu.com',
    [string]$OriginIp = '8.147.66.237'
)
$ErrorActionPreference = 'Stop'
$ak = $env:ALIYUN_AK_ID; $sk = $env:ALIYUN_AK_SECRET
if (-not $ak -or -not $sk) {
    $ak = [Environment]::GetEnvironmentVariable('ALIYUN_AK_ID','User')
    $sk = [Environment]::GetEnvironmentVariable('ALIYUN_AK_SECRET','User')
}
if (-not $ak -or -not $sk) { throw 'missing AK/SK' }
Write-Host ("params: Mode=$Mode CdnDomain=$CdnDomain OriginIp=$OriginIp")

function Get-UtcStamp { (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }
function Get-Nonce { [guid]::NewGuid().ToString() }
function Encode-Param([string]$s) {
    $e = [System.Uri]::EscapeDataString($s)
    $e.Replace('+','%20').Replace('*','%2A').Replace('%7E','~').Replace('[','%5B').Replace(']','%5D')
}
function Invoke-Ali([string]$ApiHost, [hashtable]$Params, [string]$Ver) {
    $common = @{
        Format='JSON'; Version=$Ver; AccessKeyId=$ak
        SignatureMethod='HMAC-SHA1'; SignatureVersion='1.0'
        SignatureNonce=Get-Nonce; Timestamp=Get-UtcStamp
    }
    $all = $common + $Params
    $sorted = $all.GetEnumerator() | Sort-Object Name
    $canon = ($sorted | ForEach-Object { "$(Encode-Param $_.Name)=$(Encode-Param ([string]$_.Value))" }) -join '&'
    $sts = "GET&%2F&$(Encode-Param $canon)"
    $hmac = New-Object System.Security.Cryptography.HMACSHA1
    $hmac.Key = [Text.Encoding]::UTF8.GetBytes($sk + '&')
    $sig = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($sts)))
    $url = "https://$ApiHost/?$canon&Signature=$(Encode-Param $sig)"
    Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 30
}

switch ($Mode) {
    'rebuild' {
        Write-Host "== delete existing =="
        try {
            $null = Invoke-Ali 'cdn.aliyuncs.com' @{ Action='DeleteCdnDomain'; DomainName=$CdnDomain } '2018-05-10'
            Write-Host '  deleted(old)'
            Start-Sleep 8
        } catch { Write-Host '  (not exists or delete skip)' }
        Write-Host "== add with JSON Sources =="
        $sources = '[{"content":"' + $OriginIp + '","type":"ipaddr","port":80,"priority":"20","weight":"15"}]'
        $r = Invoke-Ali 'cdn.aliyuncs.com' @{
            Action='AddCdnDomain'; CdnType='web'; DomainName=$CdnDomain; Region='cn-mainland'
            Sources=$sources
        } '2018-05-10'
        $r | ConvertTo-Json -Depth 3
    }
    'status' {
        $r = Invoke-Ali 'cdn.aliyuncs.com' @{ Action='DescribeCdnDomainDetail'; DomainName=$CdnDomain } '2018-05-10'
        $d = $r.GetDomainDetailModel
        if ($d) {
            Write-Host ("  Status: " + $d.DomainStatus + "  CName: " + $d.Cname)
            $d.SourceModels.SourceModel | ForEach-Object {
                Write-Host ("  Origin: [" + $_.Content + "] type=" + $_.Type + " port=" + $_.Port + " enabled=" + $_.Enabled)
            }
        } else { $r | ConvertTo-Json -Depth 4 }
    }
    'cache' {
        $target = '[{"functionArgs":[{"argName":"file_type","argValue":"jpg,jpeg,png,webp,gif,ico,svg,woff,woff2,css,js"},{"argName":"ttl","argValue":"2592000"},{"argName":"weight","argValue":"10"}],"functionName":"filetype_based_ttl_set"}]'
        $r = Invoke-Ali 'cdn.aliyuncs.com' @{ Action='BatchSetCdnDomainConfig'; DomainNames=$CdnDomain; Functions=$target } '2018-05-10'
        $r | ConvertTo-Json -Depth 3
    }
    'dns' {
        $detail = Invoke-Ali 'cdn.aliyuncs.com' @{ Action='DescribeCdnDomainDetail'; DomainName=$CdnDomain } '2018-05-10'
        $cname = $detail.GetDomainDetailModel.Cname
        Write-Host ("CDN CNAME: " + $cname)
        if (-not $cname) { throw 'no cname' }
        $recs = Invoke-Ali 'alidns.aliyuncs.com' @{ Action='DescribeSubDomainRecords'; SubDomain=$CdnDomain; Type='CNAME' } '2015-01-09'
        $exist = @($recs.DomainRecords.Record) | Where-Object { $_.RR -eq 's' }
        if ($exist.Count) {
            $null = Invoke-Ali 'alidns.aliyuncs.com' @{ Action='UpdateDomainRecord'; RecordId=$exist[0].RecordId; RR='s'; Type='CNAME'; Value=$cname } '2015-01-09'
            Write-Host ("  DNS updated -> " + $cname)
        } else {
            $r = Invoke-Ali 'alidns.aliyuncs.com' @{ Action='AddDomainRecord'; DomainName='zyjiu.com'; RR='s'; Type='CNAME'; Value=$cname } '2015-01-09'
            Write-Host ("  DNS added -> " + $cname)
            $r | ConvertTo-Json -Depth 3
        }
    }
}
Write-Host "CDN-$Mode-DONE"
