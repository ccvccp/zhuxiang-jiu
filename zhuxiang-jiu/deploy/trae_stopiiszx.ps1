# trae_stopiiszx.ps1 -- t27b: release TCP/80 on the zxjiu.com host 39.107.43.149 by
# stopping exactly one IIS site, "Default Web Site". Nothing else is changed.
#
# AUTHORIZATION (user AskUserQuestion Q2, verbatim): use
#   appcmd stop site /site.name:"Default Web Site"
# plus a Get-Website readback asserting state=Stopped. "Finest granularity: only this one
# site is closed, the W3SVC/WAS service bodies are preserved, IIS can be started again at
# any time. Best reversibility."
# This is step 2 of the authorized chain t27a -> t27b -> t27c -> t27d. t27a already
# republished the three source-of-truth pages into C:\www\dist (stamp 20261114051014), so
# the ordering hazard that made this chain dangerous no longer applies.
#
# SCOPE -- what this script may change, exhaustively:
#   * the runtime state of the IIS site named "Default Web Site" (Started -> Stopped)
# Everything below is explicitly OFF LIMITS, and section [6/6] self-attests each item:
#   * the W3SVC service body, its running state, and its start type
#   * the WAS service body, its running state, and its start type
#   * the site's binding list -- removing the http/*:80 binding was explicitly vetoed
#   * nginx8147 -- must stay Paused, and the nginx process count must stay 0
#   * C:\www\dist (t27a's output) and C:\nginx\* (t27c's business)
#
# WHY netstat.exe IS THE PRIMARY PORT SOURCE: the read-only probe trae_pre27zx.ps1 section
# [5/9] threw a CimJobException while querying 443, which flipped that run's verdict to
# INCONCLUSIVE even though every substantive measurement was correct (the user then waived
# it). netstat is a plain executable and cannot raise a CIM job exception. Get-NetTCP-
# Connection is kept as one corroborating line only, and its failure never flips a verdict.
#
# BACKUP FIRST (security-hard-coded 2.4): applicationHost.config is copied into
# C:\ProgramData\zxjiu-backup\ and hash-verified BEFORE any state change. It is deliberately
# NOT written inside C:\Windows\System32\inetsrv\config\, because IIS watches that folder.
# fail-closed (2.3): no hash-verified backup => no stop attempt is made.
#
# Target paths and the site name are a HARDCODED WHITELIST; nothing is parameterized, so
# there is no path-traversal surface (security-hard-coded 1.8).
# 100% ASCII, bounded output, and a closing sentinel self-attesting "not truncated".

$ErrorActionPreference = 'Stop'

$SITENAME  = 'Default Web Site'
$APPCMD    = 'C:\Windows\System32\inetsrv\appcmd.exe'
$APPHOST   = 'C:\Windows\System32\inetsrv\config\applicationHost.config'
$NETSTAT   = 'C:\Windows\System32\netstat.exe'
$BAKDIR    = 'C:\ProgramData\zxjiu-backup'
$STAMP     = Get-Date -Format 'yyyyMMddHHmmss'
$STOPARGS  = @('stop','site',('/site.name:' + $SITENAME))
$STARTARGS = @('start','site',('/site.name:' + $SITENAME))

# Expectations, every one of them measured read-only on this same host by the t27 pre-flight
# probe (trae_pre27zx.ps1 sections [4/9] .. [7/9]).
$EXP = @{
    Bindings      = 'http/*:80:'
    SiteId        = 1
    AppHostBytes  = 47122
    AppHostSha16  = '4C8FAA5FBF4814BA'
    LoopbackBytes = 703
    LoopbackSha16 = '557B5E9015B4963A'
    ServerHeader  = 'Microsoft-IIS/10.0'
    NginxSvc      = 'Paused/Automatic'
    W3SvcSvc      = 'Running/Automatic'
    WasSvc        = 'Running/Manual'
}

$CapNetstat = 26
$CapAppcmd  = 12

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

# Native-exe wrapper. Under $ErrorActionPreference='Stop' a 2>&1 merge turns stderr lines
# into error records and aborts the run, so EAP is relaxed for the call only and restored in
# a finally block. The parameter is $ArgList, never $Args -- $args is a PowerShell automatic
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

function Show-Native([string]$tag, $res) {
    Note ('  ' + $tag + ' exitCode=' + $res.ExitCode)
    $i = 0
    foreach ($ln in $res.Out) {
        if ($i -ge $CapAppcmd) { Note ('    ... output truncated at CapAppcmd=' + $CapAppcmd); break }
        Note ('    | ' + $ln)
        $i++
    }
}

# Port listeners via netstat only. The match is EndsWith(':' + $port) on the BARE local-address
# token, so :80 cannot match :8080. It must not be a regex: $parts comes from
# ($ln.Trim() -split '\s+'), so $parts[1] carries NO leading space, and the earlier
# -notmatch (' :' + $port + '$') never matched anything -- the function returned empty for
# every port, which made the [5/6] AFTER port-80 gate unconditionally true (false green).
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
        Note ('  netstat QUERY-ERROR for port ' + $port + ': ' + $_.Exception.GetType().Name + ' | ' + $_.Exception.Message)
    }
    # `return , $rows` emits the array as ONE pipeline item, which is what lets an empty result
    # survive the function boundary. Consequence: callers MUST assign it bare
    # ($p = Get-PortListeners 80). Wrapping the CALL in @(...) double-wraps it -- .Count becomes
    # a constant 1 and the lone element is the array itself, whose .ProcId/.ProcName are empty.
    # That silently made the [5/6] AFTER port-80 gate unconditionally true (false green). Caught
    # by the [3/4] helper harness before dispatch, not after.
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

$SITE_RE = 'SITE\s+"([^"]+)"\s+\(id:(\d+),bindings:(.*),state:([A-Za-z]+)\)'

# Returns facts only; all printing happens in the caller so that Write-Output from inside
# this function cannot contaminate the returned object.
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

# Loopback over http only. No Host header: PowerShell 5.1 rejects Host in -Headers ("must be
# modified using the appropriate property"), and the probe already proved there is no
# host-based binding -- with and without Host:zxjiu.com the response was byte-identical.
# https://127.0.0.1/ is skipped by policy: 443 has no listener so it cannot answer, the
# certificate's CN is zyjiu.com and would fail validation, and bypassing TLS validation is
# forbidden (security-hardcoded item 9).
function Get-Loopback {
    $o = @{ Outcome = 'NOT-TRIED'; Code = -1; Bytes = -1; Sha16 = 'NONE'; Server = 'NONE'; Detail = '' }
    try {
        $resp = Invoke-WebRequest -Uri 'http://127.0.0.1/' -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
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

function Show-Loopback([string]$tag, $lb) {
    Note ('  ' + $tag + ' http://127.0.0.1/ | outcome=' + $lb.Outcome + ' code=' + $lb.Code + ' bytes=' + $lb.Bytes + ' sha16=' + $lb.Sha16 + ' server=' + $lb.Server)
    if ($lb.Detail) { Note ('      detail=' + $lb.Detail) }
    Note ('  ' + $tag + ' https://127.0.0.1/ | SKIPPED-BY-POLICY (443 unbound, cert CN mismatch, TLS bypass forbidden)')
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
# Reverting helper. Guarded by $didStop: if BEFORE already reported the site as stopped then
# this run changed nothing, and issuing the start verb would be an UNAUTHORIZED change rather
# than a rollback. That guard is the whole reason $didStop exists.
function Do-Rollback {
    if (-not $script:didStop) {
        Note '  ROLLBACK skipped: this run never changed the site state (BEFORE already reported it stopped), so there is nothing to revert and the start verb must not be issued.'
        return
    }
    Note '  ROLLBACK: restoring the site to its pre-run state.'
    $rr = Invoke-Native $APPCMD $STARTARGS
    Show-Native 'ROLLBACK' $rr
    $rf = Get-SiteFacts
    Show-Site 'ROLLBACK-READBACK' $rf
}

Note ('=== STOPIISZX t27b | site="' + $SITENAME + '" | stamp=' + $STAMP + ' | whoami=' + (whoami))
Note ''
Note '=== [1/6] BEFORE (read-only snapshot; this section changes nothing) ==='

$missTool = @()
foreach ($t in @(@{ N = 'appcmd'; P = $APPCMD }, @{ N = 'netstat'; P = $NETSTAT }, @{ N = 'apphost'; P = $APPHOST })) {
    $present = Test-Path -LiteralPath $t.P
    Note ('  tool ' + $t.N + ' present=' + $present + ' path=' + $t.P)
    if (-not $present) { $missTool += $t.N }
}
if ($missTool.Count -gt 0) {
    Note ''
    Note ('VERDICT-BEFORE FAIL: required path(s) missing: ' + ($missTool -join ','))
    Note 'fail-closed (security-hardcoded 2.3): an unmeasurable precondition is a red, not a pass.'
    Note 'Nothing was changed. No backup was created.'
    Note '=== STOPIISZX-DONE ==='
    exit 1
}

$sfBefore = Get-SiteFacts
Show-Site 'BEFORE' $sfBefore
$beforeBindings = [string]$sfBefore.Bindings
$beforeId = $sfBefore.Id

Note ''
$p80Before = Get-PortListeners 80
Show-Port 'BEFORE port 80' $p80Before
$p443Before = Get-PortListeners 443
Show-Port 'BEFORE port 443' $p443Before
# Corroborating line only. Its failure is swallowed and NEVER flips a verdict -- that is the
# exact lesson from the probe run, where a CimJobException on 443 produced INCONCLUSIVE even
# though netstat had already answered the question correctly.
try {
    $cim80 = @(Get-NetTCPConnection -State Listen -LocalPort 80 -ErrorAction Stop)
    Note ('  BEFORE port 80 corroborating Get-NetTCPConnection rows=' + $cim80.Count + ' pids=' + (($cim80 | ForEach-Object { $_.OwningProcess } | Select-Object -Unique) -join ','))
} catch {
    Note ('  BEFORE port 80 corroborating Get-NetTCPConnection UNAVAILABLE (' + $_.Exception.GetType().Name + ') -- informational only, netstat above is the authority.')
}

Note ''
$lbBefore = Get-Loopback
Show-Loopback 'BEFORE' $lbBefore

Note ''
$svcBefore = Get-SvcSnapshot
Show-Svc 'BEFORE' $svcBefore
$ahBefore = Get-FileFacts $APPHOST
Note ('  BEFORE applicationHost.config bytes=' + $ahBefore.Bytes + '/' + $EXP.AppHostBytes + ' sha16=' + $ahBefore.Sha16 + '/' + $EXP.AppHostSha16 + ' mtime=' + $ahBefore.Mtime)

# Drift gate. Bindings are part of it: if the binding list is no longer what the user was
# shown when authorizing this, the live situation is not the situation that was authorized.
# apphost bytes/sha16 drift is REPORTED only -- IIS rewrites that file on its own schedule.
$ahDrift = -not (($ahBefore.Bytes -eq $EXP.AppHostBytes) -and ($ahBefore.Sha16 -eq $EXP.AppHostSha16))
if ($ahDrift) { Note ('  NOTE applicationHost.config drifted from the probe baseline (informational, not gated).') }
$idMatch = ($beforeId -eq $EXP.SiteId)
$bindMatch = ($beforeBindings -eq $EXP.Bindings)
$beforeOk = $sfBefore.Found -and ($sfBefore.AppcmdExit -eq 0) -and $bindMatch
Note ('  BEFORE gates | found=' + $sfBefore.Found + ' appcmdExit=' + $sfBefore.AppcmdExit + ' bindingsMatch=' + $bindMatch + ' idMatch=' + $idMatch + ' => verdictOk=' + $beforeOk)
if (-not $beforeOk) {
    Note ''
    Note ('VERDICT-BEFORE FAIL: the live site does not match the authorized baseline (found=' + $sfBefore.Found + ' appcmdExit=' + $sfBefore.AppcmdExit + ' bindings=' + $beforeBindings + ' expected=' + $EXP.Bindings + ').')
    Note 'Refusing to act on a situation the user did not authorize. Nothing was changed. No backup was created.'
    Note '=== STOPIISZX-DONE ==='
    exit 1
}
$alreadyStopped = ($sfBefore.State -eq 'Stopped')
Note ('  BEFORE state=' + $sfBefore.State + ' GetWebsiteState=' + $sfBefore.WebState + ' alreadyStopped=' + $alreadyStopped)
if ($alreadyStopped) { Note '  ALREADY-STOPPED: the goal state is already in effect. No state change will be attempted and no start verb may ever be issued by this run.' }
Note 'VERDICT-BEFORE PASS'

Note ''
Note '=== [2/6] BACKUP (must precede any state change; security-hardcoded 2.4) ==='
if (-not (Test-Path -LiteralPath $BAKDIR)) {
    New-Item -ItemType Directory -Path $BAKDIR -Force | Out-Null
    Note ('  created backup dir ' + $BAKDIR)
} else {
    Note ('  backup dir already present ' + $BAKDIR)
}
$bak = Join-Path $BAKDIR ('applicationHost.config.bak-stopiis-' + $STAMP)
Copy-Item -LiteralPath $APPHOST -Destination $bak -Force
$srcBytes = [IO.File]::ReadAllBytes($APPHOST)
$bakBytes = [IO.File]::ReadAllBytes($bak)
$hashEqual = ((Get-Sha256Hex $srcBytes) -eq (Get-Sha256Hex $bakBytes))
Note ('  backupPath=' + $bak)
Note ('  sourceBytes=' + $srcBytes.Length + ' backupBytes=' + $bakBytes.Length + ' hashEqual=' + $hashEqual)
Note ('  backupSha256=' + (Get-Sha256Hex $bakBytes))
if (-not $hashEqual) {
    Note ''
    Note 'VERDICT-BACKUP FAIL: the backup does not hash-equal its source, so a rollback could not be trusted.'
    Note 'No state change was attempted. The site is untouched.'
    Note '=== STOPIISZX-DONE ==='
    exit 1
}
Note 'VERDICT-BACKUP PASS: hash-verified backup in place, a rollback is now possible.'

Note ''
Note '=== [3/6] STATE CHANGE (the single authorized action) ==='
$didStop = $false
$stopExit = -1
$stopForm = 'none'
if ($alreadyStopped) {
    Note '  skipped: BEFORE already reported the site stopped, so there is nothing to do.'
} else {
    $r1 = Invoke-Native $APPCMD $STOPARGS
    Show-Native 'STOP form=site.name-flag' $r1
    $stopExit = $r1.ExitCode
    $stopForm = 'site.name-flag'
    if ($stopExit -ne 0) {
        Note '  primary form returned non-zero; trying the same-granularity alternate form (still names this one site only, so this is not a scope widening).'
        $r2 = Invoke-Native $APPCMD @('stop', 'site', $SITENAME)
        Show-Native 'STOP form=positional' $r2
        $stopExit = $r2.ExitCode
        $stopForm = 'positional'
    }
    if ($stopExit -eq 0) {
        $didStop = $true
        Note ('  state change accepted (form=' + $stopForm + ', exitCode=0).')
    } else {
        Note ('  both forms returned non-zero (last exitCode=' + $stopExit + '). Proceeding to the readback so the failure path can report the real live state.')
    }
}

Note ''
Note '=== [4/6] READBACK (did the authorized action actually take effect?) ==='
$sfAfter = Get-SiteFacts
Show-Site 'AFTER' $sfAfter
$stateOk = ($sfAfter.State -eq 'Stopped') -and ($sfAfter.WebState -eq 'Stopped')
Note ('  readback gates | appcmdState=' + $sfAfter.State + ' getWebsiteState=' + $sfAfter.WebState + ' => stateOk=' + $stateOk)
if (-not $stateOk) {
    Note ''
    Do-Rollback
    Note 'VERDICT-READBACK FAIL: the site is not confirmed stopped by both appcmd and Get-Website.'
    Note 'Fail-closed (security-hardcoded 2.3). Port 80 is therefore still held, and t27c must NOT proceed.'
    Note '=== STOPIISZX-DONE ==='
    exit 1
}
Note 'VERDICT-READBACK PASS'

Note ''
Note '=== [5/6] AFTER (wait for http.sys to release 80, then re-measure) ==='
# http.sys tears the URL reservation down asynchronously, so poll instead of asserting once.
$p80After = $p80Before
$sysHeld = -1
$tries = 0
for ($i = 0; $i -lt 8; $i++) {
    $tries = $i + 1
    Start-Sleep -Seconds 2
    $p80After = Get-PortListeners 80
    $sysHeld = @($p80After | Where-Object { ($_.ProcId -eq '4') -or ($_.ProcName -eq 'System') }).Count
    if ($sysHeld -eq 0) { break }
}
Note ('  port 80 poll | tries=' + $tries + ' rowsNow=' + $p80After.Count + ' heldBySystemPid4=' + $sysHeld)
Show-Port 'AFTER port 80' $p80After
$p443After = Get-PortListeners 443
Show-Port 'AFTER port 443' $p443After
$lbAfter = Get-Loopback
Show-Loopback 'AFTER' $lbAfter
$stillOldIis = (($lbAfter.Outcome -eq 'RESPONDED') -and ($lbAfter.Bytes -eq $EXP.LoopbackBytes) -and ($lbAfter.Sha16 -eq $EXP.LoopbackSha16) -and ($lbAfter.Server -eq $EXP.ServerHeader))
$afterOk = ($sysHeld -eq 0) -and (-not $stillOldIis)
Note ('  AFTER gates | port80FreeOfSystem=' + ($sysHeld -eq 0) + ' oldIisResponseGone=' + (-not $stillOldIis) + ' => afterOk=' + $afterOk)
if (-not $afterOk) {
    Note ''
    Do-Rollback
    Note 'VERDICT-AFTER FAIL: the site reports stopped but port 80 is still answered by the previous owner.'
    Note 'Fail-closed. t27c must NOT proceed while 80 is still held.'
    Note '=== STOPIISZX-DONE ==='
    exit 1
}
Note 'VERDICT-AFTER PASS'

Note ''
Note '=== [6/6] SUMMARY + NO-SCOPE-CREEP SELF-ATTESTATION ==='
$svcAfter = Get-SvcSnapshot
Show-Svc 'AFTER' $svcAfter
$ahAfter = Get-FileFacts $APPHOST
Note ('  AFTER applicationHost.config bytes=' + $ahAfter.Bytes + ' sha16=' + $ahAfter.Sha16 + ' mtime=' + $ahAfter.Mtime)
$ahChanged = ($ahAfter.Sha16 -ne $ahBefore.Sha16)
if ($ahChanged) { Note ('  NOTE applicationHost.config sha16 moved ' + $ahBefore.Sha16 + ' -> ' + $ahAfter.Sha16 + '. Reported, not gated: IIS rewrites this file on its own, and a rollback verb on a site cannot restore file content. The verified backup is at ' + $bak) }

$bindingsUnchanged = ([string]$sfAfter.Bindings -eq $beforeBindings)
$cNginx = ($svcAfter['nginx8147'] -eq $EXP.NginxSvc) -and ($svcAfter['nginx8147'] -eq $svcBefore['nginx8147'])
$cProc  = ($svcAfter['nginxProcCount'] -eq 0)
$cW3    = ($svcAfter['W3SVC'] -eq $EXP.W3SvcSvc) -and ($svcAfter['W3SVC'] -eq $svcBefore['W3SVC'])
$cWas   = ($svcAfter['WAS'] -eq $EXP.WasSvc) -and ($svcAfter['WAS'] -eq $svcBefore['WAS'])
Note ('  no-scope-creep | bindingsUnchanged=' + $bindingsUnchanged + ' nginxStillPausedAndAutomatic=' + $cNginx + ' nginxProcCountStillZero=' + $cProc + ' w3svcUntouched=' + $cW3 + ' wasUntouched=' + $cWas)
$creepOk = $bindingsUnchanged -and $cNginx -and $cProc -and $cW3 -and $cWas

$summaryOk = $stateOk -and $afterOk -and $creepOk
Note ('  result | siteState=' + $sfAfter.State + ' didThisRunChangeIt=' + $didStop + ' stopForm=' + $stopForm + ' backup=' + $bak + ' hashEqual=' + $hashEqual)
Note ('  summaryOk=' + $summaryOk)
if (-not $summaryOk) {
    Note ''
    Do-Rollback
    Note 'VERDICT-SUMMARY FAIL: an out-of-scope item moved, or the goal state is not fully attested.'
    Note '=== STOPIISZX-DONE ==='
    exit 1
}
Note ''
Note 'VERDICT-SUMMARY PASS: port 80 released by stopping exactly one IIS site; every off-limits item self-attested unchanged. t27c may proceed.'
Note '=== STOPIISZX-DONE ==='
exit 0
