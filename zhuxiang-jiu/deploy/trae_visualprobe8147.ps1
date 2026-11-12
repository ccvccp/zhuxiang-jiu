# Read-only inventory probe for the 4th resident process on 8.147.66.237, plus the
# residual-exposure baseline. Sentinel: VISUALPROBE8147-DONE
#
# QUESTION (todo td12b): C:\www\visual\visual_server.py is a 4th resident process and does
# not belong to the three nssm services (nginx8147 / redis8147 / backend8147). How is it
# started, and what keeps it alive across a reboot? Last observed: pid 1820, ppid 1276,
# created 21:08:45 -- one second after lastBoot 21:08:44.
#
# WHY THIS PROBE IS NARROW: repo history already answers most of the question, so this is a
# hypothesis test rather than an open-ended sweep. deploy\ecs8147-visualschtasks.ps1 registers
#   schtasks /Create /TN VisualSearch
#     /TR "C:\Python312\python.exe C:\www\visual\visual_server.py"
#     /SC ONSTART /RU SYSTEM /RL HIGHEST /F
# and first deletes an older nssm service of the same name (its header says: drop nssm, use
# schtasks; ONSTART autostart; server defaults are already 8200 / C:\www\visual). Two docs
# agree: the agent startup plan (L79, "schtasks ONSTART after nssm registration was proven to
# fail") and the failover migration runbook (L314, "new box lands C:\www\visual + schtasks
# VisualSearch ONSTART autostart").
# BUT deploy\tmp\p0fixE-nssm-cutover1.ps1 L82 lists BOTH svc='visual8147' AND
# task='VisualSearch', so a later cut-over to an nssm-wrapped service may have happened.
# The local evidence contradicts itself; only the box can decide.
#
# THE DECIDING FACT: the parent process of the python that runs visual_server.py.
#   taskeng.exe / svchost.exe -k netsvcs  -> Task Scheduler engine, schtasks hypothesis holds
#   services.exe                          -> a plain SCM service
#   nssm.exe                              -> nssm-wrapped service, visual8147 hypothesis holds
# [4/8] cross-checks this by counting nssm-wrapped services: 3 means visual is NOT wrapped,
# 4 means it is.
#
# THIS SCRIPT PERFORMS NO WRITE. READ-ONLY-ASSERT: it opens files for read, queries CIM /
# Get-ScheduledTask / Get-NetTCPConnection / Get-NetFirewallProfile / registry reads plus two
# loopback HTTP GETs, and writes only to stdout. [1/8] and [8/8] stamp the target file and the
# task state before and after so "no write" is falsifiable from the output alone.
# READ-ONLY-CAVEAT-1: the cloud assistant wrapper itself materialises a copy of this script
# under $env:TEMP before running it, so "no write" is true of the script body, not of the
# transport.
# READ-ONLY-CAVEAT-2: under the cloud assistant HKCU resolves to the SYSTEM account hive, not
# the RDP user's hive, so the Run-key result in [6/8] describes the SYSTEM side only.
#
# OUTPUT BUDGET (an earlier probe blew the ~24 KB Output cap and silently lost its tail
# because a wide Get-WinEvent query matched 971 events): every collection below is capped by
# a constant, log tails are not read at all, and no Get-WinEvent is used anywhere.
$ErrorActionPreference = 'Stop'
$Sentinel  = 'VISUALPROBE8147-DONE'
$VRoot     = 'C:\www\visual'
$VTarget   = 'C:\www\visual\visual_server.py'
$TaskName  = 'VisualSearch'
$ExpShaPre = '0B7AAAC8C0A99908'
$CapDir    = 24
$CapTask   = 5
$CapSvc    = 8
$CapPort   = 26
$CapLine   = 200
$preStamp  = 'ABSENT'
$preTask   = 'ABSENT'

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

# ---- [1/8] no-write baseline, taken before anything else touches the box
Write-Output '[1/8] nowrite-pre'
try {
    $fi = Get-Item -LiteralPath $VTarget -ErrorAction Stop
    $preStamp = $fi.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss.fff') + '|' + $fi.Length
    Write-Output ('  targetStamp=' + $preStamp)
} catch { Write-Output ('  UNKNOWN nowrite-pre: ' + (Esc $_.Exception.Message)) }
try {
    $t0 = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $preTask = [string]$t0.State
    Write-Output ('  taskState=' + $preTask)
} catch { Write-Output ('  taskState=ABSENT reason=' + (Esc $_.Exception.Message)) }

# ---- [2/8] process tree: who is the parent of the python running visual_server.py?
Write-Output '[2/8] process-tree'
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    Write-Output ('  lastBoot=' + $os.LastBootUpTime)
} catch { Write-Output ('  UNKNOWN lastBoot: ' + (Esc $_.Exception.Message)) }
try {
    $py = @(Get-CimInstance Win32_Process -ErrorAction Stop |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match 'visual_server') })
    Write-Output ('  matches=' + $py.Count)
    foreach ($p in $py) {
        Write-Output ('  pid=' + $p.ProcessId + ' ppid=' + $p.ParentProcessId + ' name=' + $p.Name + ' created=' + $p.CreationDate)
        Write-Output ('    cmd=' + (Esc (Clip $p.CommandLine $CapLine)))
        Write-Output ('    exe=' + (Esc $p.ExecutablePath))
        $pp = @(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $p.ParentProcessId) -ErrorAction SilentlyContinue)
        if ($pp.Count -eq 0) {
            Write-Output ('    parent=' + $p.ParentProcessId + ' GONE (exited before this probe; pid may have been reused)')
        } else {
            $q = $pp[0]
            Write-Output ('    PARENT pid=' + $q.ProcessId + ' name=' + $q.Name + ' created=' + $q.CreationDate)
            Write-Output ('    PARENT cmd=' + (Esc (Clip $q.CommandLine $CapLine)))
            Write-Output ('    PARENT exe=' + (Esc $q.ExecutablePath))
            $gp = @(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $q.ParentProcessId) -ErrorAction SilentlyContinue)
            if ($gp.Count -gt 0) {
                Write-Output ('    GRANDPARENT pid=' + $gp[0].ProcessId + ' name=' + $gp[0].Name)
            } else {
                Write-Output ('    GRANDPARENT ' + $q.ParentProcessId + ' GONE')
            }
        }
    }
} catch { Write-Output ('  UNKNOWN process-tree: ' + (Esc $_.Exception.Message)) }

# ---- [3/8] scheduled task: the VisualSearch hypothesis, plus any other task mentioning visual
Write-Output '[3/8] scheduled-task'
try {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Output ('  taskPath=' + $t.TaskPath + ' state=' + $t.State)
    foreach ($a in $t.Actions) {
        Write-Output ('  action exe=' + (Esc $a.Execute) + ' args=' + (Esc (Clip $a.Arguments 160)) + ' wd=' + (Esc $a.WorkingDirectory))
    }
    foreach ($tr in $t.Triggers) {
        Write-Output ('  trigger=' + $tr.CimClass.CimClassName + ' enabled=' + $tr.Enabled)
    }
    Write-Output ('  principal user=' + $t.Principal.UserId + ' runLevel=' + $t.Principal.RunLevel + ' logonType=' + $t.Principal.LogonType)
    Write-Output ('  settings execLimit=' + $t.Settings.ExecutionTimeLimit + ' restartCount=' + $t.Settings.RestartCount + ' multiInst=' + $t.Settings.MultipleInstances + ' startWhenAvailable=' + $t.Settings.StartWhenAvailable)
    $i = $t | Get-ScheduledTaskInfo -ErrorAction Stop
    Write-Output ('  lastRun=' + $i.LastRunTime + ' lastResult=' + $i.LastTaskResult + ' nextRun=' + $i.NextRunTime + ' missed=' + $i.NumberOfMissedRuns)
} catch { Write-Output ('  UNKNOWN task-VisualSearch: ' + (Esc $_.Exception.Message)) }
try {
    $others = @(Get-ScheduledTask -ErrorAction Stop | Where-Object {
            $_.TaskName -ne $TaskName -and (@($_.Actions | Where-Object {
                    ($_.Execute -and ($_.Execute -match 'visual')) -or
                    ($_.Arguments -and ($_.Arguments -match 'visual'))
                })).Count -gt 0
        })
    Write-Output ('  otherVisualTasks=' + $others.Count + ' cap=' + $CapTask)
    $n = 0
    foreach ($o in $others) {
        if ($n -ge $CapTask) { Write-Output '    ...truncated-by-cap'; break }
        Write-Output ('    ' + $o.TaskPath + $o.TaskName + ' state=' + $o.State)
        $n++
    }
} catch { Write-Output ('  UNKNOWN task-scan: ' + (Esc $_.Exception.Message)) }

# ---- [4/8] services: the visual8147 hypothesis, and how many services nssm wraps in total
Write-Output '[4/8] services'
try {
    $sv = @(Get-CimInstance Win32_Service -Filter "Name LIKE '%visual%'" -ErrorAction Stop)
    Write-Output ('  visualNamedServices=' + $sv.Count + ' cap=' + $CapSvc)
    $n = 0
    foreach ($s in $sv) {
        if ($n -ge $CapSvc) { Write-Output '    ...truncated-by-cap'; break }
        Write-Output ('    name=' + $s.Name + ' state=' + $s.State + ' startMode=' + $s.StartMode + ' pid=' + $s.ProcessId + ' startName=' + $s.StartName)
        Write-Output ('      path=' + (Esc (Clip $s.PathName $CapLine)))
        $n++
    }
} catch { Write-Output ('  UNKNOWN services-visual: ' + (Esc $_.Exception.Message)) }
try {
    $sv2 = @(Get-CimInstance Win32_Service -Filter "PathName LIKE '%nssm%'" -ErrorAction Stop)
    Write-Output ('  nssmWrappedTotal=' + $sv2.Count + '  (3 = visual is NOT nssm-wrapped, 4 = it is)')
    foreach ($s in $sv2) {
        Write-Output ('    ' + $s.Name + ' state=' + $s.State + ' startMode=' + $s.StartMode + ' pid=' + $s.ProcessId)
    }
} catch { Write-Output ('  UNKNOWN services-nssm: ' + (Esc $_.Exception.Message)) }
try {
    Write-Output ('  nssmExePresent=' + (Test-Path -LiteralPath 'C:\nginx\nssm.exe'))
} catch { Write-Output ('  UNKNOWN nssm-exe: ' + (Esc $_.Exception.Message)) }

# ---- [5/8] file state: is the deployed visual_server.py still the F-segment revision?
Write-Output '[5/8] file-state'
try {
    $b = [IO.File]::ReadAllBytes($VTarget)
    $sha = ShaOf $b
    $fi2 = Get-Item -LiteralPath $VTarget
    Write-Output ('  bytes=' + $b.Length + ' sha=' + $sha)
    Write-Output ('  mtime=' + $fi2.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') + ' shaHasExpectedPrefix=' + $sha.StartsWith($ExpShaPre) + ' expectedPrefix=' + $ExpShaPre)
    $bom = ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)
    $hi = 0
    foreach ($x in $b) { if ($x -gt 127) { $hi++ } }
    Write-Output ('  bom=' + $bom + ' nonAsciiBytes=' + $hi + ' (repo note: this file is GBK-encoded, not UTF-8)')
} catch { Write-Output ('  UNKNOWN file-state: ' + (Esc $_.Exception.Message)) }
try {
    $d = @(Get-ChildItem -LiteralPath $VRoot -Force -ErrorAction Stop | Sort-Object Name)
    Write-Output ('  dirEntries=' + $d.Count + ' cap=' + $CapDir)
    $n = 0
    foreach ($e in $d) {
        if ($n -ge $CapDir) { Write-Output '    ...truncated-by-cap'; break }
        if ($e.PSIsContainer) {
            Write-Output ('    [d] ' + $e.Name)
        } else {
            Write-Output ('    [f] ' + $e.Name + ' ' + $e.Length + 'B ' + $e.LastWriteTime.ToString('MM-dd HH:mm:ss'))
        }
        $n++
    }
} catch { Write-Output ('  UNKNOWN dir: ' + (Esc $_.Exception.Message)) }

# ---- [6/8] Run keys (SYSTEM hive only, see caveat 2) and two loopback health GETs
Write-Output '[6/8] run-keys-and-healthz'
foreach ($rk in @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run')) {
    try {
        $p = Get-ItemProperty -LiteralPath $rk -ErrorAction Stop
        $vals = @($p.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' })
        Write-Output ('  ' + $rk + ' values=' + $vals.Count)
        foreach ($v in $vals) {
            Write-Output ('    ' + $v.Name + ' = ' + (Esc (Clip ([string]$v.Value) $CapLine)))
        }
    } catch { Write-Output ('  ' + $rk + ' UNKNOWN reason=' + (Esc $_.Exception.Message)) }
}
try {
    $h1 = & curl.exe -s -m 8 http://127.0.0.1:8200/healthz
    Write-Output ('  direct8200=' + (Esc (Clip ([string]$h1).Trim() 240)))
} catch { Write-Output ('  UNKNOWN direct8200: ' + (Esc $_.Exception.Message)) }
try {
    $h2 = & curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' http://127.0.0.1/api/visual/healthz
    Write-Output ('  viaNginxCode=' + (Esc (Clip ([string]$h2).Trim() 40)))
} catch { Write-Output ('  UNKNOWN viaNginx: ' + (Esc $_.Exception.Message)) }

# ---- [7/8] residual exposure baseline (todo td9, read-only half): firewall, RDP, listeners
Write-Output '[7/8] exposure-baseline'
try {
    foreach ($fp in (Get-NetFirewallProfile -ErrorAction Stop)) {
        Write-Output ('  fw ' + $fp.Name + ' enabled=' + $fp.Enabled + ' defaultIn=' + $fp.DefaultInboundAction + ' defaultOut=' + $fp.DefaultOutboundAction)
    }
} catch { Write-Output ('  UNKNOWN firewall: ' + (Esc $_.Exception.Message)) }
try {
    $ts = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -ErrorAction Stop
    $ws = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name PortNumber -ErrorAction Stop
    Write-Output ('  rdp fDenyTSConnections=' + $ts.fDenyTSConnections + ' PortNumber=' + $ws.PortNumber)
} catch { Write-Output ('  UNKNOWN rdp-reg: ' + (Esc $_.Exception.Message)) }
try {
    $l = @(Get-NetTCPConnection -State Listen -ErrorAction Stop)
    $wild = @($l | Where-Object { $_.LocalAddress -eq '0.0.0.0' -or $_.LocalAddress -eq '::' })
    Write-Output ('  listenTotal=' + $l.Count + ' wildcardTotal=' + $wild.Count + ' cap=' + $CapPort)
    $seen = @{}
    $n = 0
    foreach ($c in ($wild | Sort-Object LocalPort)) {
        $k = [string]$c.LocalAddress + ':' + [string]$c.LocalPort
        if ($seen.ContainsKey($k)) { continue }
        $seen[$k] = 1
        if ($n -ge $CapPort) { Write-Output '    ...truncated-by-cap'; break }
        $op = @(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $c.OwningProcess) -ErrorAction SilentlyContinue)
        if ($op.Count -gt 0) { $on = $op[0].Name } else { $on = 'GONE' }
        Write-Output ('    ' + $k + ' pid=' + $c.OwningProcess + ' ' + $on)
        $n++
    }
    foreach ($fpn in @(22, 3389, 8200)) {
        $hit = @($l | Where-Object { $_.LocalPort -eq $fpn })
        if ($hit.Count -eq 0) {
            Write-Output ('    focus ' + $fpn + ' = NOT-LISTENING')
        } else {
            foreach ($c in $hit) { Write-Output ('    focus ' + $fpn + ' = ' + $c.LocalAddress + ' pid=' + $c.OwningProcess) }
        }
    }
} catch { Write-Output ('  UNKNOWN listen: ' + (Esc $_.Exception.Message)) }
try {
    $sd = @(Get-CimInstance Win32_Service -Filter "Name='sshd'" -ErrorAction Stop)
    if ($sd.Count -eq 0) {
        Write-Output '  sshd=ABSENT'
    } else {
        foreach ($s in $sd) { Write-Output ('  sshd state=' + $s.State + ' startMode=' + $s.StartMode + ' pid=' + $s.ProcessId) }
    }
} catch { Write-Output ('  UNKNOWN sshd: ' + (Esc $_.Exception.Message)) }

# ---- [8/8] no-write proof: the two stamps from [1/8] must be byte-identical
Write-Output '[8/8] nowrite-post'
try {
    $fi3 = Get-Item -LiteralPath $VTarget -ErrorAction Stop
    $postStamp = $fi3.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss.fff') + '|' + $fi3.Length
    Write-Output ('  targetStamp=' + $postStamp + ' unchanged=' + ($postStamp -eq $preStamp))
} catch { Write-Output ('  UNKNOWN nowrite-post: ' + (Esc $_.Exception.Message)) }
try {
    $t2 = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Output ('  taskState=' + $t2.State + ' unchanged=' + (([string]$t2.State) -eq $preTask))
} catch { Write-Output ('  taskState=ABSENT unchanged=' + ($preTask -eq 'ABSENT')) }

Write-Output $Sentinel
exit 0
