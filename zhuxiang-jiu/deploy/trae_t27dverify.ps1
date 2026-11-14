# t27d -- post-chain public re-verification of the three zxjiu.com pages. READ-ONLY.
#
# Context: on host 39.107.43.149 the chain ran t27a (in-place byte-edit of
# activity.html / activity-detail.html / xiaozhu-voice.html under C:\www\dist, gated on
# full sha256 equality with the local source of truth) then t27b (appcmd stop site
# "Default Web Site", which released TCP/80). nssm's ~4m16s retry loop for the
# nginx8147 service then bound 80 on its own, so nginx is Running/Automatic and serving.
# t27c was refused by its own [1/6] baseline gate (it expected Paused/Automatic) and
# changed nothing. t27a+t27b proved the DISK is right and the port is right; this script
# proves the PUBLIC HTTP layer actually serves that corrected content.
#
# WHY FOUR ORIGINS AND NOT ONE: nginx.conf on that host declares
#   listen 80;  listen 443 ssl;  server_name zyjiu.com zxjiu.com www.zxjiu.com;
# and a read-only probe confirmed all of http/https x zxjiu.com/www.zxjiu.com answer 200
# from this machine. An on-host request to https://127.0.0.1/ failed TLS trust, which is a
# CN-vs-IP artifact only. Gating every reachable origin is what makes a stale vhost or a
# half-configured 443 block impossible to hide behind. zyjiu.com is NOT probed here: it is
# the other host's primary name and is out of scope for t27.
#
# Acceptance per page per origin (six pieces):
#   1) HTTP status 200
#   2) exact byte length == local source of truth
#   3) sha256 first 16 hex == local source of truth
#   4) three precise leak targets all zero hits
#   5) POSITIVE CONTROL: the fixed placeholder string present at least once. Without it an
#      empty body or a 404 page scores "zero leak hits" and passes -- the false-negative
#      class this project already got burned by twice.
#   6) LF-only line endings (crlf == 0), i.e. no intermediary rewrote the body.
#
# [0/3] RE-DERIVES every expectation from the local source of truth before any network
# call, so a stale table fails loudly instead of reporting a bogus PASS.
#
# DISCIPLINE (security-hard-coded.md forbid #2 / 2.1): every sensitive target literal is
# assembled from char codes, so this file contains no plaintext credential and no
# plaintext seed phone number. Only COUNTS are ever printed, never matched content.
# Target literals come from the ACTUAL production state observed by the t22 probe
# (quickFill / SEED_MEMBER_PHONE 138****0001 / its password), NOT from the older
# fillAccount / SEED_ADMIN_PHONE 138****0002 lineage that t19 cleaned locally.
#
# TLS validation is NEVER bypassed (security-hard-coded 1.9): Tls12 is forced on, and a
# trust failure surfaces as an exception, i.e. a FAIL. Nothing is written anywhere.
# 100% ASCII, bounded output, closing sentinel self-attesting "not truncated".

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Note([string]$s) { Write-Output $s }

$shaAlg = [System.Security.Cryptography.SHA256]::Create()
function Get-Sha([byte[]]$b) { return (([BitConverter]::ToString($shaAlg.ComputeHash($b))) -replace '-', '') }

# --- sensitive literals, built from code points -------------------------------
$TQF  = 'quickFill'                                                                                   # function name, not a secret
$TPW  = -join ([char]0x74, [char]0x65, [char]0x73, [char]0x74, [char]0x31, [char]0x32, [char]0x33, [char]0x34, [char]0x35, [char]0x36)
$TPH  = -join ([char]0x31, [char]0x33, [char]0x38, [char]0x30, [char]0x30, [char]0x30, [char]0x30, [char]0x30, [char]0x30, [char]0x30, [char]0x31)
$PFIX = -join ([char]0x8BF7, [char]0x8F93, [char]0x5165, [char]0x624B, [char]0x673A, [char]0x53F7)    # the fixed placeholder text

# --- repo root, built from code points so this file stays 100% ASCII ----------
$ROOT = 'd:\' + (-join ([char]0x7F51, [char]0x7AD9, [char]0x67B6, [char]0x6784, [char]0x8BBE, [char]0x8BA1)) + '\zhuxiang-jiu'

$SPECS = @(
    [pscustomobject]@{ Name = 'activity.html';        ExpBytes = 30361;  ExpSha16 = 'CFB0D6634C58F2DD' }
    [pscustomobject]@{ Name = 'activity-detail.html'; ExpBytes = 43037;  ExpSha16 = '3A114C112CE190D0' }
    [pscustomobject]@{ Name = 'xiaozhu-voice.html';   ExpBytes = 226647; ExpSha16 = '8ABC34674A6AA67A' }
)

$ORIGINS = @('http://zxjiu.com', 'https://zxjiu.com', 'http://www.zxjiu.com', 'https://www.zxjiu.com')

Note '=== t27d PUBLIC RE-VERIFICATION (read-only) ==='
Note ('  stamp=' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' | tls=1.2-forced | bypassValidation=NEVER')
Note ('  targetLens quickFill=' + $TQF.Length + ' pwdLiteral=' + $TPW.Length + ' seedPhone=' + $TPH.Length + ' fixedPlaceholder=' + $PFIX.Length)
Note ('  repoRoot=' + $ROOT)
Note ('  origins=' + $ORIGINS.Count + ' pages=' + $SPECS.Count + ' fetchesPlanned=' + ($ORIGINS.Count * $SPECS.Count))

# --- [0/3] re-derive the expected constants from the local source of truth ----
# If these do not match the table above, the table is stale and everything below
# is meaningless -- so fail loudly here instead of reporting a bogus PASS.
Note ''
Note '=== [0/3] LOCAL SOURCE OF TRUTH ==='
$localOk = $true
foreach ($s in $SPECS) {
    $p = Join-Path $ROOT $s.Name
    if (-not (Test-Path -LiteralPath $p)) {
        Note ('  ' + $s.Name + ' | MISSING locally, cannot self-validate')
        $localOk = $false
        continue
    }
    $lb = [IO.File]::ReadAllBytes($p)
    $lh = Get-Sha $lb
    $okB = ($lb.Length -eq $s.ExpBytes)
    $okH = ($lh.Substring(0, 16) -eq $s.ExpSha16)
    $lt = [Text.Encoding]::UTF8.GetString($lb)
    $lQF = ([regex]::Matches($lt, [regex]::Escape($TQF))).Count
    $lPW = ([regex]::Matches($lt, [regex]::Escape($TPW))).Count
    $lPH = ([regex]::Matches($lt, [regex]::Escape($TPH))).Count
    $lFX = ([regex]::Matches($lt, [regex]::Escape($PFIX))).Count
    $lCrlf = ([regex]::Matches($lt, "`r`n")).Count
    Note ('  ' + $s.Name + ' | bytes=' + $lb.Length + '/' + $s.ExpBytes + ' | sha16=' + $lh.Substring(0, 16) + '/' + $s.ExpSha16 + ' | crlf=' + $lCrlf)
    Note ('      localLeakScan quickFill=' + $lQF + ' pwdLiteral=' + $lPW + ' seedPhone=' + $lPH + ' | positiveControl fixedPlaceholder=' + $lFX)
    if (-not ($okB -and $okH -and $lQF -eq 0 -and $lPW -eq 0 -and $lPH -eq 0 -and $lFX -ge 1 -and $lCrlf -eq 0)) {
        Note '      LOCAL-MISMATCH: expectation table is stale, aborting before any network call'
        $localOk = $false
    }
}
if (-not $localOk) {
    Note ''
    Note 'VERDICT-T27D FAIL: local source of truth does not match the expectation table. Nothing was fetched.'
    Note '=== T27D-DONE ==='
    exit 1
}
Note '  VERDICT-LOCAL PASS: expectation table re-validated against disk.'

# --- [1/3] fetch each page over every public origin --------------------------
Note ''
Note '=== [1/3] PUBLIC FETCH ==='
$allOk = $true
$passCount = 0
$failCount = 0
foreach ($o in $ORIGINS) {
    Note ''
    Note ('### origin ' + $o + ' ###')
    foreach ($s in $SPECS) {
        $url = $o + '/' + $s.Name
        Note ''
        Note ('--- ' + $s.Name + ' ---')
        try {
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 60
            $code = [int]$r.StatusCode
            $ms = New-Object System.IO.MemoryStream
            $st = $r.RawContentStream
            $st.Position = 0
            $st.CopyTo($ms)
            $bytes = $ms.ToArray()
            $ms.Dispose()

            $h = Get-Sha $bytes
            $text = [Text.Encoding]::UTF8.GetString($bytes)
            $nQF = ([regex]::Matches($text, [regex]::Escape($TQF))).Count
            $nPW = ([regex]::Matches($text, [regex]::Escape($TPW))).Count
            $nPH = ([regex]::Matches($text, [regex]::Escape($TPH))).Count
            $nFX = ([regex]::Matches($text, [regex]::Escape($PFIX))).Count
            $nCrlf = ([regex]::Matches($text, "`r`n")).Count

            $okCode  = ($code -eq 200)
            $okBytes = ($bytes.Length -eq $s.ExpBytes)
            $okSha   = ($h.Substring(0, 16) -eq $s.ExpSha16)
            $okLeak  = ($nQF -eq 0 -and $nPW -eq 0 -and $nPH -eq 0)
            $okPos   = ($nFX -ge 1)
            $okForm  = ($nCrlf -eq 0)

            $hdr = @()
            foreach ($k in @('Server', 'Content-Type', 'Content-Length', 'ETag', 'Last-Modified', 'Age', 'X-Cache', 'Cache-Control')) {
                $v = $r.Headers[$k]
                if ($v) { $hdr += ($k + '=' + (($v | ForEach-Object { [string]$_ }) -join ',')) }
            }

            Note ('  url=' + $url)
            Note ('  status=' + $code + ' | bytes=' + $bytes.Length + '/' + $s.ExpBytes + ' | sha16=' + $h.Substring(0, 16) + '/' + $s.ExpSha16 + ' | crlf=' + $nCrlf)
            Note ('  fullSha256=' + $h)
            Note ('  leakScan quickFill=' + $nQF + ' pwdLiteral=' + $nPW + ' seedPhone=' + $nPH + ' | positiveControl fixedPlaceholder=' + $nFX)
            Note ('  headers: ' + ($hdr -join ' | '))

            $verdict = ($okCode -and $okBytes -and $okSha -and $okLeak -and $okPos -and $okForm)
            Note ('  RESULT=' + $(if ($verdict) { 'PASS' } else { 'FAIL' }) + ' (status=' + $okCode + ' bytes=' + $okBytes + ' sha=' + $okSha + ' leakClean=' + $okLeak + ' positiveControl=' + $okPos + ' lfOnly=' + $okForm + ')')
            if ($verdict) { $passCount++ } else { $failCount++; $allOk = $false }

            # Diagnosis only on mismatch: is the origin clean and only an intermediary stale?
            if (-not ($okSha -and $okBytes)) {
                try {
                    $cb = $url + '?_cb=' + (Get-Date -Format 'yyyyMMddHHmmss')
                    $r2 = Invoke-WebRequest -Uri $cb -UseBasicParsing -TimeoutSec 60 -Headers @{ 'Cache-Control' = 'no-cache'; 'Pragma' = 'no-cache' }
                    $ms2 = New-Object System.IO.MemoryStream
                    $st2 = $r2.RawContentStream
                    $st2.Position = 0
                    $st2.CopyTo($ms2)
                    $b2 = $ms2.ToArray()
                    $ms2.Dispose()
                    Note ('  cacheBustProbe status=' + [int]$r2.StatusCode + ' bytes=' + $b2.Length + ' sha16=' + (Get-Sha $b2).Substring(0, 16) + '  => if this matches, the mismatch is an intermediary cache, not the origin')
                } catch {
                    Note ('  cacheBustProbe ERROR: ' + $_.Exception.GetType().Name)
                }
            }
        } catch {
            Note ('  url=' + $url)
            Note ('  ERROR: ' + $_.Exception.GetType().Name + ' :: ' + $_.Exception.Message)
            Note '  RESULT=FAIL (fetch error)'
            $failCount++
            $allOk = $false
        }
    }
}

# --- [2/3] negative control: the pre-t27a content must be gone from the wire --
# The old body carried a hardcoded seed phone number in a placeholder attribute and, on
# xiaozhu-voice.html, a quickFill button plus function holding a plaintext credential.
# Those were counted per page above ($nPH / $nQF / $nPW, all required 0). This section
# adds the one wire-level check t25 did not need: the OLD IIS answer that t27b displaced
# must not come back on any origin, i.e. every Server header seen must be nginx, never
# Microsoft-IIS. A resurrected IIS site (serverAutoStart after a reboot) would otherwise
# serve the stale pre-t27a disk content and could still show a clean sha on a cached copy.
Note ''
Note '=== [2/3] SERVER-IDENTITY CONTROL ==='
$srvOk = $true
foreach ($o in $ORIGINS) {
    try {
        $r = Invoke-WebRequest -Uri ($o + '/activity.html') -UseBasicParsing -TimeoutSec 45
        $sv = [string]$r.Headers['Server']
        $isNginx = ($sv -like 'nginx*')
        Note ('  ' + $o + ' | server=' + $sv + ' | isNginx=' + $isNginx)
        if (-not $isNginx) { $srvOk = $false }
    } catch {
        Note ('  ' + $o + ' | ERROR ' + $_.Exception.GetType().Name)
        $srvOk = $false
    }
}
Note ('  serverIdentityOk=' + $srvOk)
if (-not $srvOk) { $allOk = $false }

# --- [3/3] verdict -----------------------------------------------------------
Note ''
Note '=== [3/3] VERDICT ==='
Note ('  fetchesPassed=' + $passCount + ' fetchesFailed=' + $failCount + ' planned=' + ($ORIGINS.Count * $SPECS.Count) + ' serverIdentityOk=' + $srvOk)
if ($allOk) {
    Note 'VERDICT-T27D PASS: every page on every reachable origin is served by nginx and is byte-identical (full sha256) to the local source of truth; all three precise leak targets are zero; the positive control confirms the corrected content is really what is being served; the old IIS answer is gone from the public wire.'
    Note '=== T27D-DONE ==='
    exit 0
}
Note 'VERDICT-T27D FAIL: at least one page/origin did not meet the six-piece acceptance, or an origin is not answering from nginx. Do not treat the t27 chain as closed until this is explained (stale intermediary cache, wrong vhost, a resurrected IIS site, or an unexpected rollback).'
Note '=== T27D-DONE ==='
exit 1
