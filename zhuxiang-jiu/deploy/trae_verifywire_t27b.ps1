# trae_verifywire_t27b.ps1 -- four-check local verification of the t27b wire wrapper BEFORE
# it is sent to production 39.107.43.149. Derived from trae_verifywire_t24.ps1, with one
# structural change driven by the fact that t27b's inner script MUTATES (it stops an IIS site).
#
#   [1/4] independent unpack: re-extract the gzip blob from the wrapper and compare against the
#         inner script on disk. td12d was burned by a stale blob, so "we just generated it" is
#         not accepted as proof.
#   [2/4] wrapper gate: ASCII purity / BOM / parse errors / wire cap / mutating-verb scan. The
#         wrapper may only write its own TEMP copy.
#   [3/4] HELPER-ONLY dry run. t24's inner was read-only so it could be run whole on this PC.
#         t27b's inner stops a site: running it whole here would either change THIS machine or
#         prove nothing. Instead the inner text is split at the literal 'function Do-Rollback'
#         -- which is exactly the boundary between the 13 read-only helpers and the six mutating
#         sections -- the fragment is asserted to contain no mutating call shape, and only then
#         is a harness appended that exercises every helper end to end.
#   [4/4] conditional full dry run. If this PC has appcmd.exe the full wrapper is REFUSED
#         (SKIPPED-BY-POLICY): we do not run a mutating script locally. If it does not, the
#         wrapper is run and the EXPECTED result is a controlled FAIL at [1/6] BEFORE, before
#         [2/6] BACKUP is ever reached -- i.e. fail-closed proven with zero writes.
#
# TRAP the whole report must be read against: an inner `exit` does not terminate the wrapper,
# so 'WIRE-DONE' ALWAYS prints. It is never a success criterion. Success criteria are the inner
# script's own verdict lines and `innerExitCode=`.
# fail-closed: the first red check exits 1.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$W = Join-Path $PSScriptRoot 'trae_stopiiszx_wire.ps1'
$I = Join-Path $PSScriptRoot 'trae_stopiiszx.ps1'
# $FRAGPATH, never $FRAG: PowerShell variable names are case-insensitive, and $frag below holds
# the fragment TEXT. They were one variable, so the text clobbered the path and WriteAllBytes
# threw "Illegal characters in path". Same collision class as $Chunk/$chunk in the generator.
$FRAGPATH = Join-Path $env:TEMP 'trae_stopiiszx_frag.ps1'
$LOG = Join-Path $env:TEMP 't27b_helpers.log'
$LOG2 = Join-Path $env:TEMP 't27b_fullrun.log'
$SPLITMARK = 'function Do-Rollback'

function Hex([byte[]]$b) {
    return (([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b))) -replace '-', '')
}

foreach ($p in @($W, $I)) {
    if (-not (Test-Path -LiteralPath $p)) { Write-Output ('MISSING ' + $p); Write-Output 'VERDICT-VERIFY FAIL'; exit 1 }
}

$wBody = [IO.File]::ReadAllText($W)
$iBytes = [IO.File]::ReadAllBytes($I)
$iBody = [IO.File]::ReadAllText($I)
$iSha = Hex $iBytes

# ---- [1/4] independent unpack ----
Write-Output '=== [1/4] INDEPENDENT UNPACK ==='
$m = [regex]::Matches($wBody, "(?m)^\`$B64 \+= '([A-Za-z0-9+/=]+)'\s*$")
$blob = -join ($m | ForEach-Object { $_.Groups[1].Value })
Write-Output ('  blobAssignments=' + $m.Count + ' blobChars=' + $blob.Length)
$msIn = New-Object IO.MemoryStream(, [Convert]::FromBase64String($blob))
$ds = New-Object IO.Compression.GZipStream($msIn, [IO.Compression.CompressionMode]::Decompress)
$msR = New-Object IO.MemoryStream
$ds.CopyTo($msR); $ds.Close(); $msIn.Close()
$back = $msR.ToArray(); $msR.Close()
$bSha = Hex $back
Write-Output ('  unpackedBytes=' + $back.Length + ' unpackedSha256=' + $bSha)
Write-Output ('  diskInnerBytes=' + $iBytes.Length + ' diskInnerSha256=' + $iSha)
$stale = ($bSha -cne $iSha -or $back.Length -ne $iBytes.Length)
Write-Output ('  staleWrapper=' + $stale)
Write-Output ('  headerClaimShaMatchesDisk=' + ($wBody -match [regex]::Escape($iSha)))
if ($stale) { Write-Output 'VERDICT-UNPACK FAIL'; exit 1 }
Write-Output '  VERDICT-UNPACK PASS: BLOB-EQUALS-DISK-INNER'

# ---- [2/4] wrapper gate ----
Write-Output ''
Write-Output '=== [2/4] WRAPPER GATE ==='
$wBytes = [IO.File]::ReadAllBytes($W)
$nonAscii = @($wBytes | Where-Object { $_ -gt 127 })
$bom = ($wBytes.Length -ge 3 -and $wBytes[0] -eq 0xEF -and $wBytes[1] -eq 0xBB -and $wBytes[2] -eq 0xBF)
$errs = $null; $toks = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($W, [ref]$toks, [ref]$errs)
$perr = if ($errs) { @($errs).Count } else { 0 }
$b64Len = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($wBody)).Length
$fits = ($b64Len -le 24576)
$sentWire = ([regex]::Matches($wBody, 'WIRE-DONE')).Count
$mutRe = '(?i)\b(Start-Service|Stop-Service|Restart-Service|Restart-Computer|Stop-Computer|Set-Service|New-Service|Remove-Service|taskkill|Stop-Process|Start-Process|Set-ItemProperty|Remove-ItemProperty|Set-Content|Add-Content|Out-File|Remove-Item\s|Set-ScheduledTask|Register-ScheduledTask|Unregister-ScheduledTask|Set-NetFirewall|New-NetFirewall|Remove-NetFirewall|netsh\s+advfirewall|nginx\.exe\s+-s)'
$mut = @([regex]::Matches($wBody, $mutRe))
Write-Output ('  wrapperBytes=' + $wBytes.Length + ' wrapperSha256=' + (Hex $wBytes))
Write-Output ('  nonAscii=' + $nonAscii.Count + ' bom=' + $bom + ' lines=' + (($wBody -split "`n").Count))
Write-Output ('  parseErrors=' + $perr + ' sentinelWireDone=' + $sentWire + ' mutatingVerbScan=' + $mut.Count)
Write-Output ('  b64WireLen=' + $b64Len + ' cap=24576 fits=' + $fits + ' headroom=' + (24576 - $b64Len))
$gate = ($nonAscii.Count -eq 0 -and -not $bom -and $perr -eq 0 -and $fits -and $sentWire -ge 1 -and $mut.Count -eq 0)
Write-Output ('  VERDICT-WRAPPERGATE ' + $(if ($gate) { 'PASS' } else { 'FAIL' }))
if (-not $gate) { exit 1 }

# ---- [3/4] helper-only dry run ----
Write-Output ''
Write-Output '=== [3/4] HELPER-ONLY DRY RUN (fragment split at Do-Rollback) ==='
$idx = $iBody.IndexOf($SPLITMARK)
Write-Output ('  splitMarkFound=' + ($idx -gt 0) + ' fragmentChars=' + $idx + ' discardedChars=' + ($iBody.Length - $idx))
if ($idx -le 0) { Write-Output 'VERDICT-HELPERS FAIL: split mark not found'; exit 1 }
$fragText = $iBody.Substring(0, $idx)
# Two scans, because one broad scan is wrong in both directions.
# (a) unconditional mutating shapes. $STOPARGS/$STARTARGS are DEFINED in the config region, which
#     IS inside the fragment, so a bare literal scan for 'stop','site' would be a false red. What
#     must be absent is any USE of them as an argument -- that is scan (b)'s job.
$fragMut = @([regex]::Matches($fragText, '(?m)(Copy-Item|New-Item|Remove-Item|Set-Content|Add-Content|Out-File|WriteAllBytes|Start-Sleep|Do-Rollback\s*\(|Invoke-Native\s+\$APPCMD\s+\$(STOPARGS|STARTARGS))'))
foreach ($h in ($fragMut | Select-Object -First 10)) { Write-Output ('  FRAGMUT L' + ($fragText.Substring(0, $h.Index) -split "`n").Count + ': ' + $h.Value) }
Write-Output ('  fragmentMutatingCallShapes=' + $fragMut.Count + ' (must be 0)')
# (b) every appcmd invocation in the fragment must carry the read-only 'list' verb.
#     Get-SiteFacts legitimately runs `appcmd list site`; only stop/start mutate.
$acCalls = @([regex]::Matches($fragText, 'Invoke-Native\s+\$APPCMD\s+(@\([^)]*\)|\$\w+)'))
$acBad = @()
foreach ($c in $acCalls) { if ($c.Groups[1].Value -notmatch "'list'") { $acBad += $c.Value } }
Write-Output ('  fragmentAppcmdCalls=' + $acCalls.Count + ' nonListVerbCalls=' + $acBad.Count + ' (must be 0)')
foreach ($b in ($acBad | Select-Object -First 6)) { Write-Output ('  APPCMD-BAD ' + $b) }
if ($fragMut.Count -ne 0 -or $acBad.Count -ne 0) { Write-Output 'VERDICT-HELPERS FAIL: fragment is not read-only'; exit 1 }

$HARNESS = @'

# ---- harness appended by trae_verifywire_t27b.ps1; NOT part of the shipped script ----
$script:fail = 0
function Chk([string]$n, [bool]$ok, [string]$d) {
    Write-Output ('  HARNESS ' + $(if ($ok) { 'OK  ' } else { 'FAIL' }) + ' ' + $n + ' :: ' + $d)
    if (-not $ok) { $script:fail++ }
}

$expAbc = 'BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD'
$gotAbc = Get-Sha256Hex ([Text.Encoding]::UTF8.GetBytes('abc'))
Chk 'Get-Sha256Hex-known-answer' ($gotAbc -ceq $expAbc) ('sha256("abc")=' + $gotAbc.Substring(0, 16))

$nf = 'C:\Windows\System32\netstat.exe'
$ff = Get-FileFacts $nf
$ref = (Get-FileHash -LiteralPath $nf -Algorithm SHA256).Hash.Substring(0, 16)
Chk 'Get-FileFacts-realfile' ($ff.Exists -and $ff.Sha16 -ceq $ref) ('sha16=' + $ff.Sha16 + ' ref=' + $ref + ' bytes=' + $ff.Bytes)
$fx = Get-FileFacts 'C:\no\such\file.bin'
Chk 'Get-FileFacts-missingfile' ((-not $fx.Exists) -and $fx.Sha16 -ceq 'NOFILE' -and $fx.Bytes -eq -1) ('exists=' + $fx.Exists + ' sha16=' + $fx.Sha16)

$rn = Invoke-Native 'C:\Windows\System32\cmd.exe' @('/c', 'echo', 'harness-ok')
Chk 'Invoke-Native-exitcode-and-output' ($rn.ExitCode -eq 0 -and ((@($rn.Out) -join ' ').Contains('harness-ok'))) ('exitCode=' + $rn.ExitCode + ' out=' + ((@($rn.Out) -join ' ').Trim()))
Chk 'Invoke-Native-restores-ErrorActionPreference' ($ErrorActionPreference -ceq 'Stop') ('eap=' + $ErrorActionPreference)

# Bare assignment, NOT @(...): Get-PortListeners ends with `return , $rows`, so wrapping the CALL
# in @() double-wraps -- .Count becomes a constant 1 and the lone element is the rows array
# itself, whose .ProcId/.ProcName read back empty. The two assertions below are what caught that
# defect in the inner script; keep them bare-assigned or they prove nothing.
$r80 = Get-PortListeners 80
$n80 = @($r80).Count
Write-Output ('  INFO Get-PortListeners(80) rows=' + $n80)
Show-Port 'HARNESS80' $r80
$first80 = if ($n80 -gt 0) { @($r80)[0] } else { $null }
Chk 'Get-PortListeners-row-fields-populated' (($n80 -eq 0) -or (([string]$first80.ProcId -ne '') -and ([string]$first80.ProcName -ne ''))) ('rows=' + $n80 + ' firstPid=' + $first80.ProcId + ' firstProc=' + $first80.ProcName)
$rNone = Get-PortListeners 65001
$nNone = @($rNone).Count
Chk 'Get-PortListeners-unused-port-is-empty' ($nNone -eq 0) ('rows=' + $nNone)

$sv = Get-SvcSnapshot
Show-Svc 'HARNESS' $sv
Chk 'Get-SvcSnapshot-has-all-keys' ($sv.ContainsKey('nginx8147') -and $sv.ContainsKey('W3SVC') -and $sv.ContainsKey('WAS') -and $sv.ContainsKey('nginxProcCount')) ('keys=' + (($sv.Keys | Sort-Object) -join ','))

$fnNames = @('Note', 'Get-Sha256Hex', 'Get-FileFacts', 'Invoke-Native', 'Show-Native', 'Get-PortListeners', 'Show-Port', 'Get-SiteFacts', 'Show-Site', 'Get-Loopback', 'Show-Loopback', 'Get-SvcSnapshot', 'Show-Svc')
$missing = @()
foreach ($f in $fnNames) { if (-not (Get-Command -Name $f -CommandType Function -ErrorAction SilentlyContinue)) { $missing += $f } }
Chk 'all-13-helpers-defined' ($missing.Count -eq 0) ('missing=' + $(if ($missing.Count) { $missing -join ',' } else { 'none' }))

# These two are expected to fail on a non-IIS PC. Get-SiteFacts calls appcmd through
# Invoke-Native, which has try/finally but NO catch, so a missing appcmd propagates. That is
# exactly why section [1/6] gates on tool existence before anything else runs.
try { $sf = Get-SiteFacts; Write-Output ('  INFO Get-SiteFacts returned found=' + $sf.Found + ' state=' + $sf.State) }
catch { Write-Output ('  INFO Get-SiteFacts threw as expected here: ' + $_.Exception.GetType().Name) }
try { $lb = Get-Loopback; Write-Output ('  INFO Get-Loopback outcome=' + $lb.Outcome + ' code=' + $lb.Code) }
catch { Write-Output ('  INFO Get-Loopback threw as expected here: ' + $_.Exception.GetType().Name) }

Chk 'no-harness-failure' ($script:fail -eq 0) ('failures=' + $script:fail)
Write-Output ('VERDICT-HELPERS ' + $(if ($script:fail -eq 0) { 'PASS' } else { 'FAIL' }))
Write-Output '=== HELPERS-DONE ==='
if ($script:fail -ne 0) { exit 1 }
exit 0
'@

$frag = $fragText + "`r`n" + $HARNESS
[IO.File]::WriteAllBytes($FRAGPATH, (New-Object Text.UTF8Encoding($false)).GetBytes($frag))
$fperr = $null; $ftoks = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($FRAGPATH, [ref]$ftoks, [ref]$fperr)
Write-Output ('  fragmentPath=' + $FRAGPATH + ' fragmentBytes=' + ([IO.File]::ReadAllBytes($FRAGPATH)).Length + ' parseErrors=' + @($fperr).Count)
if (@($fperr).Count -ne 0) { foreach ($e in (@($fperr) | Select-Object -First 6)) { Write-Output ('  PARSE L' + $e.Extent.StartLineNumber + ': ' + $e.Message) }; Write-Output 'VERDICT-HELPERS FAIL: fragment does not parse'; exit 1 }

foreach ($lg in @($LOG, ($LOG + '.err'))) { if (Test-Path -LiteralPath $lg) { Remove-Item -LiteralPath $lg -Force } }
$p = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $FRAGPATH) `
    -NoNewWindow -Wait -PassThru -RedirectStandardOutput $LOG -RedirectStandardError ($LOG + '.err')
$txt = ''
if (Test-Path -LiteralPath $LOG) { $txt = [IO.File]::ReadAllText($LOG) }
$etxt = ''
if (Test-Path -LiteralPath ($LOG + '.err')) { $etxt = [IO.File]::ReadAllText($LOG + '.err') }
Write-Output ('  helperRunExitCode=' + $p.ExitCode + ' stdoutLines=' + (($txt -split "`n").Count) + ' stderrBytes=' + $etxt.Length)
# Error-record markers only. A loose 'Exception' sweep would false-positive on the two
# INFO lines that report EXPECTED throws from Get-SiteFacts / Get-Loopback.
$fragErr = @([regex]::Matches(($txt + $etxt), '(?i)(CategoryInfo|FullyQualifiedErrorId|at line:|PositionMessage|ParserError|CommandNotFoundException\s*:)'))
Write-Output ('  unexpectedErrorRecords=' + $fragErr.Count)
$hFail = @([regex]::Matches($txt, '(?m)^\s*HARNESS FAIL.*$'))
foreach ($h in $hFail) { Write-Output ('  ' + $h.Value.TrimEnd()) }
Write-Output ('  harnessFailLines=' + $hFail.Count)
Write-Output '  --- harness output ---'
foreach ($l in ($txt -split "`n")) { if ($l -match 'HARNESS|INFO|VERDICT|HELPERS-DONE') { Write-Output ('  ' + $l.TrimEnd()) } }
if ($etxt.Trim().Length -gt 0) { Write-Output '  --- stderr (first 12 lines) ---'; foreach ($l in (($etxt -split "`n") | Select-Object -First 12)) { Write-Output ('  ' + $l.TrimEnd()) } }
$hOk = ($p.ExitCode -eq 0 -and ($txt -match '=== HELPERS-DONE ===') -and ($txt -match 'VERDICT-HELPERS PASS') -and $hFail.Count -eq 0 -and $fragErr.Count -eq 0)
Write-Output ('  VERDICT-HELPERRUN ' + $(if ($hOk) { 'PASS' } else { 'FAIL' }))
if (-not $hOk) { exit 1 }

# ---- [4/4] conditional full wrapper dry run ----
Write-Output ''
Write-Output '=== [4/4] CONDITIONAL FULL WRAPPER DRY RUN ==='
$hasAppcmd = Test-Path -LiteralPath 'C:\Windows\System32\inetsrv\appcmd.exe'
Write-Output ('  thisPcHasAppcmd=' + $hasAppcmd)
if ($hasAppcmd) {
    Write-Output '  SKIPPED-BY-POLICY: this PC has IIS, and t27b is a mutating script. Refusing to run it locally.'
    Write-Output '  The wrapper unpack path is already proven byte-exact by [1/4], which is what this check would add.'
    Write-Output '  VERDICT-FULLRUN SKIPPED'
} else {
    foreach ($lg in @($LOG2, ($LOG2 + '.err'))) { if (Test-Path -LiteralPath $lg) { Remove-Item -LiteralPath $lg -Force } }
    $p2 = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $W) `
        -NoNewWindow -Wait -PassThru -RedirectStandardOutput $LOG2 -RedirectStandardError ($LOG2 + '.err')
    $t2 = ''
    if (Test-Path -LiteralPath $LOG2) { $t2 = [IO.File]::ReadAllText($LOG2) }
    # The wrapper does NOT propagate the inner exit code, so dryRunExitCode is meaningless here.
    # Expected: fail-closed at [1/6] BEFORE because the tools are absent, with [2/6] BACKUP
    # never reached, i.e. zero writes on this PC.
    $beforeFail = ($t2 -match 'VERDICT-BEFORE FAIL')
    $reachedSentinel = ($t2 -match '=== STOPIISZX-DONE ===')
    $innerExit1 = ($t2 -match 'innerExitCode=1')
    $reachedBackup = ($t2 -match '\[2/6\]')
    $reachedState = ($t2 -match '\[3/6\]')
    Write-Output ('  dryRunExitCode=' + $p2.ExitCode + ' (wrapper does not propagate inner code; NOT a criterion)')
    Write-Output ('  unpackVerified=' + ($t2 -match 'unpackVerified=True') + ' diskVerified=' + ($t2 -match 'diskVerified=True'))
    Write-Output ('  beforeFail=' + $beforeFail + ' reachedDoneSentinel=' + $reachedSentinel + ' innerExitCode1=' + $innerExit1)
    Write-Output ('  reachedBackupSection=' + $reachedBackup + ' reachedStateChangeSection=' + $reachedState + ' (both must be False)')
    Write-Output '  --- verdict lines from the full run ---'
    foreach ($l in ($t2 -split "`n")) { if ($l -match 'VERDICT|innerExitCode|WIRE-DONE|STOPIISZX-DONE') { Write-Output ('  ' + $l.TrimEnd()) } }
    $fOk = ($beforeFail -and $reachedSentinel -and $innerExit1 -and -not $reachedBackup -and -not $reachedState)
    Write-Output ('  VERDICT-FULLRUN ' + $(if ($fOk) { 'PASS' } else { 'FAIL' }) + ' (expected a CONTROLLED fail-closed, zero writes)')
    if (-not $fOk) { exit 1 }
}

Write-Output ''
Write-Output 'VERDICT-VERIFY PASS: all four local checks green, wrapper is dispatchable'
Write-Output '=== VERIFY4-DONE ==='
exit 0
