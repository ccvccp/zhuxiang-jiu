# td12 follow-up, READ-ONLY evidence probe #2 for 8.147.66.237 (zyjiu.com, i-0jl2lh29xbe4d0sownat).
# Sentinel: VHLOG8147-DONE
#
# WHY A SECOND PROBE. Probe #1 (VHAGENT8147, InvokeId t-wl072tu7hp1lxxc, ExitCode 0) already
# settled the attribution, so this file re-derives nothing and only closes the loop:
#   * C:\backend\scripts\voice_health_agent.py EXISTS -- 10327 bytes, 276 lines,
#     sha256 DC50E99F3E6997700D2AA3FFF920EB77F99BC22DBE928551E0CEDEAF6E52A107.
#   * It is RESTART-CAPABLE by direct reading, not by inference. Line 95 is literally
#         subprocess.run(["C:\\nginx\\nssm.exe", "restart", SVC], ...)
#     with SVC = "backend8147" (L36), gated by MAX_FAIL_BEFORE_RESTART = 3 (L37) and
#     RESTART_COOLDOWN_SEC = 3600 (L38). Two call sites: L218 restart_backend("service-down")
#     and L254-255 restart_backend("probes-failed-x3") behind the cooldown test at L249-251.
#   * System log 7036 shows NINE stop/run pairs inside 72 h, gaps alternating 60/70 minutes
#     (04:58, 06:08, 07:08, 08:18, 09:18, 10:18, 11:28, 12:28, 13:28), each stop->run only 1-2 s
#     apart, i.e. a clean nssm restart and not a crash. No 7034, no 7031, and the only Application
#     1000/1001 records are LoadPerf/WmiApRpl noise. The 10th pair (21:08:13 stop -> 21:08:47 run,
#     34 s apart) is the whole-machine reboot: lastBoot = 2026-11-11 21:08:44.
#     60-70 min alternating is what "3 failed probes at 10 min each, then a 1 h cooldown" produces.
#   ==> td6's "scheduled-task watchdog = excluded" is OVERTURNED. td6 scanned task definitions with
#     a regex that only matched SCM shapes (sc/nssm/Restart-Service) in the task's own command line,
#     which was "C:\Python312\python.exe C:\backend\scripts\voice_health_agent.py" -- it could never
#     see inside the .py. Also settled: lastResult=2 is NOT ERROR_FILE_NOT_FOUND, because the file
#     is there and python resolves from the machine PATH (C:\Python312\ is entry 2 of machinePath).
#
# WHY PROBE #1 LOST DATA, AND THE RULE THIS FILE OBEYS. Probe #1 ran one event query with no text
# filter -- DumpEvents 'System' @(7036,...) 'systemSvcEvents(all services)' '' -- which matched 971
# records and printed 120 of them: pure Windows noise (AppXSVC, Software Protection, WaaSMedicSvc,
# Network Setup Service, Windows Update). That blew the cloud assistant's ~24 KB Output cap, the
# middle of the transcript was silently dropped, and with it went the tail of [4/7] (runLevel,
# triggers, settings, lastRunTime, lastTaskResult, nextRunTime), all of [5/7] (full task scan) and
# all of [6/7] (services, nssm registry, processes). So the discipline here is explicit:
#   1. NO unfiltered wide query of any kind. Not one Get-WinEvent in this file.
#   2. Every list is capped, and prints CAP-HIT when the cap bit, so a short list is never
#      mistaken for a complete one.
#   3. The log is streamed line by line (never slurped), and only the tail plus the last few
#      restart-matching lines are emitted.
#   4. Total emitted text is budgeted to roughly 12 KB raw, i.e. ~16 KB base64, comfortably
#      inside the cap that probe #1 exceeded.
#
# WHAT THIS PROBE ADDS. The agent keeps its own evidence, declared at its L12-13 and L32-34:
#   C:/backend/logs/voice-health-state.json   (consec_fail, last_restart_ts)      -> [1/6]
#   C:/backend/logs/voice-health.json         (machine readable, atomic write)    -> [2/6]
#   C:/backend/logs/voice-health.log          (human readable per-round flow)     -> [3/6]
# Reading those is the difference between "attribution basically settled" and "attribution
# evidenced", and -- more usefully -- it says WHY the probes failed three rounds in a row. The
# standing suspicion is td8: the upstream TTS vendor answers code 1113 "insufficient balance",
# /api/xiaozhu/tts returns 503, and the agent imports websockets (L121) and opens a connection
# (L130), so if its probe path lands on that broken endpoint then td8 and td12 are ONE causal
# chain: upstream unpaid -> probes fail -> backend8147 restarted every hour. [3/6] is where that
# either shows up or dies. Sections [4/6]-[6/6] recover exactly what probe #1 lost and nothing more.
#
# STRICTLY READ-ONLY. No file under C:\backend or C:\nginx is written, created, moved or deleted.
# No registry write. No start/stop/restart/kill of any service, task or process. No task or service
# definition is altered. voice_health_agent.py is NOT modified -- the user authorized read-only
# investigation only, and that constraint is absolute here. Nothing is enabled, including the
# TaskScheduler Operational log (enabling it would be a write, so task-level events stay
# unavailable and this probe does not pretend otherwise). Fail-closed: a query that throws is
# reported UNKNOWN, never blank and never PASS. Secrets: the .py reads JWT_SECRET out of the nssm
# service registry (its L70), so [5/6] redacts any Parameters value whose name suggests environment
# or credential material instead of printing it, and every foreign string passes through Mask().
# All non-ASCII (the log is UTF-8 Chinese) is emitted as \uXXXX escapes, never as literal CJK, so
# this file stays pure ASCII on the wire and the transcript decodes losslessly on the local side.

$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'

$LogDir = 'C:\backend\logs'
$TaskName = 'voice-health-agent'
$Svcs = @('backend8147', 'nginx8147', 'redis8147')

# Output budget. These four numbers are the reason probe #1's transcript survived or did not.
$CapTail = 25        # log lines emitted from the tail
$CapLine = 180       # characters per emitted line
$CapHit = 10         # restart-matching log lines emitted
$CapJson = 1500      # characters per JSON file emitted

# Restart-line classification. td8 (2026-11-12) taught voice_health_agent.py to write the literal
# token 'no-restart' into actions when an upstream fault must NOT trigger a restart, so the bare
# (?i)restart below -- written the day before -- now counts an explicit non-restart as a restart
# event. That inflates restartMatchingLines and breaks the 9:9 alignment with System 7036 that
# td12's attribution rests on. Only the documented negated token is excluded; nothing speculative,
# because a real restart line may legitimately mention cooldown. Both counts stay in the output,
# so narrowing can never hide a line: recall is unchanged, only the classification is.
$RestartRe    = '(?i)restart'
$RestartNotRe = '(?i)no[-_ ]?restart'

function Esc([string]$s) {
    if ($null -eq $s) { return '' }
    $sb = New-Object Text.StringBuilder
    foreach ($ch in ([string]$s).ToCharArray()) {
        $code = [int]$ch
        if ($code -ge 32 -and $code -le 126) { [void]$sb.Append($ch) }
        else { [void]$sb.Append('\u' + $code.ToString('x4')) }
    }
    return $sb.ToString()
}

function Cut([string]$s, [int]$n) {
    $t = [string]$s
    $t = $t -replace "`r`n", ' | '
    $t = $t -replace "`n", ' | '
    $t = $t -replace "`r", ' | '
    $t = $t -replace '\s+', ' '
    $t = $t.Trim()
    if ($t.Length -gt $n) { return ($t.Substring(0, $n) + '..[TRUNC]') }
    return $t
}

# net.exe/nssm/python error text can carry a path, a header or a connection fragment. Mask first.
function Mask([string]$s) {
    $t = [string]$s
    $t = [regex]::Replace($t, '(?i)(jwt_secret|secret|password|passwd|pwd|token|api_key|apikey|access_key|accesskey|authorization|bearer)(\s*[=:]\s*)([^\s,;"\|]+)', '$1$2<MASKED>')
    return $t
}

function HexOf([byte[]]$b) {
    return (([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b))) -replace '-', '')
}

function Ts($d) {
    if ($null -eq $d) { return '<null>' }
    try { return ([datetime]$d).ToString('yyyy-MM-dd HH:mm:ss') } catch { return '<unparseable>' }
}

# The agent writes UTF-8; a future edit could add a BOM; PowerShell 5.1 would otherwise decode
# UTF-8-without-BOM as GBK and turn every Chinese line into mojibake. Sniff, never assume.
function ReadTextAuto([string]$path) {
    try { $b = [IO.File]::ReadAllBytes($path) } catch { return $null }
    if ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF) {
        return [Text.Encoding]::UTF8.GetString($b, 3, $b.Length - 3)
    }
    if ($b.Length -ge 2 -and $b[0] -eq 0xFF -and $b[1] -eq 0xFE) {
        return [Text.Encoding]::Unicode.GetString($b, 2, $b.Length - 2)
    }
    return [Text.Encoding]::UTF8.GetString($b)
}

function DumpJsonFile([string]$path, [string]$label) {
    Write-Host ('==> ' + $label + '  ' + $path)
    try {
        if (-not (Test-Path -LiteralPath $path)) {
            Write-Host '    MISSING-ON-DISK'
            return
        }
        $fi = Get-Item -LiteralPath $path
        $bytes = [IO.File]::ReadAllBytes($path)
        Write-Host ('    bytes=' + $fi.Length + ' sha256=' + (HexOf $bytes) + ' lastWrite=' + (Ts $fi.LastWriteTime) + ' creation=' + (Ts $fi.CreationTime))
        $txt = ReadTextAuto $path
        if ($null -eq $txt) { Write-Host '    READ-FAILED (unknown)'; return }
        Write-Host ('    raw=' + (Esc (Cut (Mask $txt) $CapJson)))
    } catch {
        Write-Host ('    UNKNOWN ' + (Esc (Cut $_.Exception.Message 200)))
    }
}

Write-Host '===== VHLOG8147 read-only evidence probe #2 ====='
$now = Get-Date
Write-Host ('    localNow=' + (Ts $now) + ' utcNow=' + (Ts $now.ToUniversalTime()))
Write-Host ('    host=' + $env:COMPUTERNAME + ' user=' + $env:USERNAME)
Write-Host ('    caps: tail=' + $CapTail + ' lineChars=' + $CapLine + ' restartLines=' + $CapHit + ' jsonChars=' + $CapJson)

# ---------------------------------------------------------------- [1/6] state
DumpJsonFile (Join-Path $LogDir 'voice-health-state.json') '[1/6] voice-health-state.json (consec_fail / last_restart_ts)'

# ---------------------------------------------------------------- [2/6] report
DumpJsonFile (Join-Path $LogDir 'voice-health.json') '[2/6] voice-health.json (machine readable report, atomic write)'

# ---------------------------------------------------------------- [3/6] flow log
Write-Host ('==> [3/6] voice-health.log  ' + (Join-Path $LogDir 'voice-health.log'))
$FlowLog = Join-Path $LogDir 'voice-health.log'
try {
    if (-not (Test-Path -LiteralPath $FlowLog)) {
        Write-Host '    MISSING-ON-DISK'
    } else {
        $fi = Get-Item -LiteralPath $FlowLog
        Write-Host ('    bytes=' + $fi.Length + ' lastWrite=' + (Ts $fi.LastWriteTime) + ' creation=' + (Ts $fi.CreationTime))
        # Single streaming pass. Never slurp: this file is appended every round, forever.
        $total = 0
        $restartTotal = 0
        $restartEvents = 0
        $restartNegated = 0
        $tail = New-Object 'System.Collections.Generic.Queue[string]'
        $hits = New-Object 'System.Collections.Generic.Queue[string]'
        $sr = New-Object IO.StreamReader($FlowLog, [Text.Encoding]::UTF8)
        try {
            while ($null -ne ($line = $sr.ReadLine())) {
                $total++
                if ($tail.Count -ge $CapTail) { [void]$tail.Dequeue() }
                $tail.Enqueue($total.ToString() + '|' + $line)
                if ($line -match $RestartRe) {
                    $restartTotal++
                    if ($line -match $RestartNotRe) {
                        $restartNegated++
                    } else {
                        $restartEvents++
                        if ($hits.Count -ge $CapHit) { [void]$hits.Dequeue() }
                        $hits.Enqueue($total.ToString() + '|' + $line)
                    }
                }
            }
        } finally { $sr.Close() }
        Write-Host ('    lineCount=' + $total + ' restartMatchingLines=' + $restartTotal + ' restartEvents=' + $restartEvents + ' negatedNoRestart=' + $restartNegated + ' (tailCapped=' + $CapTail + ' hitCapped=' + $CapHit + ')')
        Write-Host '    --- last restart-event lines (negated no-restart excluded) ---'
        if ($hits.Count -eq 0) { Write-Host '    NONE' } else { foreach ($h in $hits) { Write-Host ('      ' + (Esc (Cut (Mask $h) $CapLine))) } }
        Write-Host '    --- tail ---'
        foreach ($t in $tail) { Write-Host ('      ' + (Esc (Cut (Mask $t) $CapLine))) }
    }
} catch {
    Write-Host ('    UNKNOWN ' + (Esc (Cut $_.Exception.Message 200)))
}

# Which evidence files actually exist. Filtered to the agent's own prefix on purpose: an unfiltered
# directory dump of a production log folder is exactly the kind of wide query that ate probe #1.
Write-Host ('==> [3b/6] evidence files matching voice-health* under ' + $LogDir)
try {
    $ev = @(Get-ChildItem -LiteralPath $LogDir -Filter 'voice-health*' -File -ErrorAction Stop | Sort-Object LastWriteTime -Descending)
    Write-Host ('    count=' + $ev.Count)
    $i = 0
    foreach ($f in $ev) {
        $i++
        if ($i -gt 15) { Write-Host '    CAP-HIT (15)'; break }
        Write-Host ('      ' + $f.Name + ' ' + $f.Length + 'B ' + (Ts $f.LastWriteTime))
    }
    if ($ev.Count -eq 0) { Write-Host '    NONE-FOUND' }
} catch {
    Write-Host ('    UNKNOWN ' + (Esc (Cut $_.Exception.Message 200)))
}

# ---------------------------------------------------------------- [4/6] task
# Exactly the fields probe #1 lost to truncation, and nothing else.
Write-Host ('==> [4/6] scheduled task info  \' + $TaskName)
try {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host ('    state=' + $t.State + ' taskPath=' + (Esc $t.TaskPath))
    $p = $t.Principal
    if ($null -ne $p) {
        Write-Host ('    principal userId=' + (Esc $p.UserId) + ' logonType=' + $p.LogonType + ' runLevel=' + $p.RunLevel + ' groupId=' + (Esc $p.GroupId) + ' impersonate=' + $p.ImpersonateAccount)
    } else { Write-Host '    principal UNKNOWN (null)' }
    $s = $t.Settings
    if ($null -ne $s) {
        Write-Host ('    settings enabled=' + $s.Enabled + ' hidden=' + $s.Hidden + ' restartCount=' + $s.RestartCount + ' restartInterval=' + $s.RestartInterval + ' executionTimeLimit=' + $s.ExecutionTimeLimit + ' multipleInstances=' + $s.MultipleInstances + ' runOnlyIfIdle=' + $s.RunOnlyIfIdle + ' wakeToRun=' + $s.WakeToRun + ' disallowStartIfOnBatteries=' + $s.DisallowStartIfOnBatteries + ' stopIfGoingOnBatteries=' + $s.StopIfGoingOnBatteries + ' startWhenAvailable=' + $s.StartWhenAvailable)
    } else { Write-Host '    settings UNKNOWN (null)' }
    $tr = @($t.Triggers)
    Write-Host ('    triggerCount=' + $tr.Count)
    $k = 0
    foreach ($g in $tr) {
        $k++
        if ($k -gt 5) { Write-Host '    CAP-HIT (5 triggers)'; break }
        $rep = $g.Repetition
        $repTxt = '<null>'
        if ($null -ne $rep) { $repTxt = ('interval=' + $rep.Interval + ' duration=' + $rep.Duration + ' stopAtDurationEnd=' + $rep.StopAtDurationEnd) }
        Write-Host ('      trigger[' + $k + '] class=' + $g.CimClass.CimClassName + ' enabled=' + $g.Enabled + ' startBoundary=' + (Esc $g.StartBoundary) + ' endBoundary=' + (Esc $g.EndBoundary) + ' repetition(' + $repTxt + ')')
    }
    $info = $t | Get-ScheduledTaskInfo -ErrorAction Stop
    Write-Host ('    lastRunTime=' + (Ts $info.LastRunTime) + ' lastTaskResult=' + $info.LastTaskResult + ' nextRunTime=' + (Ts $info.NextRunTime) + ' missedRuns=' + $info.NumberOfMissedRuns)
    # LastTaskResult decoding, stated rather than guessed. 2 = ERROR_FILE_NOT_FOUND was probe #1's
    # hypothesis and is now dead (file exists, python resolves), so whatever this reads is the
    # script's own exit code. 0x41301 = task currently running, 0x41303 = has not yet run.
    $lr = [string]$info.LastTaskResult
    $hex = ''
    try { $hex = ('0x{0:X8}' -f ([int64]$info.LastTaskResult -band 0xFFFFFFFF)) } catch { $hex = '<unparseable>' }
    Write-Host ('    lastTaskResultHex=' + $hex + ' (decimal=' + $lr + ')')
} catch {
    Write-Host ('    UNKNOWN ' + (Esc (Cut $_.Exception.Message 240)))
}

# ---------------------------------------------------------------- [5/6] nssm registry
# The competing hypothesis that probe #1 never got to test: nssm's own AppExit=Restart could
# restart backend8147 without the python agent being involved at all. If AppExit says Restart and
# AppRestartDelay is small, the two mechanisms are indistinguishable from the 7036 pairs alone --
# but the 60-70 minute alternating gap does not look like an immediate crash-restart, and the agent's
# own logs in [1/6]-[3/6] are what will separate them. Read the registry either way; it is free.
Write-Host '==> [5/6] nssm Parameters registry (competing hypothesis: AppExit=Restart)'
foreach ($svc in $Svcs) {
    $rp = 'HKLM:\SYSTEM\CurrentControlSet\Services\' + $svc + '\Parameters'
    Write-Host ('    --- ' + $svc + '  ' + $rp)
    try {
        if (-not (Test-Path -LiteralPath $rp)) { Write-Host '      MISSING-KEY'; continue }
        $it = Get-Item -LiteralPath $rp -ErrorAction Stop
        $names = @($it.Property)
        Write-Host ('      valueCount=' + $names.Count + ' subKeyCount=' + @($it.GetSubKeyNames()).Count)
        foreach ($n in $names) {
            # AppEnvironmentExtra is where nssm keeps the service environment, and voice_health_agent.py
            # L70 says it pulls JWT_SECRET out of exactly there. Never print it: redact by name.
            if ($n -match '(?i)(environment|extra|secret|token|password|passwd|credential|apikey|api_key)') {
                $vlen = 0
                try { $vlen = ([string]($it.GetValue($n))).Length } catch { $vlen = -1 }
                Write-Host ('      ' + (Esc $n) + ' = <REDACTED-BY-NAME len=' + $vlen + '>')
                continue
            }
            $kind = ''
            try { $kind = [string]$it.GetValueKind($n) } catch { $kind = 'UNKNOWN' }
            $val = $null
            try { $val = $it.GetValue($n) } catch { Write-Host ('      ' + (Esc $n) + ' = <READ-FAILED>'); continue }
            if ($val -is [array]) { $vs = ($val | ForEach-Object { [string]$_ }) -join ' ; ' } else { $vs = [string]$val }
            Write-Host ('      ' + (Esc $n) + ' [' + $kind + '] = ' + (Esc (Cut (Mask $vs) 200)))
        }
        foreach ($skn in @($it.GetSubKeyNames())) {
            # nssm stores per-exit-code actions as subkeys named after the code.
            Write-Host ('      subkey=' + (Esc $skn))
        }
    } catch {
        Write-Host ('      UNKNOWN ' + (Esc (Cut $_.Exception.Message 200)))
    }
}

# ---------------------------------------------------------------- [6/6] services + processes
Write-Host '==> [6/6] services, PIDs and process start times'
foreach ($svc in $Svcs) {
    try {
        $w = Get-CimInstance -ClassName Win32_Service -Filter ("Name='" + $svc + "'") -ErrorAction Stop
        if ($null -eq $w) { Write-Host ('    ' + $svc + ' NOT-FOUND'); continue }
        Write-Host ('    --- ' + $svc + ' state=' + $w.State + ' startMode=' + $w.StartMode + ' startName=' + (Esc $w.StartName) + ' pid=' + $w.ProcessId + ' exitCode=' + $w.ExitCode)
        Write-Host ('        pathName=' + (Esc (Cut (Mask $w.PathName) 240)))
        $pid1 = [int]$w.ProcessId
        if ($pid1 -gt 0) {
            try {
                $pr = Get-Process -Id $pid1 -ErrorAction Stop
                Write-Host ('        proc name=' + $pr.ProcessName + ' startTime=' + (Ts $pr.StartTime) + ' uptimeMin=' + [math]::Round(((Get-Date) - $pr.StartTime).TotalMinutes, 1))
            } catch { Write-Host ('        proc UNKNOWN ' + (Esc (Cut $_.Exception.Message 160))) }
            # The service PID is nssm.exe; the real backend is its child. Without the child's start
            # time there is no way to tell an nssm-level restart from an app-level restart.
            try {
                $kids = @(Get-CimInstance -ClassName Win32_Process -Filter ('ParentProcessId=' + $pid1) -ErrorAction Stop)
                Write-Host ('        childCount=' + $kids.Count)
                $j = 0
                foreach ($c in $kids) {
                    $j++
                    if ($j -gt 4) { Write-Host '        CAP-HIT (4 children)'; break }
                    Write-Host ('          child pid=' + $c.ProcessId + ' name=' + (Esc $c.Name) + ' created=' + (Ts $c.CreationDate) + ' cmd=' + (Esc (Cut (Mask $c.CommandLine) 160)))
                }
            } catch { Write-Host ('        children UNKNOWN ' + (Esc (Cut $_.Exception.Message 160))) }
        } else {
            Write-Host '        pid=0 (service not running)'
        }
    } catch {
        Write-Host ('    ' + $svc + ' UNKNOWN ' + (Esc (Cut $_.Exception.Message 200)))
    }
}

# Any stray python process is evidence about the agent itself: if voice_health_agent.py is resident
# rather than per-run, its restart loop is in-process and the scheduled task is only the launcher.
Write-Host '==> [6b/6] python processes on the box'
try {
    $py = @(Get-CimInstance -ClassName Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop)
    Write-Host ('    pythonExeCount=' + $py.Count)
    $m = 0
    foreach ($q in $py) {
        $m++
        if ($m -gt 6) { Write-Host '    CAP-HIT (6)'; break }
        Write-Host ('      pid=' + $q.ProcessId + ' ppid=' + $q.ParentProcessId + ' created=' + (Ts $q.CreationDate) + ' cmd=' + (Esc (Cut (Mask $q.CommandLine) 200)))
    }
} catch {
    Write-Host ('    UNKNOWN ' + (Esc (Cut $_.Exception.Message 200)))
}

# Honest scope statement, same wording discipline as probe #1 after its correction: the ONLY
# filesystem effect anywhere in this run is the wire wrapper materialising this script into
# $env:TEMP before executing it (the gzip payload path, needed because the raw cap is 18432 bytes).
# Nothing under investigation is touched.
Write-Host 'READ-ONLY-ASSERT: no write under investigation, no start/stop, no kill, no task/service/registry change, voice_health_agent.py NOT modified'
Write-Host ('READ-ONLY-CAVEAT: wire wrapper wrote its own copy to ' + (Join-Path $env:TEMP 'trae_vhlog8147.ps1') + ' -- temp artifact only')
Write-Host 'VHLOG8147-DONE'
