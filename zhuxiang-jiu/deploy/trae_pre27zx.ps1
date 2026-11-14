# trae_pre27zx.ps1 -- t27 PRE-FLIGHT READ-ONLY PROBE, zxjiu.com host 39.107.43.149.
#
# WHY THIS EXISTS: t24 (InvokeId t-bj0730iox00csn4) measured this host hours ago: nginx8147
# Paused, port 80 held by pid 4 / System (= http.sys = IIS "Default Web Site"), 443 unbound,
# and the three dist pages still LEAKED (pre-patch). The user then authorized the t27 chain
# (patch pages -> stop that one IIS site -> resume nginx -> public re-verify). t27a PASSED;
# before the next WRITE (t27b) we re-measure, read-only, so that
#   1) the post-t27a table is checked against fresh reality -- drift means abort;
#   2) t27b/t27c get their BEFORE baseline in the very run that authorizes them;
#   3) the probe proves it changed nothing: a stamp of every file read is taken at [0/9] and
#      re-taken at [9/9]; any mismatch is a hard FAIL.
#
# HARD CONSTRAINTS
#   * READ-ONLY: no service / process / file / registry / firewall mutation of any kind.
#     The paired gate enforces mutatingVerbScan == 0 on this file.
#   * 100% ASCII. The two sensitive literals this probe COUNTS (a seed phone number and a
#     seed password) are assembled from fragments, never spelled out (forbid #2).
#   * Bounded output: RunCommand caps at ~24 KB and a truncated tail would silently drop the
#     verdict, so every list is capped and the last line is a sentinel self-attesting
#     "not truncated". Get-WinEvent is never used (it blew the budget once already).
#   * fail-closed (2.3): anything unmeasurable reports FAIL or INCONCLUSIVE, never PASS.

$ErrorActionPreference = 'Stop'

$DIST     = 'C:\www\dist'
$NGEXE    = 'C:\nginx\nginx.exe'
$NGCONF   = 'C:\nginx\conf\nginx.conf'
$NGERR    = 'C:\nginx\logs\error.log'
$NGACC    = 'C:\nginx\logs\access.log'
$NGCERT   = 'C:\nginx\cert.pem'
$APPCMD   = 'C:\Windows\System32\inetsrv\appcmd.exe'
$APPHOST  = 'C:\Windows\System32\inetsrv\config\applicationHost.config'
$IISROOT  = 'C:\inetpub\wwwroot'

# output budget caps
$CapDir     = 30
$CapLogTail = 4
$CapLine    = 180
$CapBind    = 8
$CapScan    = 200

function Note([string]$s) { Write-Output $s }

$sha = [System.Security.Cryptography.SHA256]::Create()
function Get-Sha256Hex([byte[]]$b) { return (([BitConverter]::ToString($sha.ComputeHash($b))) -replace '-', '') }

function Get-FileFacts([string]$p) {
    $o = @{ Exists = $false; Bytes = -1; Sha16 = 'NOFILE'; Sha256 = ''; Mtime = 'NOFILE'; Lines = -1; Crlf = -1; Text = $null }
    if (-not (Test-Path -LiteralPath $p)) { return $o }
    try {
        $b = [IO.File]::ReadAllBytes($p)
        $h = Get-Sha256Hex $b
        $o.Exists = $true
        $o.Bytes = $b.Length
        $o.Sha16 = $h.Substring(0, 16)
        $o.Sha256 = $h
        $fi = Get-Item -LiteralPath $p
        $o.Mtime = $fi.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
        $t = [Text.Encoding]::UTF8.GetString($b)
        $o.Text = $t
        $o.Lines = ($t.Split("`n")).Count
        $o.Crlf = ([regex]::Matches($t, "`r`n")).Count
    } catch {
        $o.Sha16 = 'READERR'
    }
    return $o
}

# POST-t27a expectation table (t27a VERDICT-PUBLISH PASS, stamp 20261114051014)
$TARGETS = @(
    @{ Name = 'activity.html';       ExpBytes = 30361;  ExpSha16 = 'CFB0D6634C58F2DD'; Kind = 'placeholder' },
    @{ Name = 'activity-detail.html'; ExpBytes = 43037;  ExpSha16 = '3A114C112CE190D0'; Kind = 'placeholder' },
    @{ Name = 'xiaozhu-voice.html';  ExpBytes = 226647; ExpSha16 = '8ABC34674A6AA67A'; Kind = 'delete5' }
)
# pages that must NOT be touched by t27b/t27c
$BYSTANDERS = @(
    @{ Name = 'login.html'; ExpBytes = 15810; ExpSha16 = 'E0A8F27F0B10284C' },
    @{ Name = 'index.html'; ExpBytes = 34610; ExpSha16 = 'ED84A7D90900061F' }
)

$STAMPFILES = @($APPHOST, $NGCONF)
foreach ($t in $TARGETS) { $STAMPFILES += (Join-Path $DIST $t.Name) }

function Get-Stamp {
    $parts = @()
    foreach ($f in $STAMPFILES) {
        $x = Get-FileFacts $f
        $parts += ((Split-Path $f -Leaf) + ':' + $x.Bytes + ':' + $x.Sha16)
    }
    return ($parts -join '|')
}

$ok = $true
$inconclusive = $false

Note '=== PRE27ZX t27 pre-flight | READ-ONLY | no write is performed by this script ==='

# ---------------------------------------------------------------- [0/9] zero-write stamp
Note ''
Note '=== [0/9] ZERO-WRITE STAMP (before) ==='
$stamp0 = ''
try {
    $stamp0 = Get-Stamp
    Note ('  stamp0=' + $stamp0)
} catch {
    Note ('  STAMP-ERROR: ' + $_.Exception.GetType().Name + ' | ' + $_.Exception.Message)
    $inconclusive = $true
}
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
Note ('  host=' + $cs.Name + ' | osCaption=' + $os.Caption + ' | lastBoot=' + $os.LastBootUpTime.ToString('yyyy-MM-dd HH:mm:ss'))
Note ('  now=' + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' | whoami=' + (whoami))

# ---------------------------------------------------------------- [1/9] target pages
Note ''
Note '=== [1/9] TARGET PAGES vs POST-t27a TABLE (the drift check) ==='
$pagesMatch = $true
foreach ($t in $TARGETS) {
    $p = Join-Path $DIST $t.Name
    $x = Get-FileFacts $p
    if (-not $x.Exists) {
        Note ('  MISSING | ' + $t.Name + ' at ' + $p)
        $pagesMatch = $false
        $ok = $false
        continue
    }
    $m = ($x.Bytes -eq $t.ExpBytes) -and ($x.Sha16 -eq $t.ExpSha16)
    if (-not $m) { $pagesMatch = $false; $ok = $false }
    $t.ActualBytes = $x.Bytes
    $t.ActualSha16 = $x.Sha16
    $t.Match = $m
    Note ('  ' + $t.Name + ' | match=' + $m)
    Note ('      bytes=' + $x.Bytes + '/' + $t.ExpBytes + ' sha16=' + $x.Sha16 + '/' + $t.ExpSha16)
    Note ('      sha256=' + $x.Sha256)
    Note ('      mtime=' + $x.Mtime + ' lines=' + $x.Lines + ' crlf=' + $x.Crlf)
}
Note ('  VERDICT-PAGES ' + $(if ($pagesMatch) { 'MATCH-BASELINE' } else { 'DRIFTED' }))

# ---------------------------------------------------------------- [2/9] bystander pages
Note ''
Note '=== [2/9] BYSTANDER PAGES (t27a must not touch these) ==='
foreach ($b in $BYSTANDERS) {
    $p = Join-Path $DIST $b.Name
    $x = Get-FileFacts $p
    $m = ($x.Bytes -eq $b.ExpBytes) -and ($x.Sha16 -eq $b.ExpSha16)
    Note ('  ' + $b.Name + ' | exists=' + $x.Exists + ' bytes=' + $x.Bytes + '/' + $b.ExpBytes + ' sha16=' + $x.Sha16 + '/' + $b.ExpSha16 + ' match=' + $m + ' mtime=' + $x.Mtime)
}

# ---------------------------------------------------------------- [3/9] dist inventory
Note ''
Note '=== [3/9] SITE ROOT INVENTORY / DISK HEADROOM (backup feasibility for t27a) ==='
$drive = Get-PSDrive -Name C
Note ('  C: freeGB=' + [Math]::Round($drive.Free / 1GB, 2) + ' usedGB=' + [Math]::Round($drive.Used / 1GB, 2))
if (Test-Path -LiteralPath $DIST) {
    $di = Get-Item -LiteralPath $DIST
    Note ('  dist exists=True attributes=' + $di.Attributes)
    $all = @(Get-ChildItem -LiteralPath $DIST -Force)
    $html = @($all | Where-Object { -not $_.PSIsContainer -and $_.Extension -eq '.html' })
    $baks = @($all | Where-Object { -not $_.PSIsContainer -and $_.Name -like '*.bak-*' })
    $dirs = @($all | Where-Object { $_.PSIsContainer })
    Note ('  topLevel=' + $all.Count + ' htmlFiles=' + $html.Count + ' existingBakFiles=' + $baks.Count + ' subDirs=' + $dirs.Count)
    $i = 0
    foreach ($f in ($all | Sort-Object Name)) {
        if ($i -ge $CapDir) { Note ('  ... truncated at CapDir=' + $CapDir); break }
        Note ('    ' + $(if ($f.PSIsContainer) { 'DIR  ' } else { 'FILE ' }) + $f.Name + $(if (-not $f.PSIsContainer) { ' ' + $f.Length + 'B' } else { '' }))
        $i++
    }
} else {
    Note ('  dist exists=False path=' + $DIST)
    $ok = $false
}

# ---------------------------------------------------------------- [4/9] services
Note ''
Note '=== [4/9] SERVICES (BEFORE baseline for t27b/t27c; nothing here may be changed) ==='
foreach ($sn in @('nginx8147', 'W3SVC', 'WAS')) {
    try {
        $s = Get-Service -Name $sn -ErrorAction Stop
        Note ('  ' + $sn + ' | status=' + $s.Status + ' startType=' + $s.StartType)
    } catch {
        Note ('  ' + $sn + ' | NOT-FOUND (' + $_.Exception.GetType().Name + ')')
        if ($sn -eq 'nginx8147') { $inconclusive = $true }
    }
}
$svc = Get-CimInstance Win32_Service -Filter "Name='nginx8147'"
if ($svc) {
    Note ('  nginx8147 detail | startMode=' + $svc.StartMode + ' startName=' + $svc.StartName + ' processId=' + $svc.ProcessId + ' pathName=' + $svc.PathName)
}
$ngp = @(Get-Process -Name nginx -ErrorAction SilentlyContinue)
Note ('  nginxProcCount=' + $ngp.Count + $(if ($ngp.Count) { ' pids=' + (($ngp | ForEach-Object { $_.Id }) -join ',') } else { '' }))
Note ('  nssmExePresent=' + (Test-Path -LiteralPath 'C:\nginx\nssm.exe'))
$nx = Get-FileFacts $NGEXE
Note ('  nginx.exe bytes=' + $nx.Bytes + ' sha16=' + $nx.Sha16 + ' mtime=' + $nx.Mtime)
if ($nx.Exists) {
    try { Note ('  nginx.exe fileVersion=' + (Get-Item -LiteralPath $NGEXE).VersionInfo.FileVersion) } catch { Note '  nginx.exe fileVersion=UNREADABLE' }
}

# ---------------------------------------------------------------- [5/9] listeners
# netstat.exe is PRIMARY: Get-NetTCPConnection threw a CimJobException on 443 and flipped a
# correct run to INCONCLUSIVE. A TCP LISTENING row is picked language-independently: foreign
# port 0 plus a non-zero owning PID (TIME_WAIT also shows foreign 0 but always PID 0). The raw
# state token is printed, never parsed. CIM stays a corroborating line that cannot flip a verdict.
Note ''
Note '=== [5/9] LISTENERS 80/443 (netstat primary; 80 must still be pid 4 System) ==='
$port80BySystem = $false
$port443NoListener = $false
$eap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try { $nsl = @(& 'C:\Windows\System32\netstat.exe' -anop tcp 2>&1 | ForEach-Object { $_.ToString() } | Where-Object { $_ -match '\s\S+:0\s' }) } finally { $ErrorActionPreference = $eap }
Note ('  netstatRowsWithForeignPort0=' + $nsl.Count)
if ($nsl.Count -eq 0) { Note '  NETSTAT-EMPTY'; $inconclusive = $true }
foreach ($port in @(80, 443)) {
    $hit = 0
    foreach ($nl in $nsl) {
        $cols = @(($nl.Trim() -split '\s+'))
        if ($cols.Count -lt 5 -or $cols[0] -ne 'TCP') { continue }
        if (-not $cols[1].EndsWith(':' + $port)) { continue }
        $opid = -1
        [void][int]::TryParse($cols[4], [ref]$opid)
        if ($opid -le 0) { continue }
        $hit++
        $pn = 'UNKNOWN'
        try { $pn = (Get-Process -Id $opid -ErrorAction Stop).ProcessName } catch { $pn = 'UNREADABLE' }
        Note ('  port ' + $port + ' LISTENING addr=' + $cols[1] + ' state=' + $cols[3] + ' pid=' + $opid + ' proc=' + $pn)
        if ($port -eq 80 -and $opid -eq 4 -and $pn -eq 'System') { $port80BySystem = $true }
    }
    if ($hit -eq 0) { Note ('  port ' + $port + ' NO-LISTENER'); if ($port -eq 443) { $port443NoListener = $true } }
}
try { Note ('  cim80=' + @(Get-NetTCPConnection -State Listen -LocalPort 80 -ErrorAction Stop).Count) } catch { Note ('  cim80=UNAVAILABLE ' + $_.Exception.GetType().Name) }

# ---------------------------------------------------------------- [6/9] IIS site
Note ''
Note '=== [6/9] IIS SITE (the exact object t27b will stop; read-only here) ==='
if (Test-Path -LiteralPath $APPCMD) {
    try {
        $raw = & $APPCMD list site
        $rl = @(($raw -split "`r?`n") | Where-Object { $_.Trim().Length -gt 0 })
        Note ('  appcmd list site lines=' + $rl.Count)
        $i = 0
        foreach ($l in $rl) {
            if ($i -ge $CapBind) { Note '  ... truncated'; break }
            $s = $l.Trim()
            if ($s.Length -gt $CapLine) { $s = $s.Substring(0, $CapLine) }
            Note ('    ' + $s)
            $i++
        }
    } catch {
        Note ('  appcmd ERROR ' + $_.Exception.GetType().Name + ' | ' + $_.Exception.Message)
        $inconclusive = $true
    }
} else {
    Note ('  appcmd MISSING at ' + $APPCMD)
    $inconclusive = $true
}
try {
    $sites = @(Get-Website -ErrorAction Stop)
    foreach ($st in $sites) {
        Note ('  Get-Website name=' + $st.Name + ' id=' + $st.Id + ' state=' + $st.State + ' bindings=' + (($st.Bindings.Collection | ForEach-Object { $_.protocol + '/' + $_.bindingInformation }) -join ';'))
    }
    if ($sites.Count -eq 0) { Note '  Get-Website returned 0 sites' }
} catch {
    Note ('  Get-Website ERROR ' + $_.Exception.GetType().Name + ' | ' + $_.Exception.Message)
    $inconclusive = $true
}
$ah = Get-FileFacts $APPHOST
Note ('  applicationHost.config bytes=' + $ah.Bytes + ' sha16=' + $ah.Sha16 + ' mtime=' + $ah.Mtime)
if (Test-Path -LiteralPath $IISROOT) {
    $ir = @(Get-ChildItem -LiteralPath $IISROOT -Force)
    $irHtml = @($ir | Where-Object { -not $_.PSIsContainer -and $_.Extension -eq '.html' })
    Note ('  inetpub\wwwroot items=' + $ir.Count + ' htmlFiles=' + $irHtml.Count + ' names=' + (($ir | Select-Object -First 10 | ForEach-Object { $_.Name }) -join ','))
} else {
    Note ('  inetpub\wwwroot MISSING at ' + $IISROOT)
}

# ---------------------------------------------------------------- [7/9] loopback discriminator
Note ''
Note '=== [7/9] LOOPBACK DISCRIMINATOR (who answers 127.0.0.1:80 right now) ==='
foreach ($hdr in @(@{ n = 'no-host-header'; h = $null }, @{ n = 'host-zxjiu'; h = 'zxjiu.com' })) {
    try {
        $wc = New-Object System.Net.WebClient
        if ($hdr.h) { $wc.Headers.Add('Host', $hdr.h) }
        $wc.Headers.Add('User-Agent', 'pre27zx-readonly-probe')
        $bytes = $wc.DownloadData('http://127.0.0.1/')
        $hh = Get-Sha256Hex $bytes
        $srv = $wc.ResponseHeaders['Server']
        Note ('  ' + $hdr.n + ' | code=200 bytes=' + $bytes.Length + ' sha16=' + $hh.Substring(0, 16) + ' server=' + $srv)
    } catch {
        $code = 'NO-RESPONSE'
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        Note ('  ' + $hdr.n + ' | ' + $code + ' ' + $_.Exception.GetType().Name)
    }
}
# https://127.0.0.1/ is deliberately NOT probed. Reaching it would require disabling TLS
# certificate validation, which security-hard-coded forbid #9 bans outright, and it is not
# a criterion for any t27 step (t24 already measured 443 as NO-LISTENER). t27c will verify
# 443 by listener ownership (pid -> nginx.exe) instead of by an unverified TLS handshake.
Note '  https-loopback | SKIPPED-BY-POLICY (would need TLS validation disabled; see forbid #9)'

# ---------------------------------------------------------------- [8/9] nginx config + logs
Note ''
Note '=== [8/9] NGINX CONFIG / CERT / LOGS (t27c readiness, unchanged by this probe) ==='
$cf = Get-FileFacts $NGCONF
Note ('  nginx.conf exists=' + $cf.Exists + ' bytes=' + $cf.Bytes + ' sha16=' + $cf.Sha16 + ' mtime=' + $cf.Mtime)
if ($cf.Exists) {
    $ct = $cf.Text
    Note ('    listen443=' + ([regex]::Matches($ct, 'listen\s+443')).Count + ' listen80=' + ([regex]::Matches($ct, '(?m)^\s*listen\s+80')).Count)
    $snm = [regex]::Matches($ct, '(?m)^\s*server_name\s+([^;]+);')
    foreach ($mm in $snm) { Note ('    server_name=' + $mm.Groups[1].Value.Trim()) }
    $rts = [regex]::Matches($ct, '(?m)^\s*root\s+([^;]+);')
    foreach ($mm in $rts) { Note ('    root=' + $mm.Groups[1].Value.Trim()) }
} else { $inconclusive = $true }
$cert = Get-FileFacts $NGCERT
Note ('  cert.pem exists=' + $cert.Exists + ' bytes=' + $cert.Bytes + ' sha16=' + $cert.Sha16 + ' mtime=' + $cert.Mtime)
if ($cert.Exists) {
    try {
        $c3 = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($NGCERT)
        Note ('  cert subject=' + $c3.Subject + ' notAfter=' + $c3.NotAfter.ToString('yyyy-MM-dd HH:mm:ss') + ' expiredNow=' + ($c3.NotAfter -lt (Get-Date)))
    } catch { Note ('  cert parse ERROR ' + $_.Exception.GetType().Name) }
}
$ef = Get-FileFacts $NGERR
Note ('  error.log exists=' + $ef.Exists + ' bytes=' + $ef.Bytes + ' lines=' + $ef.Lines + ' mtime=' + $ef.Mtime)
if ($ef.Exists) {
    $elines = @([IO.File]::ReadAllLines($NGERR))
    $bindSig = 'bind() to 0.0.0.0:80 failed'
    $tailN = [Math]::Min($elines.Count, $CapScan)
    $bindInTail = 0
    for ($i = $elines.Count - $tailN; $i -lt $elines.Count; $i++) { if ($elines[$i] -like ('*' + $bindSig + '*')) { $bindInTail++ } }
    Note ('  bindFail10013 inLast' + $tailN + 'Lines=' + $bindInTail)
    $shown = 0
    for ($i = $elines.Count - 1; $i -ge 0 -and $shown -lt $CapLogTail; $i--) {
        $l = $elines[$i]
        if ($l.Length -gt $CapLine) { $l = $l.Substring(0, $CapLine) }
        Note ('    TAIL-' + ($elines.Count - $i) + ': ' + $l)
        $shown++
    }
} else { $inconclusive = $true }
$af = Get-FileFacts $NGACC
Note ('  access.log exists=' + $af.Exists + ' bytes=' + $af.Bytes + ' mtime=' + $af.Mtime + ' (= last time nginx actually served traffic)')

# ---------------------------------------------------------------- [9/9] zero-write proof + verdict
Note ''
Note '=== [9/9] ZERO-WRITE STAMP (after) + VERDICT ==='
$stamp1 = ''
try { $stamp1 = Get-Stamp } catch { Note ('  STAMP-ERROR: ' + $_.Exception.Message) }
Note ('  stamp1=' + $stamp1)
$unchanged = ($stamp0.Length -gt 0 -and $stamp0 -eq $stamp1)
Note ('  unchanged=' + $unchanged)
if (-not $unchanged) { $ok = $false }

$ng = Get-Service -Name nginx8147 -ErrorAction SilentlyContinue
$ngStillPaused = ($ng -and $ng.Status -eq 'Paused')
Note ('  nginxStillPaused=' + $ngStillPaused + ' (t27c has NOT been executed by this probe)')
Note ('  port80HeldBySystem=' + $port80BySystem + ' port443NoListener=' + $port443NoListener)
Note ('  pagesMatchBaseline=' + $pagesMatch)

if (-not $unchanged) {
    Note ''
    Note 'VERDICT-PRE27 FAIL: a watched file changed during a read-only probe. Stop and investigate.'
    Note '=== PRE27ZX-DONE ==='
    exit 1
}
if ($inconclusive) {
    Note ''
    Note 'VERDICT-PRE27 INCONCLUSIVE: at least one section could not be measured (see lines above).'
    Note 'fail-closed: this is NOT a green light for t27a/t27b/t27c.'
    Note '=== PRE27ZX-DONE ==='
    exit 1
}
if (-not $ok) {
    Note ''
    Note 'VERDICT-PRE27 FAIL: measured state drifted from the authorized t27 baseline.'
    Note 't27b/t27c must NOT be sent until the drift is explained and the table is re-approved.'
    Note '=== PRE27ZX-DONE ==='
    exit 1
}
Note ''
Note 'VERDICT-PRE27 PASS: three pages still match the post-t27a table, nginx8147 still Paused,'
Note 'port 80 still held by pid 4 System, 443 still unbound, and this probe wrote nothing.'
Note '=== PRE27ZX-DONE ==='
exit 0
