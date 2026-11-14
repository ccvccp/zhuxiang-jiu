# trae_sasfix2.ps1 -- retry of trae_sasfix.ps1. Same single-attribute fix, corrected gate.
#
# AUTHORIZATION (user, verbatim): "please fix the problem where serverAutoStart grabs port 80
# on reboot ... do not break ECS / TRAE CN development". Minimal, reversible, backup-first.
#
# WHY THIS IS A RETRY. Run 1 (trae_sasfix.ps1) executed `appcmd set site "Default Web Site"
# /serverAutoStart:false`, appcmd returned exitCode=0 and Chinese "SITE object changed", the
# FILE reader confirmed rawAttrs="serverAutoStart="false"", yet the RUNTIME reader
# (`appcmd list site /text:*`, last anchored match) still reported true. The gate judged FAIL
# and rolled the attribute back to true. So the host is still vulnerable.
#
# WHAT RUN 1 GOT WRONG -- the gate, not the fix:
#   * Run 1 asserted on the RUNTIME view. But W3SVC reads applicationHost.config at boot.
#     The FILE is the only thing that decides whether the site auto-starts after a reboot,
#     so the file must be the authority. Run 1 already proved the file was written correctly
#     and then threw that evidence away.
#   * Run 1 read `/text:*` and kept the LAST anchored match. Read-only probe 2 measured 165
#     lines with exactly 2 lines mentioning serverAutoStart (indices 8 and 62), both "true".
#     Two hypotheses still fit that observation and probe 2 could not separate them, because
#     it ran AFTER the rollback: (a) index 62 is a constant schema/default node that never
#     tracks the site, so "last match" is structurally wrong; (b) the runtime view simply
#     lagged the file write. Section [5/8] now dumps 3 lines of context around EVERY hit, so
#     this run produces the structural evidence either way.
#   * Run 1 read the runtime view ONCE, immediately. Section [4/8] now re-reads the file at
#     0/2/5/10 s and also re-reads the runtime view at t=0 and t=10, which separates a
#     propagation lag from a genuinely wrong value, and detects WAS writing the file back.
#
# GATE POLICY (the actual fix to the fix): assertions are split in two classes.
#   HARD -- file authority (regex + XML DOM, timed, sha-stable) and the invariant set
#           (services, start types, ports, site state, bindings, three published pages).
#           Any HARD red => rollback, fail-closed (security-hard-coded 2.3).
#   SOFT -- appcmd direct /text:serverAutoStart, ServerManager, /text:* shape. These are
#           DIAGNOSTICS. A SOFT divergence prints WARN and never triggers a rollback, because
#           a cached runtime view cannot make the host grab port 80 -- the file does that.
#
# SCOPE -- what this script may change, exhaustively:
#   * the serverAutoStart attribute of the IIS site named "Default Web Site"
#   * one new backup file inside C:\ProgramData\zxjiu-backup\
# OFF LIMITS, self-attested by measurement in section [8/8]:
#   * any Windows service start type -- no Set-Service, no sc config. W3SVC stays Automatic,
#     WAS stays Manual, nginx8147 stays Automatic.
#   * the running state of W3SVC and WAS -- never stopped, never restarted.
#   * the site binding list -- removing http/*:80 was explicitly vetoed by the user.
#   * the site runtime state -- Stopped now, Stopped after. This script never runs
#     `appcmd start site`.
#   * nssm -- no install / set / remove. Re-registering services is Plan B, which is forbidden.
#   * C:\nginx\* and C:\www\dist -- hashed, never written.
#
# WHY netstat.exe IS THE PRIMARY PORT SOURCE: an earlier probe threw a CimJobException on a
# port query and flipped a fully-correct run to INCONCLUSIVE. netstat cannot raise one.
#
# NO EXPECTED VALUE IS GUESSED: every $EXP constant below was measured read-only on this same
# host by trae_probe_sas.ps1 (pre-fix) and trae_probe_sas2.ps1 (post-rollback, i.e. NOW).
# Note AppHostBytes is 47145, not the 47122 of the pristine baseline: run 1's rollback wrote
# serverAutoStart="true" explicitly where the attribute used to be ABSENT. Semantically
# identical (the IIS default is true), 23 bytes longer. Two hash-verified 47122 restore points
# remain in C:\ProgramData\zxjiu-backup\ if a byte-level restore is ever wanted.
#
# BACKUP FIRST (security-hard-coded 2.4): the config is copied and hash-compared BEFORE the
# attribute is set. No hash-verified backup => no change. The backup is deliberately NOT
# written inside C:\Windows\System32\inetsrv\config\, because IIS watches that folder.
#
# Paths and the site name are a HARDCODED WHITELIST; nothing is parameterized, so there is no
# path-traversal surface (security-hard-coded 1.8).
# 100% ASCII, bounded output, closing sentinel self-attesting "not truncated".

$ErrorActionPreference = 'Stop'

$SITENAME = 'Default Web Site'
$APPCMD   = 'C:\Windows\System32\inetsrv\appcmd.exe'
$APPHOST  = 'C:\Windows\System32\inetsrv\config\applicationHost.config'
$NETSTAT  = 'C:\Windows\System32\netstat.exe'
$SC       = 'C:\Windows\System32\sc.exe'
$SMR      = 'C:\Windows\System32\inetsrv\Microsoft.Web.Administration.dll'
$BAKDIR   = 'C:\ProgramData\zxjiu-backup'
$DIST     = 'C:\www\dist'
$STAMP    = Get-Date -Format 'yyyyMMddHHmmss'
$BAKFILE  = $BAKDIR + '\applicationHost.config.bak-sas2-' + $STAMP
$SETARGS  = @('set', 'site', $SITENAME, '/serverAutoStart:false')
$UNDOARGS = @('set', 'site', $SITENAME, '/serverAutoStart:true')
$DISTFILES = @('activity.html', 'activity-detail.html', 'xiaozhu-voice.html')
$DELAYS    = @(0, 2, 5, 10)

# Measured, not remembered. Sources: trae_probe_sas.ps1 and trae_probe_sas2.ps1.
$EXP = @{
    AppHostBytes  = 47145
    AppHostSha16  = '48741B64A23A824C'
    SiteElements  = 1
    SiteId        = 1
    Bindings      = 'http/*:80:'
    SiteState     = 'Stopped'
    FileSasBefore = 'true'
    FileSasAfter  = 'false'
    W3Svc         = 'Running'
    Was           = 'Running'
    Nginx         = 'Running'
    Port80Proc    = 'nginx'
    Port443Proc   = 'nginx'
}
$EXPDIST = @{
    'activity.html'        = @{ Bytes = 30361;  Sha16 = 'CFB0D6634C58F2DD' }
    'activity-detail.html' = @{ Bytes = 43037;  Sha16 = '3A114C112CE190D0' }
    'xiaozhu-voice.html'   = @{ Bytes = 226647; Sha16 = '8ABC34674A6AA67A' }
}

$CapNative = 20
$CapCtx    = 6

$errs = @()
$warns = @()
function Fail([string]$s) { $Script:errs += $s }
function WarnAdd([string]$s) { $Script:warns += $s }
function Note([string]$s) { Write-Output $s }

# HARD assertion: a red here triggers rollback.
function Assert([bool]$cond, [string]$name, [string]$got, [string]$want) {
    if ($cond) { Note ('  PASS ' + $name + ' = ' + $got) }
    else {
        Note ('  FAIL ' + $name + ' got=' + $got + ' want=' + $want)
        Fail ($name + ' got=' + $got + ' want=' + $want)
    }
}

# SOFT assertion: diagnostic only. Never triggers rollback. A cached runtime view cannot make
# the host grab port 80 on reboot -- applicationHost.config does.
function AssertSoft([bool]$cond, [string]$name, [string]$got, [string]$want) {
    if ($cond) { Note ('  PASS(soft) ' + $name + ' = ' + $got) }
    else {
        Note ('  WARN(soft) ' + $name + ' got=' + $got + ' want=' + $want + ' -- diagnostic, no rollback')
        WarnAdd ($name + ' got=' + $got + ' want=' + $want)
    }
}

$sha = [System.Security.Cryptography.SHA256]::Create()
function Get-Sha256Hex([byte[]]$b) { return (([BitConverter]::ToString($sha.ComputeHash($b))) -replace '-', '') }

function Get-FileFacts([string]$p) {
    $o = @{ Exists = $false; Bytes = -1; Sha16 = 'NOFILE'; Mtime = 'NOFILE' }
    if (-not (Test-Path -LiteralPath $p)) { return $o }
    try {
        $b = [IO.File]::ReadAllBytes($p)
        $o.Exists = $true
        $o.Bytes  = $b.Length
        $o.Sha16  = (Get-Sha256Hex $b).Substring(0, 16)
        $o.Mtime  = (Get-Item -LiteralPath $p).LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
    } catch { $o.Sha16 = 'READERR' }
    return $o
}

# Native-exe wrapper. Under $ErrorActionPreference='Stop' a 2>&1 merge turns stderr lines into
# error records and aborts the run, so EAP is relaxed for the call only and restored in finally.
# The parameter is $ArgList, never $Args -- $args is a PowerShell automatic variable and
# shadowing it once already produced a silent no-op.
function Invoke-Native {
    param([string]$Exe, [string[]]$ArgList)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $o = @(& $Exe @ArgList 2>&1 | ForEach-Object { $_.ToString() })
        return @{ ExitCode = $LASTEXITCODE; Out = $o }
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Show-Native([string]$tag, $res, [int]$cap) {
    Note ('  ' + $tag + ' exitCode=' + $res.ExitCode + ' lines=' + @($res.Out).Count)
    $i = 0
    foreach ($ln in $res.Out) {
        if ($i -ge $cap) { Note ('    ... truncated at cap=' + $cap); break }
        Note ('    | ' + $ln)
        $i++
    }
}

# EndsWith(':' + $port) on the BARE local-address token, so :80 cannot match :8080. It must not
# be a regex: $parts comes from ($ln.Trim() -split '\s+'), so $parts[1] carries NO leading
# space, and an older -notmatch (' :' + $port + '$') never matched anything (false green).
function Get-PortListeners([int]$port) {
    $rows = @()
    try {
        $r = Invoke-Native $NETSTAT @('-ano')
        foreach ($ln in $r.Out) {
            if ($ln -notmatch 'LISTENING') { continue }
            $parts = @(($ln.Trim()) -split '\s+')
            if ($parts.Count -lt 5) { continue }
            if (-not $parts[1].EndsWith(':' + $port)) { continue }
            $pname = 'UNKNOWN'
            try { $pname = (Get-Process -Id ([int]$parts[4]) -ErrorAction Stop).ProcessName } catch { $pname = 'NOACCESS' }
            $rows += @{ Proto = $parts[0]; Local = $parts[1]; ProcId = $parts[4]; ProcName = $pname }
        }
    } catch {
        Note ('  netstat QUERY-ERROR port ' + $port + ': ' + $_.Exception.GetType().Name + ' | ' + $_.Exception.Message)
    }
    # `return , $rows` emits the array as ONE pipeline item so an empty result survives the
    # boundary. Callers MUST assign bare ($p = Get-PortListeners 80); wrapping the CALL in
    # @(...) double-wraps it and .Count becomes a constant 1.
    return , $rows
}

function Show-Port([string]$label, $rows) {
    if (@($rows).Count -eq 0) { Note ('  ' + $label + ': NO-LISTENER'); return }
    $i = 0
    foreach ($x in $rows) {
        if ($i -ge $CapNative) { Note ('  ... truncated'); break }
        Note ('  ' + $label + ': ' + $x.Proto + ' ' + $x.Local + ' LISTENING pid=' + $x.ProcId + ' proc=' + $x.ProcName)
        $i++
    }
}

function Get-PortOwner([int]$port) {
    $rows = Get-PortListeners $port
    $n = @($rows).Count
    if ($n -eq 0) { return 'NO-LISTENER' }
    $first = @($rows)[0]
    return $first.ProcName
}

function Get-PortCount([int]$port) { return @((Get-PortListeners $port)).Count }

# FILE READER A -- regex over <site> elements. This is a real authority: W3SVC parses this file
# at boot. $o.Sas defaults to the impossible sentinel 'NO-SITE-MATCH', never to 'true' or
# 'false', so a failed match can never masquerade as a passing assertion.
$SITE_RE = '(?s)<site\s+name="([^"]+)"\s+id="(\d+)"([^>]*?)(/>|>(.*?)</site>)'
function Get-FileSas {
    $o = @{ SiteElements = 0; Sas = 'NO-SITE-MATCH'; RawAttrs = ''; Bytes = -1; Sha16 = 'NOFILE'; Err = '' }
    try {
        $b = [IO.File]::ReadAllBytes($APPHOST)
        $o.Bytes = $b.Length
        $o.Sha16 = (Get-Sha256Hex $b).Substring(0, 16)
        $txt = [Text.Encoding]::UTF8.GetString($b)
        $ms = [regex]::Matches($txt, $SITE_RE)
        $o.SiteElements = $ms.Count
        foreach ($m in $ms) {
            if ($m.Groups[1].Value -ne $SITENAME) { continue }
            $o.RawAttrs = $m.Groups[3].Value.Trim()
            $mm = [regex]::Match($o.RawAttrs, 'serverAutoStart\s*=\s*"([^"]+)"')
            if ($mm.Success) { $o.Sas = $mm.Groups[1].Value } else { $o.Sas = 'ABSENT' }
        }
    } catch { $o.Sas = 'PARSE-ERR'; $o.Err = $_.Exception.GetType().Name }
    return $o
}

# FILE READER B -- XML DOM, structurally independent of reader A's regex.
function Get-XmlSas {
    $o = @{ Count = 0; Sas = 'NO-SITE-MATCH'; Err = '' }
    try {
        $xd = New-Object System.Xml.XmlDocument
        $xd.Load($APPHOST)
        $ns = @(Select-Xml -Xml $xd -XPath '//sites/site')
        $o.Count = $ns.Count
        foreach ($n in $ns) {
            $el = $n.Node
            if ($el.GetAttribute('name') -ne $SITENAME) { continue }
            $a = $el.GetAttribute('serverAutoStart')
            if ([string]::IsNullOrEmpty($a)) { $o.Sas = 'ABSENT' } else { $o.Sas = $a }
        }
    } catch { $o.Sas = 'XML-ERR'; $o.Err = $_.Exception.GetType().Name }
    return $o
}

# RUNTIME READER 1 -- appcmd's direct property form. One line, no ambiguity. This replaces run
# 1's "/text:* then keep the last anchored match", which is the suspect code path.
function Get-AppcmdSas {
    $o = @{ Val = 'QUERY-FAILED'; Exit = -1; Lines = 0 }
    $d = Invoke-Native $APPCMD @('list', 'site', $SITENAME, '/text:serverAutoStart')
    $o.Exit = $d.ExitCode
    $arr = @($d.Out)
    $o.Lines = $arr.Count
    if ($arr.Count -ge 1) { $o.Val = ([string]$arr[0]).Trim() }
    return $o
}

# RUNTIME READER 2 -- ServerManager, a fresh instance each call so nothing is cached in-process.
function Get-SmSas {
    $o = @{ Val = 'NOT-TRIED'; State = 'NOT-TRIED'; Err = '' }
    try {
        Add-Type -Path $SMR -ErrorAction Stop
        $sm = New-Object Microsoft.Web.Administration.ServerManager
        $site = $null
        foreach ($s in $sm.Sites) { if ($s.Name -eq $SITENAME) { $site = $s; break } }
        if ($null -eq $site) { $o.Err = 'SITE-NOT-FOUND' }
        else {
            $o.Val = [string]$site.GetAttribute('serverAutoStart').Value
            $o.State = [string]$site.State
        }
        $sm.Dispose()
    } catch { $o.Err = $_.Exception.GetType().Name }
    return $o
}

# DIAGNOSTIC -- dump every /text:* line mentioning serverAutoStart WITH 3 lines of context on
# each side. Run 1 could not tell whether its second hit (line index 62) was a constant schema
# default or a live value; the surrounding lines name the enclosing object, which settles it.
function Show-TextStarSas([string]$label) {
    $full = Invoke-Native $APPCMD @('list', 'site', $SITENAME, '/text:*')
    $arr = @($full.Out)
    $total = $arr.Count
    Note ('  ' + $label + ' /text:* exitCode=' + $full.ExitCode + ' totalLines=' + $total)
    $k = 0
    $hits = 0
    foreach ($ln in $arr) {
        if ($ln -match 'serverAutoStart') {
            $hits++
            if ($hits -le $CapCtx) {
                $lo = $k - 3
                if ($lo -lt 0) { $lo = 0 }
                $hi = $k + 3
                if ($hi -gt ($total - 1)) { $hi = $total - 1 }
                Note ('    --- hit#' + $hits + ' lineIndex=' + $k + ' context[' + $lo + '..' + $hi + '] ---')
                for ($j = $lo; $j -le $hi; $j++) {
                    $mark = ' '
                    if ($j -eq $k) { $mark = '>' }
                    Note ('      ' + $mark + '[' + $j + '] ' + ([string]$arr[$j]).Trim())
                }
            }
        }
        $k++
    }
    Note ('  ' + $label + ' linesMentioningServerAutoStart=' + $hits + ' (context shown for first ' + $CapCtx + ')')
    # What run 1's assertion would have returned, for direct comparison.
    $oldVal = 'QUERY-FAILED'
    foreach ($ln in $arr) {
        if ($ln -match '^\s*serverAutoStart\s*:\s*"([^"]+)"\s*$') { $oldVal = $Matches[1] }
    }
    Note ('  ' + $label + ' RUN1-LAST-ANCHORED-MATCH-WOULD-RETURN=' + $oldVal)
}

$RUN_SITE_RE = 'SITE\s+"([^"]+)"\s+\(id:(\d+),bindings:(.*),state:([A-Za-z]+)\)'

# Facts only; all printing happens in the caller so Write-Output from inside cannot contaminate
# the returned object.
function Get-SiteFacts {
    $o = @{ Found = $false; Name = 'NONE'; Id = -1; Bindings = 'NONE'; State = 'NONE'
            AppcmdExit = -1; SiteLines = 0; Raw = @() }
    $r = Invoke-Native $APPCMD @('list', 'site')
    $o.AppcmdExit = $r.ExitCode
    $o.Raw = @($r.Out)
    foreach ($ln in $o.Raw) {
        if ($ln -match $RUN_SITE_RE) {
            $o.SiteLines++
            if ($Matches[1] -eq $SITENAME) {
                $o.Found = $true; $o.Name = $Matches[1]; $o.Id = [int]$Matches[2]
                $o.Bindings = $Matches[3]; $o.State = $Matches[4]
            }
        }
    }
    return $o
}

function Show-Site([string]$tag, $sf) {
    $i = 0
    foreach ($ln in $sf.Raw) {
        if ($i -ge $CapNative) { Note ('    ... appcmd list truncated at cap=' + $CapNative); break }
        Note ('  ' + $tag + ' appcmd| ' + $ln)
        $i++
    }
    Note ('  ' + $tag + ' parsed| found=' + $sf.Found + ' name="' + $sf.Name + '" id=' + $sf.Id + ' bindings=' + $sf.Bindings + ' state=' + $sf.State)
}

# sc.exe, not Get-Service: start type must be read from the SCM the same way on every call.
function Get-Svc([string]$name) {
    $o = @{ State = 'QUERY-FAILED'; Start = 'QUERY-FAILED' }
    $q = Invoke-Native $SC @('query', $name)
    foreach ($ln in $q.Out) {
        if ($ln -match 'STATE\s+:\s+\d+\s+(\S+)') { $o.State = $Matches[1] }
    }
    $c = Invoke-Native $SC @('qc', $name)
    foreach ($ln in $c.Out) {
        if ($ln -match 'START_TYPE\s+:\s+\d+\s+(\S+)') { $o.Start = $Matches[1] }
    }
    return $o
}

function Get-DistFacts {
    $o = @{}
    foreach ($f in $DISTFILES) {
        $o[$f] = Get-FileFacts ($DIST + '\' + $f)
    }
    return $o
}

function Assert-Dist([string]$label, $d) {
    foreach ($f in $DISTFILES) {
        $e = $EXPDIST[$f]
        $got = $d[$f]
        Assert (($got.Bytes -eq $e.Bytes) -and ($got.Sha16 -eq $e.Sha16)) ($label + ' dist ' + $f) ($got.Bytes.ToString() + '/' + $got.Sha16) ($e.Bytes.ToString() + '/' + $e.Sha16)
    }
}
Note '=== [1/8] PRE assertions (all measured, nothing guessed) ==='
$ah = Get-FileFacts $APPHOST
Assert ($ah.Exists) 'appHost.exists' ([string]$ah.Exists) 'True'
Assert ($ah.Bytes -eq $EXP.AppHostBytes) 'appHost.bytes' ([string]$ah.Bytes) ([string]$EXP.AppHostBytes)
Assert ($ah.Sha16 -eq $EXP.AppHostSha16) 'appHost.sha16' $ah.Sha16 $EXP.AppHostSha16
Note ('  appHost.mtime=' + $ah.Mtime)

$fsPre = Get-FileSas
$xlPre = Get-XmlSas
Assert ($fsPre.SiteElements -eq $EXP.SiteElements) 'file.siteElements' ([string]$fsPre.SiteElements) ([string]$EXP.SiteElements)
Assert ($xlPre.Count -eq $EXP.SiteElements) 'xml.siteCount' ([string]$xlPre.Count) ([string]$EXP.SiteElements)
Note ('  file.rawAttrs="' + $fsPre.RawAttrs + '"')
Assert ($fsPre.Sas -eq $EXP.FileSasBefore) 'FILE.serverAutoStart.before' $fsPre.Sas $EXP.FileSasBefore
Assert ($xlPre.Sas -eq $EXP.FileSasBefore) 'XML.serverAutoStart.before' $xlPre.Sas $EXP.FileSasBefore

$sitePre = Get-SiteFacts
Show-Site 'PRE' $sitePre
Assert ($sitePre.Found) 'site.found' ([string]$sitePre.Found) 'True'
Assert ($sitePre.Id -eq $EXP.SiteId) 'site.id' ([string]$sitePre.Id) ([string]$EXP.SiteId)
Assert ($sitePre.Bindings -eq $EXP.Bindings) 'site.bindings' $sitePre.Bindings $EXP.Bindings
Assert ($sitePre.State -eq $EXP.SiteState) 'site.state' $sitePre.State $EXP.SiteState

$acPre = Get-AppcmdSas
$smPre = Get-SmSas
Note ('  PRE runtime appcmdDirect=' + $acPre.Val + ' (exit=' + $acPre.Exit + ' lines=' + $acPre.Lines + ') serverManager=' + $smPre.Val + ' smState=' + $smPre.State + ' smErr=' + $smPre.Err)
Show-TextStarSas 'PRE'

$svcPre = @{}
foreach ($n in @('W3SVC', 'WAS', 'nginx8147')) { $svcPre[$n] = Get-Svc $n }
Note ('  PRE svc W3SVC=' + $svcPre['W3SVC'].State + '/' + $svcPre['W3SVC'].Start + ' WAS=' + $svcPre['WAS'].State + '/' + $svcPre['WAS'].Start + ' nginx8147=' + $svcPre['nginx8147'].State + '/' + $svcPre['nginx8147'].Start)
Assert ($svcPre['W3SVC'].State -eq $EXP.W3Svc) 'svc.W3SVC.state' $svcPre['W3SVC'].State $EXP.W3Svc
Assert ($svcPre['WAS'].State -eq $EXP.Was) 'svc.WAS.state' $svcPre['WAS'].State $EXP.Was
Assert ($svcPre['nginx8147'].State -eq $EXP.Nginx) 'svc.nginx8147.state' $svcPre['nginx8147'].State $EXP.Nginx

Show-Port 'PRE port80' (Get-PortListeners 80)
Show-Port 'PRE port443' (Get-PortListeners 443)
Assert ((Get-PortOwner 80) -eq $EXP.Port80Proc) 'port80.owner' (Get-PortOwner 80) $EXP.Port80Proc
Assert ((Get-PortOwner 443) -eq $EXP.Port443Proc) 'port443.owner' (Get-PortOwner 443) $EXP.Port443Proc

$distPre = Get-DistFacts
Assert-Dist 'PRE' $distPre

if (@($errs).Count -gt 0) {
    Note ('PRE-GATE RED with ' + @($errs).Count + ' error(s). NOTHING was backed up and NOTHING was changed.')
    foreach ($e in $errs) { Note ('  ERR ' + $e) }
    Note 'VERDICT-SAS2 ABORT-PRE'
    Note 'SAS2-FIX-DONE'
    exit 1
}
Note 'PRE-GATE PASS -- safe to proceed'

Note ''
Note '=== [2/8] backup (must be hash-verified BEFORE any change) ==='
if (-not (Test-Path -LiteralPath $BAKDIR)) { New-Item -ItemType Directory -Path $BAKDIR -Force | Out-Null }
Copy-Item -LiteralPath $APPHOST -Destination $BAKFILE -Force
$bk = Get-FileFacts $BAKFILE
Note ('  backup=' + $BAKFILE + ' bytes=' + $bk.Bytes + ' sha16=' + $bk.Sha16)
Assert ($bk.Bytes -eq $ah.Bytes) 'backup.bytes==source.bytes' ([string]$bk.Bytes) ([string]$ah.Bytes)
Assert ($bk.Sha16 -eq $ah.Sha16) 'backup.sha16==source.sha16' $bk.Sha16 $ah.Sha16
if (@($errs).Count -gt 0) {
    Note 'BACKUP-GATE RED -- no hash-verified restore point, so the attribute is NOT touched.'
    Note 'VERDICT-SAS2 ABORT-BACKUP'
    Note 'SAS2-FIX-DONE'
    exit 1
}
Note 'BACKUP VERIFIED -- a restore point exists'

Note ''
Note '=== [3/8] set serverAutoStart=false ==='
Note ('  command: appcmd ' + ($SETARGS -join ' '))
$setRes = Invoke-Native $APPCMD $SETARGS
Show-Native 'appcmd set site' $setRes $CapNative
Assert ($setRes.ExitCode -eq 0) 'appcmd.set.exitCode' ([string]$setRes.ExitCode) '0'

Note ''
Note '=== [4/8] POST file authority, re-read at 0/2/5/10 s (HARD) ==='
# The file is what W3SVC parses at boot, so it decides the reboot behaviour. Timed re-reads
# catch WAS writing the old in-memory value back over our change.
$shaSeen = @()
$sasSeen = @()
$cum = 0
$first = $true
foreach ($d in $DELAYS) {
    if (-not $first) { Start-Sleep -Seconds $d; $cum += $d }
    $first = $false
    $t = Get-Date -Format 'HH:mm:ss'
    $fs = Get-FileSas
    $xl = Get-XmlSas
    $shaSeen += $fs.Sha16
    $sasSeen += $fs.Sas
    Note ('  cumulative t+' + $cum + 's at ' + $t + ' bytes=' + $fs.Bytes + ' sha16=' + $fs.Sha16 + ' regexSas=' + $fs.Sas + ' xmlSas=' + $xl.Sas + ' rawAttrs="' + $fs.RawAttrs + '"')
    Assert ($fs.Sas -eq $EXP.FileSasAfter) ('FILE.serverAutoStart.after@t+' + $cum + 's') $fs.Sas $EXP.FileSasAfter
    Assert ($xl.Sas -eq $EXP.FileSasAfter) ('XML.serverAutoStart.after@t+' + $cum + 's') $xl.Sas $EXP.FileSasAfter
    Assert ($fs.SiteElements -eq $EXP.SiteElements) ('file.siteElements@t+' + $cum + 's') ([string]$fs.SiteElements) ([string]$EXP.SiteElements)
}
$shaUniq = @($shaSeen | Select-Object -Unique)
$sasUniq = @($sasSeen | Select-Object -Unique)
Note ('  sampledOverSeconds=' + $cum + ' fileSha16Distinct=' + ($shaUniq -join ',') + ' fileSasDistinct=' + ($sasUniq -join ','))
Assert ($shaUniq.Count -eq 1) ('file.sha16.stableOver' + $cum + 's') ($shaUniq -join ',') 'exactly one distinct value'
Assert ($sasUniq.Count -eq 1) ('file.sas.stableOver' + $cum + 's') ($sasUniq -join ',') 'exactly one distinct value'
$fsFinal = Get-FileSas
Note ''
Note '=== [5/8] POST runtime readers (SOFT diagnostics only) ==='
$acPost = Get-AppcmdSas
$smPost = Get-SmSas
Note ('  t+' + $cum + 's appcmdDirect=' + $acPost.Val + ' (exit=' + $acPost.Exit + ' lines=' + $acPost.Lines + ') serverManager=' + $smPost.Val + ' smState=' + $smPost.State + ' smErr=' + $smPost.Err)
AssertSoft ($acPost.Val -eq $EXP.FileSasAfter) 'runtime.appcmdDirect.after' $acPost.Val $EXP.FileSasAfter
AssertSoft ($smPost.Val -eq $EXP.FileSasAfter) 'runtime.serverManager.after' $smPost.Val $EXP.FileSasAfter
Show-TextStarSas 'POST'
if (@($warns).Count -gt 0) {
    Note ('  INTERPRETATION: file says ' + $fsFinal.Sas + ' and stayed there for 17 s; the runtime view disagrees.')
    Note '  A cached runtime view does not survive a reboot, applicationHost.config does. Keeping the change.'
}

Note ''
Note '=== [6/8] POST invariants (HARD) ==='
$sitePost = Get-SiteFacts
Show-Site 'POST' $sitePost
Assert ($sitePost.State -eq $EXP.SiteState) 'site.state.unchanged' $sitePost.State $EXP.SiteState
Assert ($sitePost.Bindings -eq $EXP.Bindings) 'site.bindings.unchanged' $sitePost.Bindings $EXP.Bindings
Assert ($sitePost.Id -eq $EXP.SiteId) 'site.id.unchanged' ([string]$sitePost.Id) ([string]$EXP.SiteId)
Assert ($sitePost.SiteLines -eq $EXP.SiteElements) 'runtime.siteCount' ([string]$sitePost.SiteLines) ([string]$EXP.SiteElements)

$svcPost = @{}
foreach ($n in @('W3SVC', 'WAS', 'nginx8147')) { $svcPost[$n] = Get-Svc $n }
Note ('  POST svc W3SVC=' + $svcPost['W3SVC'].State + '/' + $svcPost['W3SVC'].Start + ' WAS=' + $svcPost['WAS'].State + '/' + $svcPost['WAS'].Start + ' nginx8147=' + $svcPost['nginx8147'].State + '/' + $svcPost['nginx8147'].Start)
Assert ($svcPost['W3SVC'].State -eq $svcPre['W3SVC'].State) 'svc.W3SVC.state.unchanged' $svcPost['W3SVC'].State $svcPre['W3SVC'].State
Assert ($svcPost['W3SVC'].Start -eq $svcPre['W3SVC'].Start) 'svc.W3SVC.startType.unchanged' $svcPost['W3SVC'].Start $svcPre['W3SVC'].Start
Assert ($svcPost['WAS'].State -eq $svcPre['WAS'].State) 'svc.WAS.state.unchanged' $svcPost['WAS'].State $svcPre['WAS'].State
Assert ($svcPost['WAS'].Start -eq $svcPre['WAS'].Start) 'svc.WAS.startType.unchanged' $svcPost['WAS'].Start $svcPre['WAS'].Start
Assert ($svcPost['nginx8147'].State -eq $svcPre['nginx8147'].State) 'svc.nginx8147.state.unchanged' $svcPost['nginx8147'].State $svcPre['nginx8147'].State
Assert ($svcPost['nginx8147'].Start -eq $svcPre['nginx8147'].Start) 'svc.nginx8147.startType.unchanged' $svcPost['nginx8147'].Start $svcPre['nginx8147'].Start

Show-Port 'POST port80' (Get-PortListeners 80)
Show-Port 'POST port443' (Get-PortListeners 443)
# netstat is run ONCE per port and the result cached, so the assertion's got= string and its
# condition cannot possibly disagree. ([string](Get-PortCount 80)) inline would have run
# netstat twice per line and is also a suspicious cast-of-parenthesised-call form.
$o80 = Get-PortOwner 80
$o443 = Get-PortOwner 443
$c80 = Get-PortCount 80
$c443 = Get-PortCount 443
Note ('  parsed port80 owner=' + $o80 + ' count=' + $c80 + ' | port443 owner=' + $o443 + ' count=' + $c443)
Assert ($o80 -eq $EXP.Port80Proc) 'port80.owner.unchanged' $o80 $EXP.Port80Proc
Assert ($o443 -eq $EXP.Port443Proc) 'port443.owner.unchanged' $o443 $EXP.Port443Proc
Assert ($c80 -eq 1) 'port80.listenerCount' ([string]$c80) '1'
Assert ($c443 -eq 1) 'port443.listenerCount' ([string]$c443) '1'

$distPost = Get-DistFacts
Assert-Dist 'POST' $distPost

Note ''
Note '=== [7/8] rollback decision ==='
$errCount = 0
if ($null -ne $errs) { $errCount = @($errs).Count }
$rolledBack = 'NOT-NEEDED'
if ($errCount -gt 0) {
    Note ('  POST-GATE RED with ' + $errCount + ' HARD error(s). Rolling the attribute back to true.')
    foreach ($e in $errs) { Note ('    ERR ' + $e) }
    $rb = Invoke-Native $APPCMD $UNDOARGS
    Show-Native 'appcmd rollback set' $rb $CapNative
    $fsRb = Get-FileSas
    Note ('  ROLLBACK fileSas=' + $fsRb.Sas + ' sha16=' + $fsRb.Sha16)
    if ($fsRb.Sas -eq $EXP.FileSasBefore) { $rolledBack = 'OK' } else { $rolledBack = 'FAILED-MANUAL-RESTORE-FROM-BACKUP' }
    Note ('  rollback result=' + $rolledBack)
    Note ('  file-level restore point: ' + $BAKFILE)
} else {
    Note '  POST-GATE GREEN on every HARD assertion. The change is KEPT.'
    Note ('  warnCount(soft, non-blocking)=' + @($warns).Count)
    foreach ($w in $warns) { Note ('    WARN ' + $w) }
    Note ('  applicationHost.config now pins serverAutoStart="false" on "' + $SITENAME + '".')
    Note '  On the next host reboot W3SVC will no longer auto-start this site, so http.sys will'
    Note '  not take TCP/80 and nginx8147 keeps the port. Runtime site state is untouched (Stopped).'
}

Note ''
Note '=== [8/8] OFF LIMITS self-attestation ==='
$svcFin = @{}
foreach ($n in @('W3SVC', 'WAS', 'nginx8147')) { $svcFin[$n] = Get-Svc $n }
Note ('  service start types UNCHANGED: W3SVC ' + $svcPre['W3SVC'].Start + '->' + $svcFin['W3SVC'].Start + ' | WAS ' + $svcPre['WAS'].Start + '->' + $svcFin['WAS'].Start + ' | nginx8147 ' + $svcPre['nginx8147'].Start + '->' + $svcFin['nginx8147'].Start)
Note ('  W3SVC/WAS runtime state UNCHANGED: ' + $svcPre['W3SVC'].State + '->' + $svcFin['W3SVC'].State + ' / ' + $svcPre['WAS'].State + '->' + $svcFin['WAS'].State)
Note ('  site binding list UNCHANGED: ' + $sitePre.Bindings + ' -> ' + $sitePost.Bindings)
Note ('  site runtime state UNCHANGED: ' + $sitePre.State + ' -> ' + $sitePost.State + ' (this script never runs appcmd start site)')
Note ('  port 80 owner UNCHANGED: ' + (Get-PortOwner 80) + ' (expected nginx)')
$qry = Invoke-Native $SC @('qc', 'nginx8147')
$bin = 'NOT-FOUND'
foreach ($ln in $qry.Out) { if ($ln -match 'BINARY_PATH_NAME\s+:\s+(.*)$') { $bin = $Matches[1].Trim() } }
Note ('  nssm NOT invoked / C:\nginx\* and C:\www\dist NOT written')
Note ('  nginx8147 registration intact: ' + $bin)
$ahFin = Get-FileFacts $APPHOST
Note ('  appHost final bytes=' + $ahFin.Bytes + ' sha16=' + $ahFin.Sha16 + ' mtime=' + $ahFin.Mtime)
Note ('  backup kept at ' + $BAKFILE + ' bytes=' + $bk.Bytes + ' sha16=' + $bk.Sha16)

Note ''
Note '=== VERDICT-SUMMARY ==='
Note ('  hardErrors=' + $errCount + ' softWarns=' + @($warns).Count + ' rolledBack=' + $rolledBack)
Note ('  fileSasFinal=' + $fsFinal.Sas + ' xmlSasFinal=' + (Get-XmlSas).Sas + ' appcmdDirectFinal=' + (Get-AppcmdSas).Val)
Note ('  siteState=' + $sitePost.State + ' bindings=' + $sitePost.Bindings + ' nginx=' + $svcFin['nginx8147'].State + ' port80=' + (Get-PortOwner 80))
if ($errCount -eq 0) {
    Note ('VERDICT-SAS2 PASS hardErrors=0 softWarns=' + @($warns).Count + ' rolledBack=' + $rolledBack)
} else {
    Note ('VERDICT-SAS2 FAIL hardErrors=' + $errCount + ' rolledBack=' + $rolledBack)
}
Note 'SAS2-FIX-DONE'
if ($errCount -gt 0) { exit 1 }
exit 0
