# Read-only post-deploy verification for the voice-health-agent restart-criteria fix.
# Sentinel: VHVERIFY8147-DONE
#
# WHAT WAS DEPLOYED (InvokeId t-wl072u0292se77k, ExitCode 0, 2026-11-12 06:47:47 ECS local):
#   C:\backend\scripts\voice_health_agent.py
#     old sha256 DC50E99F3E6997700D2AA3FFF920EB77F99BC22DBE928551E0CEDEAF6E52A107 (10327 B)
#     new sha256 77276416877A719FC6FB4E93BC46AC1F54AB0151A928FF0F5523726E13362136 (12037 B)
#   backup on box: C:\backend\scripts\voice_health_agent.py.bak-20261112064747 (verified == old sha)
#
# THE FIX, IN ONE LINE: probe_tts now returns a 4th value `upstream` (True when the TTS
# failure is an upstream dependency fault: HTTP 502/503/504, or a body matching
# "code":1113 / insufficient / arrear). main() computes the restart gate from
# restart_core_ok = ok_svc and (tts_ok or tts_upstream) and ws_ok, so an upstream billing
# fault no longer counts toward the 3-strike restart of backend8147. Reported `status`
# still comes from core_ok, so alerting is unchanged. Rationale: on 2026-11-11 an upstream
# 503 (code 1113, 75-byte body) drove 9 nssm restarts of backend8147 in one day -- restarting
# the backend cannot fix an upstream balance problem.
#
# THIS SCRIPT PERFORMS NO WRITE. READ-ONLY-ASSERT: it opens files for read, queries CIM /
# Get-ScheduledTask / Get-WinEvent, and writes only to stdout. READ-ONLY-CAVEAT: the cloud
# assistant wrapper itself materialises a copy of this script under $env:TEMP before running
# it, so "no write" is true of the script body, not of the transport.
#
# OUTPUT BUDGET (learned from probe #1, which blew the ~24 KB Output cap and lost its tail):
# every collection is capped, and the 7036 query is narrowed by provider + event id +
# a 3-hour window before any formatting happens.
$ErrorActionPreference = 'Stop'
$Sentinel = 'VHVERIFY8147-DONE'
$Root     = 'C:\backend'
$Target   = 'C:\backend\scripts\voice_health_agent.py'
$NewSha   = '77276416877A719FC6FB4E93BC46AC1F54AB0151A928FF0F5523726E13362136'
$OldSha   = 'DC50E99F3E6997700D2AA3FFF920EB77F99BC22DBE928551E0CEDEAF6E52A107'
$Svc      = 'backend8147'
$CapTail  = 14
$CapLine  = 200
$CapHit   = 8

function ShaOf([byte[]]$b) {
    $s = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($s.ComputeHash($b))).Replace('-', '') }
    finally { $s.Dispose() }
}
function Esc([string]$s) {
    if ($null -eq $s) { return '' }
    $sb = New-Object Text.StringBuilder
    foreach ($ch in $s.ToCharArray()) {
        $code = [int]$ch
        if ($code -gt 126 -or ($code -lt 32 -and $code -ne 10)) {
            [void]$sb.Append('\u' + $code.ToString('x4'))
        } elseif ($code -eq 10) {
            [void]$sb.Append('\n')
        } else { [void]$sb.Append($ch) }
    }
    return $sb.ToString()
}
function Clip([string]$s, [int]$n) {
    if ($null -eq $s) { return '' }
    if ($s.Length -le $n) { return $s }
    return $s.Substring(0, $n) + '...'
}

Write-Output ('localNow=' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Output ('host=' + $env:COMPUTERNAME)

# ---- [1/6] the deployed file is still the new revision, and the backup is still there
Write-Output '[1/6] file-state'
try {
    $b = [IO.File]::ReadAllBytes($Target)
    $sha = ShaOf $b
    Write-Output ('  targetBytes=' + $b.Length + ' sha=' + $sha)
    Write-Output ('  matchesNew=' + ($sha -eq $NewSha) + ' matchesOld=' + ($sha -eq $OldSha))
    $baks = @(Get-ChildItem -LiteralPath (Split-Path $Target) -Filter 'voice_health_agent.py.bak-*' -ErrorAction SilentlyContinue)
    Write-Output ('  backupCount=' + $baks.Count)
    foreach ($bk in $baks) {
        Write-Output ('    ' + $bk.Name + ' bytes=' + $bk.Length + ' sha=' + (ShaOf ([IO.File]::ReadAllBytes($bk.FullName))))
    }
} catch { Write-Output ('  UNKNOWN file-state: ' + (Esc $_.Exception.Message)) }

# ---- [2/6] did the NEW code actually run? checks.tts.upstream only exists in the new revision
Write-Output '[2/6] report'
try {
    $rp = Join-Path $Root 'logs\voice-health.json'
    $rtxt = [IO.File]::ReadAllText($rp)
    Write-Output ('  reportBytes=' + $rtxt.Length + ' hasUpstreamKey=' + $rtxt.Contains('"upstream"'))
    Write-Output ('  report=' + (Esc (Clip $rtxt 1600)))
} catch { Write-Output ('  UNKNOWN report: ' + (Esc $_.Exception.Message)) }
try {
    $sp = Join-Path $Root 'logs\voice-health-state.json'
    $stxt = [IO.File]::ReadAllText($sp)
    Write-Output ('  state=' + (Esc (Clip $stxt 700)))
} catch { Write-Output ('  UNKNOWN state: ' + (Esc $_.Exception.Message)) }

# ---- [3/6] flow log tail: single streaming pass, never slurp the whole file
Write-Output '[3/6] log-tail'
try {
    $lp = Join-Path $Root 'logs\voice-health.log'
    $tail = New-Object 'System.Collections.Generic.Queue[string]'
    $sr = New-Object IO.StreamReader($lp)
    try {
        while ($null -ne ($ln = $sr.ReadLine())) {
            $tail.Enqueue($ln)
            if ($tail.Count -gt $CapTail) { [void]$tail.Dequeue() }
        }
    } finally { $sr.Close() }
    foreach ($ln in $tail.ToArray()) { Write-Output ('  | ' + (Esc (Clip $ln $CapLine))) }
} catch { Write-Output ('  UNKNOWN log-tail: ' + (Esc $_.Exception.Message)) }

# ---- [4/6] scheduled task: is the 10-minute loop still armed and succeeding?
Write-Output '[4/6] task'
try {
    $t = Get-ScheduledTask -TaskName 'voice-health-agent' -ErrorAction Stop
    $i = Get-ScheduledTaskInfo -TaskName 'voice-health-agent' -ErrorAction Stop
    Write-Output ('  taskState=' + $t.State + ' lastRun=' + $i.LastRunTime + ' lastResult=' + $i.LastTaskResult)
    Write-Output ('  nextRun=' + $i.NextRunTime + ' missed=' + $i.NumberOfMissedRuns)
} catch { Write-Output ('  UNKNOWN task: ' + (Esc $_.Exception.Message)) }

# ---- [5/6] backend8147: still the same process we saw pre-deploy (pid 1924), i.e. no restart
Write-Output '[5/6] service'
try {
    $s = Get-CimInstance Win32_Service -Filter "Name='$Svc'" -ErrorAction Stop
    Write-Output ('  ' + $Svc + '=' + $s.State + ' startMode=' + $s.StartMode + ' pid=' + $s.ProcessId)
    $pr = Get-CimInstance Win32_Process -Filter "ProcessId=$($s.ProcessId)" -ErrorAction Stop
    Write-Output ('  procStart=' + $pr.CreationDate + ' name=' + $pr.Name)
} catch { Write-Output ('  UNKNOWN service: ' + (Esc $_.Exception.Message)) }

# ---- [6/6] SCM 7036 for backend8147 in the last 3 hours: the restart-storm signature
Write-Output '[6/6] scm-7036'
try {
    $cut = (Get-Date).AddHours(-3)
    $ev = @(Get-WinEvent -FilterHashtable @{ LogName='System'; Id=7036; StartTime=$cut } -ErrorAction Stop |
        Where-Object { $_.Message -match [regex]::Escape($Svc) })
    Write-Output ('  windowStart=' + $cut.ToString('yyyy-MM-dd HH:mm:ss') + ' hits=' + $ev.Count + ' cap=' + $CapHit)
    $n = 0
    foreach ($e in $ev) {
        if ($n -ge $CapHit) { Write-Output '  ...truncated-by-cap'; break }
        Write-Output ('  ' + $e.TimeCreated.ToString('MM-dd HH:mm:ss') + ' ' + (Esc (Clip $e.Message 110)))
        $n++
    }
} catch { Write-Output ('  UNKNOWN scm-7036: ' + (Esc $_.Exception.Message)) }

Write-Output $Sentinel
exit 0
