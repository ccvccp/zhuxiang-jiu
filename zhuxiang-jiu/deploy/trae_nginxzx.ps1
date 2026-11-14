# trae_nginxzx.ps1 -- t27c: bring the nginx8147 service up on the zxjiu.com host
# 39.107.43.149 so it takes over TCP/80, now that t27b has stopped the IIS site that held it.
#
# AUTHORIZATION (user AskUserQuestion Q1, step 3, verbatim): "resume/start nginx8147".
# Q1 also states the chain ordering t27a -> t27b -> t27c -> t27d is not reorderable, and that
# each step is accepted independently with a stop-and-rollback on any red.
#
# WHY "RESUME" ALONE IS NOT ENOUGH, AND WHY THIS SCRIPT ESCALATES: nginx8147 is an nssm-wrapped
# service. The migration runbook in docs/ (the 20261022 failover document, whose filename is
# Chinese and therefore not spelled out here, "logs directory excluded from the package" entry)
# records that when nginx cannot start, nssm leaves the service in
# Paused and IT DOES NOT SELF-HEAL -- recovery required an explicit restart. So this script
# tries Resume-Service first (the literal authorized verb), measures, and only if the evidence
# shows nginx is still not serving does it escalate to Stop-Service + Start-Service. Both forms
# are the same granularity -- one named service -- so the escalation is not a scope widening.
# Set-Service is never used: the start type must stay Automatic.
#
# THE MOST IMPORTANT GATE IN THIS FILE: section [1/6] asserts that the three pages in
# C:\www\dist are NO LONGER the pre-t27a versions. If t27a had not landed, starting nginx would
# put the high-risk quickFill block (plaintext phone number + plaintext password) live on
# zxjiu.com -- which is exactly the hazard that made this chain order-sensitive. A negative
# control is used rather than a positive expectation, because the post-patch sha256 is measured
# here and printed for t27d to gate on; hardcoding a value this script cannot re-derive would
# turn a transcription slip into a false red on a healthy host.
#
# WHY NOTHING ABOUT nginx.conf IS HARDCODED: the conf actually in use on this host
# (C:\nginx\conf\nginx.conf) has no copy in the repository, so its listen port, server_name and
# root are UNKNOWN to this file. They are measured at runtime and printed. The two conf files
# that ARE in the repo (deploy/nginx-zxjiu.conf, deploy/ecs8147-nginx.conf) belong to the old
# Linux host and to the unrelated 8.147.66.237 machine respectively, and are not authoritative.
#
# SCOPE -- what this script may change, exhaustively:
#   * the runtime state of the Windows service named "nginx8147" (Paused -> Running)
#   * on the failure path only, the runtime state of the IIS site "Default Web Site"
#     (Stopped -> Started), to undo the darkness that t27b + a failed t27c would leave behind
# Everything below is explicitly OFF LIMITS, and section [6/6] self-attests each item:
#   * any service's start type (no Set-Service, no sc config)
#   * the W3SVC and WAS service bodies and their running states
#   * nssm itself -- no install/set/remove. Re-registering services is Plan B, which the user
#     deferred ("use Plan A for a while, then decide on Plan B"); it is forbidden here.
#   * C:\nginx\conf\nginx.conf and C:\nginx\cert.pem -- read and hash, never written
#   * C:\www\dist -- t27a's output, read and hash, never written
#   * the IIS site binding list, and the SSH tunnel / private key / ACL work (also Plan B)
#
# WHY netstat.exe IS THE PRIMARY PORT SOURCE: the read-only probe's section [5/9] threw a
# CimJobException while querying 443 and flipped a fully-correct run to INCONCLUSIVE. netstat is
# a plain executable and cannot raise a CIM job exception. Get-NetTCPConnection is kept as
# one corroborating line whose failure never flips a verdict. `sc.exe qc` is likewise captured
# as informational evidence only -- its labels are localized, so nothing gates on its parsing.
#
# BACKUP FIRST (security-hardcoded 2.4): nginx.conf is copied into C:\ProgramData\zxjiu-backup\
# and hash-verified BEFORE any state change, so a restore point exists even though this script
# never writes that file. fail-closed (2.3): no hash-verified backup => no state change.
#
# Target paths and the service name are a HARDCODED WHITELIST; nothing is parameterized, so
# there is no path-traversal surface (security-hardcoded 1.8).
# 100% ASCII, bounded output, and a closing sentinel self-attesting "not truncated".

$ErrorActionPreference = 'Stop'

$SVCM     = 'nginx8147'
$NGXDIR   = 'C:\nginx'
$NGXEXE   = 'C:\nginx\nginx.exe'
$NGXCONF  = 'C:\nginx\conf\nginx.conf'
$NGXLOGD  = 'C:\nginx\logs'
$NGXERR   = 'C:\nginx\logs\error.log'
$NGXACC   = 'C:\nginx\logs\access.log'
$DIST     = 'C:\www\dist'
$APPCMD   = 'C:\Windows\System32\inetsrv\appcmd.exe'
$SC       = 'C:\Windows\System32\sc.exe'
$NETSTAT  = 'C:\Windows\System32\netstat.exe'
$BAKDIR   = 'C:\ProgramData\zxjiu-backup'
$SITENAME = 'Default Web Site'
$STARTARGS = @('start', 'site', ('/site.name:' + $SITENAME))
$STAMP    = Get-Date -Format 'yyyyMMddHHmmss'

$DISTFILES = @('activity.html', 'activity-detail.html', 'xiaozhu-voice.html')

# Expectations measured read-only on this same host by trae_pre27zx.ps1 and by t27b.
$EXP = @{
    NginxSvcBefore  = 'Paused/Automatic'
    NginxStartType  = 'Automatic'
    W3SvcSvc        = 'Running/Automatic'
    WasSvc          = 'Running/Manual'
    SiteStateBefore = 'Stopped'
    Bindings        = 'http/*:80:'
    SiteId          = 1
    # The old IIS answer on 127.0.0.1 -- used as a NEGATIVE control in [5/6]. If this exact
    # body comes back after nginx is supposedly up, nginx is not really serving.
    OldIisBytes     = 703
    OldIisSha16     = '557B5E9015B4963A'
    OldIisServer    = 'Microsoft-IIS/10.0'
}

# The pre-t27a content of the three pages. [1/6] asserts each file is NOT this any more.
$PRE = @{
    'activity.html'        = @{ Bytes = 30354;  Sha16 = '08E594F46E8E5556' }
    'activity-detail.html' = @{ Bytes = 43030;  Sha16 = '0DA59F6ED07A7C2F' }
    'xiaozhu-voice.html'   = @{ Bytes = 226903; Sha16 = '53FFA0C414BF033B' }
}

# What t27a was supposed to leave behind. Printed for the operator and for t27d, NOT gated
# here: see the header note on negative vs positive controls.
$POST = @{
    'activity.html'        = @{ Bytes = 30361;  Sha16 = 'CFB0D6634C58F2DD' }
    'activity-detail.html' = @{ Bytes = 43037;  Sha16 = '3A114C112CE190D0' }
    'xiaozhu-voice.html'   = @{ Bytes = 226647; Sha16 = '8ABC34674A6AA67A' }
}

$CapNetstat = 26
$CapAppcmd  = 12
$CapLog     = 20
$CapConf    = 8
$CapSc      = 14

$NGXCERT = 'C:\nginx\cert.pem'

function Note([string]$s) { Write-Output $s }

$sha = [System.Security.Cryptography.SHA256]::Create()
function Get-Sha256Hex([byte[]]$b) { return (([BitConverter]::ToString($sha.ComputeHash($b))) -replace '-', '') }

function Get-FileFacts([string]$p) {
    $o = @{ Exists = $false; Bytes = -1; Sha16 = 'NOFILE'; Mtime = 'NOFILE' }
    if (-not (Test-Path -LiteralPath $p)) { return $o }
    try {
        $b = [IO.File]::ReadAllBytes($p)
        $o.Exists = $true
        $o.Bytes = $b.Length
        $o.Sha16 = (Get-Sha256Hex $b).Substring(0, 16)
        $o.Mtime = (Get-Item -LiteralPath $p).LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
    } catch { $o.Sha16 = 'READERR' }
    return $o
}

# Native-exe wrapper. Under $ErrorActionPreference='Stop' a 2>&1 merge turns stderr lines into
# error records and aborts the run, so EAP is relaxed for the call only and restored in a
# finally block. The parameter is $ArgList, never $Args -- $args is a PowerShell automatic
# variable and shadowing it once already produced a silent no-op.
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

# Takes an explicit cap: t27c drives two native exes with different output budgets
# (appcmd list site is short, sc.exe qc is longer), so a single script-level cap will not do.
function Show-Native([string]$tag, $res, [int]$cap) {
    Note ('  ' + $tag + ' exitCode=' + $res.ExitCode)
    $i = 0
    foreach ($ln in $res.Out) {
        if ($i -ge $cap) { Note ('    ... output truncated at cap=' + $cap); break }
        Note ('    | ' + $ln)
        $i++
    }
}

# Port listeners via netstat only. The ':<port>$' tail anchor keeps :80 from matching :8080.
function Get-PortListeners([int]$port) {
    $rows = @()
    try {
        $r = Invoke-Native $NETSTAT @('-ano')
        foreach ($ln in $r.Out) {
            if ($ln -notmatch 'LISTENING') { continue }
            $parts = @(($ln.Trim()) -split '\s+')
            if ($parts.Count -lt 5) { continue }
            if ($parts[1] -notmatch (' :' + $port + '$')) { continue }
            $pname = 'UNKNOWN'
            try { $pname = (Get-Process -Id ([int]$parts[4]) -ErrorAction Stop).ProcessName } catch { $pname = 'NOACCESS' }
            $rows += @{ Proto = $parts[0]; Local = $parts[1]; ProcId = $parts[4]; ProcName = $pname }
        }
    } catch {
        Note ('  netstat QUERY-ERROR for port ' + $port + ': ' + $_.Exception.GetType().Name + ' | ' + $_.Exception.Message)
    }
    # `return , $rows` emits the array as ONE pipeline item, which is what lets an empty result
    # survive the function boundary. Consequence: callers MUST assign it bare
    # ($p = Get-PortListeners 80). Wrapping the CALL in @(...) double-wraps it -- .Count becomes
    # a constant 1 and the lone element is the array itself, whose .ProcId/.ProcName are empty.
    # That silently made t27b's [5/6] port-80 gate unconditionally true. Same trap, same fix.
    return , $rows
}

function Show-Port([string]$label, $rows) {
    if (@($rows).Count -eq 0) { Note ('  ' + $label + ': NO-LISTENER'); return }
    $i = 0
    foreach ($x in $rows) {
        if ($i -ge $CapNetstat) { Note ('  ... truncated at CapNetstat=' + $CapNetstat); break }
        Note ('  ' + $label + ': ' + $x.Proto + ' ' + $x.Local + ' LISTENING pid=' + $x.ProcId + ' proc=' + $x.ProcName)
        $i++
    }
}

# True when some row on this port is served by the given process name. Used by [4/6] to prove
# the listener is nginx.exe and not pid 4 / System (http.sys, i.e. the IIS site t27b stopped).
function Test-PortHeldBy([string]$label, $rows, [string]$wantProc) {
    $hit = @($rows | Where-Object { $_.ProcName -eq $wantProc })
    return @{ Held = ($hit.Count -gt 0); Count = $hit.Count; Pids = (($hit | ForEach-Object { $_.ProcId }) -join ',') }
}

$SITE_RE = 'SITE\s+"([^"]+)"\s+\(id:(\d+),bindings:(.*),state:([A-Za-z]+)\)'

# Returns facts only; all printing happens in the caller so that Write-Output from inside this
# function cannot contaminate the returned object.
function Get-SiteFacts {
    $o = @{ Found = $false; Name = 'NONE'; Id = -1; Bindings = 'NONE'; State = 'NONE'
            AppcmdExit = -1; Raw = @(); WebState = 'QUERY-FAILED'; WebBindings = 'QUERY-FAILED' }
    $r = Invoke-Native $APPCMD @('list', 'site')
    $o.AppcmdExit = $r.ExitCode
    $o.Raw = @($r.Out)
    foreach ($ln in $o.Raw) {
        if ($ln -match $SITE_RE -and $Matches[1] -eq $SITENAME) {
            $o.Found = $true
            $o.Name = $Matches[1]
            $o.Id = [int]$Matches[2]
            $o.Bindings = $Matches[3]
            $o.State = $Matches[4]
        }
    }
    try {
        $w = Get-Website -Name $SITENAME -ErrorAction Stop
        $o.WebState = [string]$w.State
        $o.WebBindings = ((@($w.bindings.Collection) | ForEach-Object { $_.protocol + '/*:' + ($_.bindingInformation -replace '^:', '') }) -join ',')
    } catch {
        $o.WebError = $_.Exception.GetType().Name + ' | ' + $_.Exception.Message
    }
    return $o
}

function Show-Site([string]$tag, $sf) {
    $i = 0
    foreach ($ln in $sf.Raw) {
        if ($i -ge $CapAppcmd) { Note ('    ... appcmd list truncated at CapAppcmd=' + $CapAppcmd); break }
        Note ('  ' + $tag + ' appcmd| ' + $ln)
        $i++
    }
    Note ('  ' + $tag + ' parsed | found=' + $sf.Found + ' name="' + $sf.Name + '" id=' + $sf.Id + ' bindings=' + $sf.Bindings + ' state=' + $sf.State)
    Note ('  ' + $tag + ' Get-Website | state=' + $sf.WebState + ' bindings=' + $sf.WebBindings + $(if ($sf.WebError) { ' error=' + $sf.WebError } else { '' }))
}

# Generalized loopback fetch. No Host header: PowerShell 5.1 rejects Host in -Headers ("must be
# modified using the appropriate property"), and t24 already proved there is no host-based
# binding -- with and without Host:zxjiu.com the response was byte-identical.
function Get-Http([string]$url) {
    $o = @{ Url = $url; Outcome = 'NOT-TRIED'; Code = -1; Bytes = -1; Sha16 = 'NONE'; Server = 'NONE'; Detail = '' }
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
        $o.Outcome = 'RESPONDED'
        $o.Code = [int]$resp.StatusCode
        $b = $resp.RawContentStream.ToArray()
        $o.Bytes = $b.Length
        $o.Sha16 = (Get-Sha256Hex $b).Substring(0, 16)
        $o.Server = [string]$resp.Headers['Server']
    } catch {
        $o.Outcome = 'NO-RESPONSE'
        $o.Detail = $_.Exception.GetType().Name + ' | ' + $_.Exception.Message
        $wr = $null
        try { $wr = $_.Exception.Response } catch { $wr = $null }
        if ($wr) {
            $o.Outcome = 'HTTP-ERROR'
            $o.Code = [int]$wr.StatusCode
            $o.Server = [string]$wr.Headers['Server']
        }
    }
    return $o
}

# Deliberately does NOT print an https-skipped line: t27c treats http://127.0.0.1/,
# https://127.0.0.1/ and https://zxjiu.com/ differently, so that note belongs at each call site.
function Show-Http([string]$tag, $h) {
    Note ('  ' + $tag + ' ' + $h.Url + ' | outcome=' + $h.Outcome + ' code=' + $h.Code + ' bytes=' + $h.Bytes + ' sha16=' + $h.Sha16 + ' server=' + $h.Server)
    if ($h.Detail) { Note ('      detail=' + $h.Detail) }
}

function Get-SvcSnapshot {
    $o = @{}
    foreach ($sn in @('nginx8147', 'W3SVC', 'WAS')) {
        try {
            $s = Get-Service -Name $sn -ErrorAction Stop
            $o[$sn] = [string]$s.Status + '/' + [string]$s.StartType
        } catch { $o[$sn] = 'NOT-FOUND' }
    }
    $o['nginxProcCount'] = @(Get-Process -Name nginx -ErrorAction SilentlyContinue).Count
    return $o
}

function Show-Svc([string]$tag, $sv) {
    Note ('  ' + $tag + ' nginx8147=' + $sv['nginx8147'] + ' W3SVC=' + $sv['W3SVC'] + ' WAS=' + $sv['WAS'] + ' nginxProcCount=' + $sv['nginxProcCount'])
}

# --------------------------------------------------------------------------------------------
# INFORMATIONAL-ONLY EVIDENCE COLLECTORS. None of the three below may ever drive a verdict.
# --------------------------------------------------------------------------------------------

# `sc.exe qc` labels are localized on Chinese Windows (BINARY_PATH_NAME renders in Chinese), so
# NOTHING in this file parses them. The raw output is printed for the operator and as a record
# that the start type was observed. Gating on it would be gating on a translation.
function Get-ScQc {
    return (Invoke-Native $SC @('qc', $SVCM))
}

# Plain `return $out`, NOT `return , $out`. The `,` form is only needed where an empty result
# must survive the function boundary as a distinguishable object (Get-PortListeners); here the
# caller wraps the RESULT in @(...), which is the correct shape and cannot double-wrap.
function Get-LogTail([string]$p, [int]$n) {
    if (-not (Test-Path -LiteralPath $p)) { return @('NOFILE ' + $p) }
    try {
        return @(Get-Content -LiteralPath $p -Tail $n -ErrorAction Stop)
    } catch {
        return @('READERR ' + $_.Exception.GetType().Name + ' | ' + $_.Exception.Message)
    }
}

function Show-LogTail([string]$tag, $lines) {
    Note ('  ' + $tag + ' tailLines=' + @($lines).Count)
    $k = 0
    foreach ($ln in $lines) {
        if ($k -ge $CapLog) { Note ('    ... truncated at CapLog=' + $CapLog); break }
        Note ('    | ' + $ln)
        $k++
    }
}

# Read-only measurement of t27a's output. This is the evidence behind the most important gate
# in the file: [1/6] refuses to start nginx while any page is still the pre-t27a body.
function Get-DistFacts {
    $o = @{}
    foreach ($f in $DISTFILES) { $o[$f] = (Get-FileFacts (Join-Path $DIST $f)) }
    return $o
}

function Show-Dist([string]$tag, $facts) {
    foreach ($f in $DISTFILES) {
        $m = $facts[$f]
        Note ('  ' + $tag + ' ' + $f + ' bytes=' + $m.Bytes + ' sha16=' + $m.Sha16 + ' mtime=' + $m.Mtime)
        Note ('      preT27a-NEGATIVE-CONTROL bytes=' + $PRE[$f].Bytes + ' sha16=' + $PRE[$f].Sha16 +
              ' | postT27a-INFO-ONLY bytes=' + $POST[$f].Bytes + ' sha16=' + $POST[$f].Sha16)
    }
}

# nginx.conf has no repository copy, so listen / server_name / root are UNKNOWN to this file.
# They are extracted at runtime and printed. Nothing gates on them: a conf this script cannot
# re-derive must not become a source of false reds on a healthy host.
function Get-ConfFacts {
    $base = Get-FileFacts $NGXCONF
    $o = @{ Bytes = $base.Bytes; Sha16 = $base.Sha16; Mtime = $base.Mtime; Lines = @() }
    if (-not $base.Exists) { return $o }
    try {
        $all = @(Get-Content -LiteralPath $NGXCONF -ErrorAction Stop)
        $o.Lines = @($all | Where-Object { $_ -match '(?i)listen|server_name|root|ssl_certificate' } |
                     Select-Object -First $CapConf)
    } catch {
        $o.Lines = @('READERR ' + $_.Exception.GetType().Name)
    }
    return $o
}

function Show-Conf([string]$tag, $cf) {
    Note ('  ' + $tag + ' nginx.conf bytes=' + $cf.Bytes + ' sha16=' + $cf.Sha16 + ' mtime=' + $cf.Mtime +
          ' (informational; no repo copy exists so nothing is gated on its content)')
    foreach ($ln in $cf.Lines) { Note ('    | ' + $ln.Trim()) }
}

# --------------------------------------------------------------------------------------------
# THE READ-ONLY / MUTATING BOUNDARY. Everything above this comment only measures. Everything
# below changes state on the host. trae_verifywire_t27c.ps1 splits the file on the literal
# string 'function Do-Rollback' and dry-runs the upper half in isolation, so this function must
# remain the FIRST item after the read-only helpers and must never move below section [1/6].
# --------------------------------------------------------------------------------------------

$script:didChange = $false
$script:svcForm   = 'none'

# Reverting helper. Two steps, strictly ordered:
#   step 1 stops nginx8147 -- if it is Running but mis-serving it still owns TCP/80, and
#          starting the IIS site underneath it would fail on a binding conflict;
#   step 2 starts the IIS site, undoing the darkness that t27b plus a failed t27c would leave
#          behind (which would be: nothing at all reachable on zxjiu.com).
# Guarded by $script:didChange for exactly the reason t27b's rollback was guarded by $didStop:
# if this run never touched the service, issuing any verb is an UNAUTHORIZED change rather than
# a rollback. Suspend-Service is deliberately not used to "restore" Paused -- the user never
# authorized that, and Paused is the broken state this chain exists to leave, not a good one.
function Do-Rollback {
    if (-not $script:didChange) {
        Note '  ROLLBACK skipped: this run never changed the nginx8147 state, so there is nothing to revert and no service verb may be issued.'
        return
    }
    Note '  ROLLBACK step 1/2: stopping nginx8147 so it cannot hold TCP/80 while the IIS site is brought back.'
    try {
        Stop-Service -Name $SVCM -Force -ErrorAction Stop
        Note '    stop-service OK'
    } catch {
        Note ('    stop-service FAILED ' + $_.Exception.GetType().Name + ' | ' + $_.Exception.Message)
    }
    for ($w = 1; $w -le 8; $w++) {
        Start-Sleep -Seconds 2
        $rbPort = Get-PortListeners 80
        if (@($rbPort).Count -eq 0) { Note ('    port 80 released after ' + ($w * 2) + 's'); break }
        if ($w -eq 8) { Note '    port 80 STILL HELD after 16s -- step 2 may fail on a binding conflict.' }
    }
    Note '  ROLLBACK step 2/2: starting the IIS site.'
    $rbStart = Invoke-Native $APPCMD $STARTARGS
    Show-Native 'ROLLBACK-STARTSITE' $rbStart $CapAppcmd
    $rbSite = Get-SiteFacts
    Show-Site 'ROLLBACK-READBACK' $rbSite
    $rbSvc = Get-SvcSnapshot
    Show-Svc 'ROLLBACK' $rbSvc
    $rbPort2 = Get-PortListeners 80
    Show-Port 'ROLLBACK port 80' $rbPort2
}

# ============================================================================================
# [1/6] BEFORE -- read-only measurement, then the three red exits that guard every later step.
# Nothing in this section changes anything, so none of its red exits may call Do-Rollback:
# issuing a service verb when this run has changed nothing would itself be an unauthorized act.
# ============================================================================================
Note ''
Note '=== [1/6] BEFORE ==='
Note ('  host=' + $env:COMPUTERNAME + ' stamp=' + $STAMP + ' targetService=' + $SVCM)

# --- Red exit 1 of 3 ---------------------------------------------------------------------------
# fail-closed (security-hardcoded 2.3): a precondition this script cannot measure is a red, not a
# pass. cert.pem and the logs directory are in the list because the runbook entry quoted in the
# header says a MISSING logs directory is exactly what put nginx8147 into Paused in the first
# place; refusing to act when it is absent again is the whole point of the check.
$needPaths = @(
    @{ N = 'netstat';    P = $NETSTAT },
    @{ N = 'appcmd';     P = $APPCMD },
    @{ N = 'sc';         P = $SC },
    @{ N = 'nginx.exe';  P = $NGXEXE },
    @{ N = 'nginx.conf'; P = $NGXCONF },
    @{ N = 'logsDir';    P = $NGXLOGD },
    @{ N = 'cert.pem';   P = $NGXCERT },
    @{ N = 'dist';       P = $DIST }
)
$missTool = @()
foreach ($tool in $needPaths) {
    $present = Test-Path -LiteralPath $tool.P
    Note ('  tool ' + $tool.N + ' present=' + $present + ' path=' + $tool.P)
    if (-not $present) { $missTool += $tool.N }
}
if ($missTool.Count -gt 0) {
    Note ''
    Note ('VERDICT-BEFORE FAIL: required path(s) missing: ' + ($missTool -join ','))
    Note 'fail-closed (security-hardcoded 2.3): an unmeasurable precondition is a red, not a pass.'
    Note 'Nothing was changed. No backup was created. No service verb was issued.'
    Note '=== NGINXZX-DONE ==='
    exit 1
}

Note ''
$distBefore = Get-DistFacts
Show-Dist 'BEFORE dist' $distBefore

# --- Red exit 2 of 3 -- THE MOST IMPORTANT GATE IN THIS FILE (see the header) ------------------
# Negative control: the three pages must NO LONGER equal their pre-t27a content. If any still
# does, t27a never landed here, and bringing nginx up would put the high-risk quickFill block
# (plaintext phone number + plaintext password) live on zxjiu.com. The post-t27a values printed
# by Show-Dist are INFO-ONLY on purpose: hardcoding a sha256 this script cannot re-derive would
# turn a transcription slip into a false red on an otherwise healthy host.
$stillPre = @()
foreach ($df in $DISTFILES) {
    if (($distBefore[$df].Bytes -eq $PRE[$df].Bytes) -and ($distBefore[$df].Sha16 -eq $PRE[$df].Sha16)) {
        $stillPre += $df
    }
}
Note ('  BEFORE negative-control stillPreT27aCount=' + $stillPre.Count + ' files=' + ($stillPre -join ','))
if ($stillPre.Count -gt 0) {
    Note ''
    Note ('VERDICT-BEFORE FAIL: page(s) still hold their PRE-t27a content: ' + ($stillPre -join ','))
    Note 'Refusing to bring nginx up: that would publish the high-risk quickFill block (plaintext phone + plaintext password) on zxjiu.com.'
    Note 'The authorized chain is t27a -> t27b -> t27c; this means t27a did not land on this host. Re-run t27a first.'
    Note 'Nothing was changed. No backup was created. No service verb was issued.'
    Note '=== NGINXZX-DONE ==='
    exit 1
}

Note ''
$cfBefore = Get-ConfFacts
Show-Conf 'BEFORE' $cfBefore

Note ''
$qcBefore = Get-ScQc
Show-Native 'BEFORE sc qc (informational, labels are localized so nothing gates on it)' $qcBefore $CapSc

Note ''
$errBefore = Get-LogTail $NGXERR $CapLog
Show-LogTail 'BEFORE nginx error.log' $errBefore
$accBefore = Get-LogTail $NGXACC 5
Show-LogTail 'BEFORE nginx access.log' $accBefore

Note ''
$sfBefore = Get-SiteFacts
Show-Site 'BEFORE' $sfBefore
$beforeBindings = [string]$sfBefore.Bindings
$beforeId = $sfBefore.Id

Note ''
$p80Before = Get-PortListeners 80
Show-Port 'BEFORE port 80' $p80Before
$p443Before = Get-PortListeners 443
Show-Port 'BEFORE port 443' $p443Before
# Corroborating line only. Its failure is swallowed and NEVER flips a verdict -- that is the exact
# lesson from the read-only probe, where a CimJobException on 443 produced INCONCLUSIVE even
# though netstat had already answered the question correctly.
try {
    $cim80 = @(Get-NetTCPConnection -State Listen -LocalPort 80 -ErrorAction Stop)
    Note ('  BEFORE port 80 corroborating Get-NetTCPConnection rows=' + $cim80.Count + ' pids=' + (($cim80 | ForEach-Object { $_.OwningProcess } | Select-Object -Unique) -join ','))
} catch {
    Note ('  BEFORE port 80 corroborating Get-NetTCPConnection UNAVAILABLE (' + $_.Exception.GetType().Name + ') -- informational only, netstat above is the authority.')
}

Note ''
# Both loopback URLs are measured for the before/after comparison. https://127.0.0.1/ is expected
# to FAIL here and after: the certificate is issued to the domain, so the name does not match, and
# this script will never disable TLS validation (security-hardcoded 1.9) to force a green. It is
# printed as evidence, never gated. The public https re-test belongs to t27d, which runs on the
# local PC where the hostname and the certificate agree.
$h80Before = Get-Http 'http://127.0.0.1/'
Show-Http 'BEFORE' $h80Before
$h443Before = Get-Http 'https://127.0.0.1/'
Show-Http 'BEFORE-INFORMATIONAL' $h443Before

Note ''
$svcBefore = Get-SvcSnapshot
Show-Svc 'BEFORE' $svcBefore

# --- Red exit 3 of 3 ---------------------------------------------------------------------------
# Drift gate. If the live situation is not the situation the user was shown when authorizing this
# step, this script has no mandate to act. Port 80 being free is part of it: that is precisely what
# t27b was authorized to establish, and nginx historically failed with WSAEACCES (10013) when
# http.sys still owned the port -- the error.log tail above is that history.
$p80Free = (@($p80Before).Count -eq 0)
$siteOk = $sfBefore.Found -and ($sfBefore.AppcmdExit -eq 0) -and
          ($beforeBindings -eq $EXP.Bindings) -and ($sfBefore.State -eq $EXP.SiteStateBefore)
$svcOk = ($svcBefore['nginx8147'] -eq $EXP.NginxSvcBefore) -and
         ($svcBefore['W3SVC'] -eq $EXP.W3SvcSvc) -and ($svcBefore['WAS'] -eq $EXP.WasSvc)
Note ('  BEFORE gates | siteFound=' + $sfBefore.Found + ' appcmdExit=' + $sfBefore.AppcmdExit +
      ' siteState=' + $sfBefore.State + '/' + $EXP.SiteStateBefore +
      ' bindingsMatch=' + ($beforeBindings -eq $EXP.Bindings) + ' idMatch=' + ($beforeId -eq $EXP.SiteId) +
      ' port80Free=' + $p80Free)
Note ('  BEFORE gates | nginx8147=' + $svcBefore['nginx8147'] + '/' + $EXP.NginxSvcBefore +
      ' W3SVC=' + $svcBefore['W3SVC'] + '/' + $EXP.W3SvcSvc +
      ' WAS=' + $svcBefore['WAS'] + '/' + $EXP.WasSvc +
      ' => siteOk=' + $siteOk + ' svcOk=' + $svcOk)
if (-not ($siteOk -and $svcOk -and $p80Free)) {
    Note ''
    Note 'VERDICT-BEFORE FAIL: the live host does not match the authorized baseline.'
    Note ('  detail siteOk=' + $siteOk + ' svcOk=' + $svcOk + ' port80Free=' + $p80Free +
          ' bindings=' + $beforeBindings + ' expected=' + $EXP.Bindings)
    Note 'Refusing to act on a situation the user did not authorize. Nothing was changed. No backup was created. No service verb was issued.'
    Note '=== NGINXZX-DONE ==='
    exit 1
}
$beforeOk = $true
Note ('  BEFORE verdictOk=' + $beforeOk + ' -- baseline matches, proceeding to the backup step.')

Note 'VERDICT-BEFORE PASS'

Note ''
Note '=== [2/6] BACKUP (must precede any state change; security-hardcoded 2.4) ==='
# nginx.conf is never written by this script, but a hash-verified restore point is made first
# anyway, because the authorized verb is a service START and a start makes nginx RE-READ that
# conf. If the read produces something unexpected the operator needs a known-good copy to diff
# against. cert.pem is deliberately NOT backed up: this script cannot change it, and copying a
# private key around for no reason is a risk of its own.
if (-not (Test-Path -LiteralPath $BAKDIR)) {
    New-Item -ItemType Directory -Path $BAKDIR -Force | Out-Null
    Note ('  created backup dir ' + $BAKDIR)
} else {
    Note ('  backup dir already present ' + $BAKDIR)
}
$bak = Join-Path $BAKDIR ('nginx.conf.bak-nginxzx-' + $STAMP)
Copy-Item -LiteralPath $NGXCONF -Destination $bak -Force
$srcBytes = [IO.File]::ReadAllBytes($NGXCONF)
$bakBytes = [IO.File]::ReadAllBytes($bak)
$hashEqual = ((Get-Sha256Hex $srcBytes) -eq (Get-Sha256Hex $bakBytes))
Note ('  backupPath=' + $bak)
Note ('  sourceBytes=' + $srcBytes.Length + ' backupBytes=' + $bakBytes.Length + ' hashEqual=' + $hashEqual)
Note ('  backupSha256=' + (Get-Sha256Hex $bakBytes))
if (-not $hashEqual) {
    Note ''
    Note 'VERDICT-BACKUP FAIL: the backup does not hash-equal its source, so a rollback could not be trusted.'
    Note 'No state change was attempted. nginx8147 is untouched and still Paused.'
    Note '=== NGINXZX-DONE ==='
    exit 1
}
Note 'VERDICT-BACKUP PASS: hash-verified backup in place, a rollback is now possible.'

Note ''
Note '=== [3/6] STATE CHANGE (the single authorized action: nginx8147 Paused -> Running) ==='
# didChange is set BEFORE the verb, never after it succeeds. If the verb throws halfway, or SCM
# accepts it and nginx then dies, the host has still been moved out of its BEFORE shape and
# Do-Rollback must be allowed to act. Optimistic bookkeeping here would strand the IIS site
# Stopped AND nginx down -- exactly the darkness the rollback exists to prevent.
$script:didChange = $true
try {
    Resume-Service -Name $SVCM -ErrorAction Stop
    $script:svcForm = 'resume'
    Note '  Resume-Service issued (the literal authorized verb); it returned without throwing.'
} catch {
    Note ('  Resume-Service FAILED ' + $_.Exception.GetType().Name + ' | ' + $_.Exception.Message)
}
# Poll rather than read back once: nssm reports the SCM state before nginx has bound its ports,
# so an immediate readback would race. 8 tries x 2s is the budget t27b used for http.sys.
$runOk = $false
$tries = 0
$svcRun = Get-SvcSnapshot
for ($i = 0; $i -lt 8; $i++) {
    $tries = $i + 1
    Start-Sleep -Seconds 2
    $svcRun = Get-SvcSnapshot
    if ($svcRun['nginx8147'] -like 'Running/*') { $runOk = $true; break }
}
Show-Svc ('AFTER-RESUME tries=' + $tries) $svcRun
if (-not $runOk) {
    # Escalation, same granularity (one named service), and the reason is in the header: the
    # runbook records that nssm leaves a failed nginx in Paused and IT DOES NOT SELF-HEAL, so a
    # restart was what recovered it. Set-Service is never called -- the start type stays Automatic.
    # nssm.exe is deliberately NOT invoked: whether it even exists on this host was never measured
    # here (that fact belongs to the other, unrelated machine), and Stop+Start is equivalent for an
    # nssm-wrapped service. Invoking an unmeasured binary path would be a fail-open guess.
    Note '  Resume-Service did not reach Running; escalating to Stop-Service + Start-Service on the same named service.'
    try {
        Stop-Service -Name $SVCM -Force -ErrorAction Stop
        Note '    stop-service OK'
    } catch {
        Note ('    stop-service FAILED ' + $_.Exception.GetType().Name + ' | ' + $_.Exception.Message)
    }
    Start-Sleep -Seconds 2
    try {
        Start-Service -Name $SVCM -ErrorAction Stop
        $script:svcForm = 'stop+start'
        Note '    start-service OK'
    } catch {
        Note ('    start-service FAILED ' + $_.Exception.GetType().Name + ' | ' + $_.Exception.Message)
    }
    $runOk = $false
    $tries = 0
    for ($i = 0; $i -lt 15; $i++) {
        $tries = $i + 1
        Start-Sleep -Seconds 4
        $svcRun = Get-SvcSnapshot
        if ($svcRun['nginx8147'] -like 'Running/*') { $runOk = $true; break }
    }
    Show-Svc ('AFTER-START tries=' + $tries) $svcRun
}
Note ('  [3/6] result | serviceForm=' + $script:svcForm + ' serviceReachedRunning=' + $runOk)
Note '  A non-Running result is deliberately NOT decided here. [4/6] gates on live evidence, so a service that reports Running while holding no port cannot pass, and one that reports Paused while nginx is in fact serving is reported rather than rolled back blindly.'

# ============================================================================================
# [4/6] LIVE VERIFICATION -- the SCM's opinion is NOT evidence. t27b's whole lesson was that a
# service can report a state its own ports contradict, so this section decides on netstat rows
# and the nginx process count, and only afterwards looks at what Get-Service says.
# ============================================================================================
Note ''
Note '=== [4/6] LIVE VERIFICATION (ports + processes) ==='

# Bare assignment, never @(Get-PortListeners ...). See the trap documented inside the function:
# wrapping the CALL double-wraps the `return , $rows` value and .Count becomes a constant 1.
$p80After = Get-PortListeners 80
Show-Port 'AFTER port 80' $p80After
$p443After = Get-PortListeners 443
Show-Port 'AFTER port 443' $p443After
# Corroborating line only. Its failure is swallowed and NEVER flips this verdict -- the exact
# lesson from the read-only probe, where a CimJobException on 443 produced INCONCLUSIVE even
# though netstat had already answered the question correctly.
try {
    $cimAfter = @(Get-NetTCPConnection -State Listen -LocalPort 80 -ErrorAction Stop)
    Note ('  AFTER port 80 corroborating Get-NetTCPConnection rows=' + $cimAfter.Count + ' pids=' + (($cimAfter | ForEach-Object { $_.OwningProcess } | Select-Object -Unique) -join ','))
} catch {
    Note ('  AFTER port 80 corroborating Get-NetTCPConnection UNAVAILABLE (' + $_.Exception.GetType().Name + ') -- informational only, netstat above is the authority.')
}

Note ''
$svcAfter = Get-SvcSnapshot
Show-Svc 'AFTER' $svcAfter
$held80  = Test-PortHeldBy 'AFTER port 80'  $p80After  'nginx'
$held443 = Test-PortHeldBy 'AFTER port 443' $p443After 'nginx'
$sysHeld = @($p80After | Where-Object { $_.ProcName -eq 'System' }).Count
$procOk   = ([int]$svcAfter['nginxProcCount'] -ge 1)
$svcRunOk = ($svcAfter['nginx8147'] -like 'Running/*')
$serveOk  = $held80.Held -and $held443.Held -and ($sysHeld -eq 0) -and $procOk
Note ('  AFTER gates | nginxOn80=' + $held80.Held + ' (rows=' + $held80.Count + ' pids=' + $held80.Pids + ')' +
      ' nginxOn443=' + $held443.Held + ' (rows=' + $held443.Count + ' pids=' + $held443.Pids + ')' +
      ' systemStillOn80=' + $sysHeld + ' nginxProcCount=' + $svcAfter['nginxProcCount'] + ' => serveOk=' + $serveOk)
Note ('  AFTER gates | scmState=' + $svcAfter['nginx8147'] + ' scmSaysRunning=' + $svcRunOk +
      ' serviceForm=' + $script:svcForm + ' -- the SCM state is reported, not gated here; serveOk above is the gate.')

# The error.log tail is printed on BOTH paths and BEFORE the verdict, because the historically
# likely failure is a repeat of the bind() WSAEACCES (10013) loop t24 found in this very log.
Note ''
$errAfter = Get-LogTail $NGXERR $CapLog
Show-LogTail 'AFTER nginx error.log' $errAfter
$accAfter = Get-LogTail $NGXACC 5
Show-LogTail 'AFTER nginx access.log' $accAfter
$qcAfter = Get-ScQc
Show-Native 'AFTER sc qc (informational, labels are localized so nothing gates on it)' $qcAfter $CapSc

if (-not $serveOk) {
    Note ''
    Note 'VERDICT-LIVE FAIL: nginx8147 is not serving. The single authorized goal was not reached.'
    Note ('  detail nginxOn80=' + $held80.Held + ' nginxOn443=' + $held443.Held + ' systemStillOn80=' + $sysHeld +
          ' nginxProcCount=' + $svcAfter['nginxProcCount'] + ' scmState=' + $svcAfter['nginx8147'] +
          ' serviceForm=' + $script:svcForm)
    Note '  The error.log tail above is the diagnosis. Rolling back so that zxjiu.com is not left dark by t27b plus a failed t27c.'
    Do-Rollback
    Note '=== NGINXZX-DONE ==='
    exit 1
}
Note 'VERDICT-LIVE PASS: nginx owns TCP/80 and TCP/443, and http.sys (pid 4 / System) no longer holds 80.'

$anomaly = ''
if (-not $svcRunOk) {
    # Contradictory but real: the ports prove nginx is serving while the SCM does not say Running.
    # Deliberately NOT rolled back -- stopping a working site to satisfy a bookkeeping field would
    # trade a cosmetic anomaly for a real outage. It is carried into [6/6] and fails the summary,
    # so the host stays serving and a human decides what the discrepancy means.
    $anomaly = 'SERVICE-STATE-ANOMALY'
    Note ('  ANOMALY: nginx is serving but Get-Service reports "' + $svcAfter['nginx8147'] + '". Not rolling back; failing the summary instead.')
}

# ============================================================================================
# [5/6] HTTP EVIDENCE -- what a client actually receives on loopback, plus the negative control
# proving it is no longer the old IIS welcome page.
# ============================================================================================
Note ''
Note '=== [5/6] HTTP EVIDENCE (loopback) ==='
# http://127.0.0.1/ is the gated one. It is the same request t24 and t27b measured, so its BEFORE
# body (703 bytes / sha16 557B5E9015B4963A / Microsoft-IIS/10.0) is a known negative control:
# if that exact body comes back now, nginx is holding the port but not serving the site.
$h80After = Get-Http 'http://127.0.0.1/'
Show-Http 'AFTER' $h80After
# https://127.0.0.1/ is INFORMATIONAL and never gated, before or after: cert.pem is issued to the
# domain so the name cannot match 127.0.0.1, and this script will not disable TLS validation
# (security-hardcoded 1.9) to force a green. The public https re-test is t27d, on the local PC
# where the hostname and the certificate agree.
$h443After = Get-Http 'https://127.0.0.1/'
Show-Http 'AFTER-INFORMATIONAL' $h443After

$answered      = ($h80After.Outcome -ne 'NO-RESPONSE')
$stillOldIis   = ($h80After.Sha16 -eq $EXP.OldIisSha16)
$serverIsNginx = ($h80After.Server -like 'nginx*')
$httpOk = $answered -and (-not $stillOldIis)
Note ('  AFTER gates | answered=' + $answered + ' outcome=' + $h80After.Outcome + ' code=' + $h80After.Code +
      ' bytes=' + $h80After.Bytes + ' stillOldIisBody=' + $stillOldIis +
      ' (negative control bytes=' + $EXP.OldIisBytes + ' sha16=' + $EXP.OldIisSha16 + ') => httpOk=' + $httpOk)
Note ('  AFTER corroboration | server="' + $h80After.Server + '" serverLooksLikeNginx=' + $serverIsNginx +
      ' -- printed, NOT gated: nginx.conf has no repository copy, so whether it sets server_tokens off is unknown here, and gating on a header this script cannot re-derive would produce false reds on a healthy host.')
if (-not $httpOk) {
    Note ''
    Note 'VERDICT-HTTP FAIL: loopback is either silent or still returning the pre-t27b IIS body.'
    Note ('  detail outcome=' + $h80After.Outcome + ' code=' + $h80After.Code + ' bytes=' + $h80After.Bytes +
          ' sha16=' + $h80After.Sha16 + ' server=' + $h80After.Server + ' | ' + $h80After.Detail)
    Note '  Rolling back so that zxjiu.com is not left dark.'
    Do-Rollback
    Note '=== NGINXZX-DONE ==='
    exit 1
}
Note 'VERDICT-HTTP PASS: loopback answered and the body is no longer the old IIS welcome page.'

# ============================================================================================
# [6/6] SUMMARY -- no-scope-creep self-attestation, then the single overall verdict.
# Every item below is a read-back of something this script promised in its header NOT to touch.
# ============================================================================================
Note ''
Note '=== [6/6] SUMMARY (self-attestation of the off-limits list) ==='

$cfAfter   = Get-ConfFacts
Show-Conf 'AFTER' $cfAfter
$certAfter = Get-FileFacts $NGXCERT
Note ('  AFTER cert.pem exists=' + $certAfter.Exists + ' bytes=' + $certAfter.Bytes + ' sha16=' + $certAfter.Sha16 + ' mtime=' + $certAfter.Mtime + ' (informational: no BEFORE byte snapshot exists for this file, only the red-exit-1 presence check)')

$sfAfter      = Get-SiteFacts
Show-Site 'AFTER' $sfAfter
$distAfter    = Get-DistFacts
Show-Dist 'AFTER' $distAfter

$cStartType    = ($svcAfter['nginx8147'] -like '*/' + $EXP.NginxStartType) -and ($svcAfter['W3SVC'] -eq $EXP.W3SvcSvc) -and ($svcAfter['WAS'] -eq $EXP.WasSvc)
$cConf         = ($cfAfter.Sha16 -eq $cfBefore.Sha16) -and ($cfAfter.Bytes -eq $cfBefore.Bytes)
$cSiteBindings = ($sfAfter.Bindings -eq $beforeBindings)
$cSiteId       = ($sfAfter.Id -eq $beforeId)
$cSiteState    = ($sfAfter.State -eq $EXP.SiteStateBefore)
$cDist         = $true
$distMatchesPost = $true
$distDrift     = @()
foreach ($df in $DISTFILES) {
    if (($distAfter[$df].Bytes -ne $distBefore[$df].Bytes) -or ($distAfter[$df].Sha16 -ne $distBefore[$df].Sha16)) {
        $cDist = $false
        $distDrift += $df
    }
    if ($distAfter[$df].Sha16 -ne $POST[$df].Sha16) { $distMatchesPost = $false }
}
$creepOk = $cStartType -and $cConf -and $cSiteBindings -and $cSiteId -and $cSiteState -and $cDist

Note ('  CREEP startTypesUnchanged=' + $cStartType + ' (nginx8147=' + $svcAfter['nginx8147'] + ' W3SVC=' + $svcAfter['W3SVC'] + ' expected=' + $EXP.W3SvcSvc + ' WAS=' + $svcAfter['WAS'] + ' expected=' + $EXP.WasSvc + ')')
Note ('  CREEP nginxConfUnchanged=' + $cConf + ' (sha16 ' + $cfBefore.Sha16 + ' -> ' + $cfAfter.Sha16 + ', bytes ' + $cfBefore.Bytes + ' -> ' + $cfAfter.Bytes + ')')
Note ('  CREEP iisSiteUntouched=' + ($cSiteBindings -and $cSiteId -and $cSiteState) + ' bindingsUnchanged=' + $cSiteBindings + ' idUnchanged=' + $cSiteId + ' stillStopped=' + $cSiteState + ' (bindings="' + $sfAfter.Bindings + '" id=' + $sfAfter.Id + ' state=' + $sfAfter.State + ')')
Note ('  CREEP distUnchangedByThisRun=' + $cDist + ' driftFiles=' + ($distDrift -join ',') + ' | distStillMatchesT27aSourceOfTruth=' + $distMatchesPost + ' (informational positive check for t27d)')
Note ('  CREEP overall=' + $creepOk + ' | nssmUntouched=no nssm verb exists in this file | tunnelKeysAclUntouched=no such path appears in this file')

if (-not $creepOk) {
    # Deliberately NOT rolled back. Do-Rollback cannot restore a drifted conf, a changed start
    # type, a re-bound site or a modified page -- it only issues service verbs -- and nginx is
    # serving correctly right now. Stopping a working site to protest a change this script did
    # not make would add an outage to an anomaly. Fail the summary and hand it to a human.
    Note ''
    Note 'VERDICT-SUMMARY FAIL: something outside the authorized scope changed. nginx is serving, so no rollback was issued -- a rollback cannot undo any of the items above and would only take the site down.'
    Note ('  detail startTypes=' + $cStartType + ' conf=' + $cConf + ' bindings=' + $cSiteBindings + ' siteId=' + $cSiteId + ' siteState=' + $cSiteState + ' dist=' + $cDist)
    Note '  Investigate by hand. Backups are in C:\ProgramData\zxjiu-backup (nginx.conf, hash-verified in [2/6]).'
    Note '=== NGINXZX-DONE ==='
    exit 1
}

$summaryOk = $serveOk -and $httpOk -and $creepOk -and ($anomaly -eq '')
Note ''
Note ('  SUMMARY | serveOk=' + $serveOk + ' httpOk=' + $httpOk + ' creepOk=' + $creepOk + ' anomaly="' + $anomaly + '" => summaryOk=' + $summaryOk)
Note ('  SUMMARY | serviceForm=' + $script:svcForm + ' nginx8147=' + $svcAfter['nginx8147'] + ' nginxProcCount=' + $svcAfter['nginxProcCount'] +
      ' iisSite=' + $sfAfter.State + ' loopback80=' + $h80After.Code + '/' + $h80After.Bytes + 'B/' + $h80After.Sha16 + ' server=' + $h80After.Server)
if (-not $summaryOk) {
    Note ''
    Note 'VERDICT-SUMMARY FAIL: the host is serving but at least one assertion is red; see the ANOMALY line above. No rollback was issued because the site is up and a rollback would only darken it.'
    Note '=== NGINXZX-DONE ==='
    exit 1
}
Note ''
Note 'VERDICT-SUMMARY PASS: nginx8147 is serving TCP/80 and TCP/443, loopback no longer returns the IIS body, and nothing outside the authorized scope changed.'
Note 'NEXT: t27d -- public re-test of https://zxjiu.com/ from the local PC (three pages 200 + sha256 equal to the t27a source of truth + leakScan 0 + positive control).'
Note '=== NGINXZX-DONE ==='
exit 0
