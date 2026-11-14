# trae_t27dprobe.ps1 -- READ-ONLY reachability probe for t27d design.
#
# WHY THIS EXISTS: t25's verifier gated on https://zyjiu.com/ against a DIFFERENT host
# (8.147.66.237). The zxjiu host 39.107.43.149 measured its own nginx.conf as
#   listen 80;  listen 443 ssl;  server_name zyjiu.com zxjiu.com www.zxjiu.com;
#   ssl_certificate cert.pem;  ssl_certificate_key key.pem;
# and an on-host request to https://127.0.0.1/ failed with "could not establish trust
# relationship for the SSL/TLS secure channel". Whether that is only a CN-vs-IP artifact
# or a real cert problem for the zxjiu.com hostname is UNKNOWN, and t27d must not be
# written to gate on a scheme that cannot succeed. This probe settles it with evidence.
#
# It fetches NOTHING but the three page names over both schemes, prints only status /
# byte count / sha16 / a few headers, and never writes to any host. No TLS validation is
# bypassed (security-hard-coded 1.9): a trust failure is reported as a distinct outcome.
# 100% ASCII, bounded output, closing sentinel.

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Note([string]$s) { Write-Output $s }

$shaAlg = [System.Security.Cryptography.SHA256]::Create()
function Get-Sha([byte[]]$b) { return (([BitConverter]::ToString($shaAlg.ComputeHash($b))) -replace '-', '') }

$NAMES = @('activity.html', 'activity-detail.html', 'xiaozhu-voice.html')
$HOSTS = @('zxjiu.com', 'www.zxjiu.com')
$SCHEMES = @('http', 'https')

Note '=== T27D-PROBE (read-only) ==='
Note ('  stamp=' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' tls=1.2-forced bypassValidation=NEVER')

foreach ($h in $HOSTS) {
    Note ''
    Note ('--- DNS ' + $h + ' ---')
    try {
        $ips = [System.Net.Dns]::GetHostAddresses($h) | ForEach-Object { $_.IPAddressToString }
        Note ('  resolved=' + ($ips -join ','))
    } catch {
        Note ('  DNS-ERROR ' + $_.Exception.GetType().Name + ' :: ' + $_.Exception.Message)
    }
}

foreach ($sc in $SCHEMES) {
    foreach ($h in $HOSTS) {
        Note ''
        Note ('=== ' + $sc + '://' + $h + '/ ===')
        foreach ($n in $NAMES) {
            $u = $sc + '://' + $h + '/' + $n
            try {
                $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 45
                $ms = New-Object System.IO.MemoryStream
                $st = $r.RawContentStream
                $st.Position = 0
                $st.CopyTo($ms)
                $b = $ms.ToArray()
                $ms.Dispose()
                $srv = [string]$r.Headers['Server']
                $cl  = [string]$r.Headers['Content-Length']
                $age = [string]$r.Headers['Age']
                $xc  = [string]$r.Headers['X-Cache']
                Note ('  ' + $n + ' | status=' + [int]$r.StatusCode + ' bytes=' + $b.Length + ' sha16=' + (Get-Sha $b).Substring(0, 16) + ' server=' + $srv + ' contentLength=' + $cl + ' age=' + $age + ' xCache=' + $xc)
            } catch {
                $en = $_.Exception.GetType().Name
                $msg = $_.Exception.Message
                if ($msg.Length -gt 150) { $msg = $msg.Substring(0, 150) }
                Note ('  ' + $n + ' | ERROR ' + $en + ' :: ' + $msg)
            }
        }
    }
}

Note ''
Note '=== T27D-PROBE-DONE ==='
exit 0
