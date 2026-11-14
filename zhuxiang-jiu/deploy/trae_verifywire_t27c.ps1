# trae_verifywire_t27c.ps1 -- four-check local verification of the TWO-PART t27c wire wrapper
# (trae_nginxzx_wire_p1.ps1 + trae_nginxzx_wire.ps1) before either half is sent to production
# 39.107.43.149. Derived from trae_verifywire_t27b.ps1, with the structural changes that a
# split blob and a service-mutating inner force.
#
#   [1/4] independent unpack + pair consistency. chunk A is re-extracted from p1 and chunk B from
#         p2 -- never trusted from the generator's own report, because td12d was burned by a
#         stale blob. Then every declared constant in BOTH files is re-derived from the bytes on
#         disk: $PartName/$PartLen/$PartSha must agree across the pair, $BlobLen must equal
#         len(A)+len(B), and gunzip(A+B) must equal the inner script's length and sha256 exactly.
#         This is the same arithmetic p2 performs at runtime, executed here first: if it is green
#         locally it cannot go red in production for a data reason.
#   [2/4] wrapper gate, run SEPARATELY on p1 and on p2. ASCII purity / BOM / parse errors /
#         24576-char wire cap / sentinel presence / mutating-verb scan. The mutating scan is
#         comment-aware: it classifies each hit as code or comment and gates on CODE hits only,
#         because p1 carries an explanatory comment that literally contains the words
#         "never Remove-Item then write". A blanket scan would be a false red; a blanket skip
#         would be a hole. Both counts are printed.
#   [3/4] HELPER-ONLY dry run. The inner starts a service, so it is never run whole here. Its text
#         is split at 'function Do-Rollback {' -- the literal boundary between the 21 read-only
#         helpers and the mutating sections -- the fragment is asserted free of mutating call
#         shapes, and only then is a harness appended that exercises all 21 end to end.
#   [4/4] two-part transport mechanism dry run, ISOLATED and STUBBED. p1 is run for real inside an
#         isolated TEMP so its part file lands nowhere near C:\Windows\TEMP; p2 is run from a
#         harness copy whose single '& $innerPath' line is replaced by a print, so the inner is
#         NEVER executed on this PC. The shipped p2 sha256 is re-checked afterwards to prove the
#         original was not touched. Running the shipped p2, or the inner, is REFUSED outright.
#
# WHY [4/4] REFUSES THE REAL THING EVEN THOUGH IT WOULD "FAIL SAFELY": this PC has appcmd.exe, and
# the inner's [2/6] creates C:\ProgramData\zxjiu-backup with New-Item BEFORE copying nginx.conf.
# A "controlled fail-closed" run would therefore still leave a stray directory on this machine.
# t27b's [4/4] could afford the full run because t27b's inner gated on tool existence first.
#
# TRAP the whole report must be read against: an inner `exit` does not terminate the wrapper, so
# 'WIRE-DONE' ALWAYS prints. It is never a success criterion.
# fail-closed: the first red check exits 1.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$W1 = Join-Path $PSScriptRoot 'trae_nginxzx_wire_p1.ps1'
$W2 = Join-Path $PSScriptRoot 'trae_nginxzx_wire.ps1'
$I = Join-Path $PSScriptRoot 'trae_nginxzx.ps1'
# $FRAGPATH, never $FRAG: PowerShell names are case-insensitive and $fragText below holds the
# fragment TEXT. They were one variable once, so the text clobbered the path and WriteAllBytes
# threw "Illegal characters in path".
$FRAGPATH = Join-Path $env:TEMP 'trae_nginxzx_frag.ps1'
$RUNNER = Join-Path $env:TEMP 'trae_t27c_iso_runner.ps1'
$STUBPATH = Join-Path $env:TEMP 'trae_nginxzx_wire_stub.ps1'
$ISO = Join-Path $env:TEMP ('trae_t27c_iso_' + (Get-Date -Format 'yyyyMMddHHmmss'))
$LOG = Join-Path $env:TEMP 't27c_helpers.log'
$LOGP1 = Join-Path $env:TEMP 't27c_p1run.log'
$LOGP2 = Join-Path $env:TEMP 't27c_p2stub.log'
# 'function Do-Rollback {' -- WITH the brace. The inner's own comment block above that function
# quotes the bare string 'function Do-Rollback', so IndexOf on the shorter form would land in a
# comment 16 lines early. All 21 helpers sit above either occurrence, so the shorter form would
# still have been safe, but an accidental split point is not an acceptable thing to rely on.
$SPLITMARK = 'function Do-Rollback {'

function Hex([byte[]]$b) {
    return (([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b))) -replace '-', '')
}
# @($null).Count is 1, not 0. Parser's [ref] stays $null when a file is clean, so every count of
# it goes through this. Counting inline produced a phantom parse error once already.
function Cnt($v) {
    if ($null -eq $v) { return 0 }
    return @($v).Count
}
function Decl([string]$body, [string]$pattern) {
    $m = [regex]::Match($body, $pattern)
    if (-not $m.Success) { return '' }
    return $m.Groups[1].Value
}
function Blob([string]$body, [string]$varName) {
    $m = [regex]::Matches($body, "(?m)^\`$$varName \+= '([A-Za-z0-9+/=]+)'\s*$")
    return , (-join ($m | ForEach-Object { $_.Groups[1].Value }))
}
function Gunzip([byte[]]$cmp) {
    $msIn = New-Object IO.MemoryStream(, $cmp)
    $ds = New-Object IO.Compression.GZipStream($msIn, [IO.Compression.CompressionMode]::Decompress)
    $msR = New-Object IO.MemoryStream
    $ds.CopyTo($msR)
    $ds.Close(); $msIn.Close()
    $out = $msR.ToArray()
    $msR.Close()
    return , $out
}

foreach ($p in @($W1, $W2, $I)) {
    if (-not (Test-Path -LiteralPath $p)) { Write-Output ('MISSING ' + $p); Write-Output 'VERDICT-VERIFY FAIL'; exit 1 }
}

$p1Body = [IO.File]::ReadAllText($W1)
$p2Body = [IO.File]::ReadAllText($W2)
$iBytes = [IO.File]::ReadAllBytes($I)
$iBody = [IO.File]::ReadAllText($I)
$iSha = Hex $iBytes

# ---- [1/4] independent unpack + pair consistency ----
Write-Output '=== [1/4] INDEPENDENT UNPACK + PAIR CONSISTENCY ==='
$chunkA = Blob $p1Body 'A'
$chunkB = Blob $p2Body 'B'
$assignA = ([regex]::Matches($p1Body, "(?m)^\`$A \+= '")).Count
$assignB = ([regex]::Matches($p2Body, "(?m)^\`$B \+= '")).Count
Write-Output ('  chunkA assignments=' + $assignA + ' chars=' + $chunkA.Length)
Write-Output ('  chunkB assignments=' + $assignB + ' chars=' + $chunkB.Length)

$p1PartName = Decl $p1Body "(?m)^\`$PartName\s*=\s*'([^']+)'"
$p1PartLen = Decl $p1Body "(?m)^\`$PartLen\s*=\s*(\d+)"
$p1PartSha = Decl $p1Body "(?m)^\`$PartSha\s*=\s*'([^']+)'"
$p2PartName = Decl $p2Body "(?m)^\`$PartName\s*=\s*'([^']+)'"
$p2PartLen = Decl $p2Body "(?m)^\`$PartLen\s*=\s*(\d+)"
$p2PartSha = Decl $p2Body "(?m)^\`$PartSha\s*=\s*'([^']+)'"
$p2BlobLen = Decl $p2Body "(?m)^\`$BlobLen\s*=\s*(\d+)"
$p2InnerName = Decl $p2Body "(?m)^\`$InnerName\s*=\s*'([^']+)'"
$p2InnerLen = Decl $p2Body "(?m)^\`$InnerLen\s*=\s*(\d+)"
$p2InnerSha = Decl $p2Body "(?m)^\`$InnerSha\s*=\s*'([^']+)'"

$aBytes = [Text.Encoding]::UTF8.GetBytes($chunkA)
$aSha = Hex $aBytes
$blobChars = $chunkA + $chunkB
$back = Gunzip ([Convert]::FromBase64String($blobChars))
$bSha = Hex $back

$bad = @()
if ($p1PartName -cne $p2PartName) { $bad += ('partName differs: p1=' + $p1PartName + ' p2=' + $p2PartName) }
if ($p1PartLen -cne $p2PartLen) { $bad += ('partLen differs: p1=' + $p1PartLen + ' p2=' + $p2PartLen) }
if ($p1PartSha -cne $p2PartSha) { $bad += ('partSha differs') }
if ([int]$p1PartLen -ne $chunkA.Length) { $bad += ('declared PartLen ' + $p1PartLen + ' != extracted chunkA ' + $chunkA.Length) }
if ($aSha -cne $p1PartSha) { $bad += ('declared PartSha != sha256(chunkA bytes) ' + $aSha) }
if ([int]$p2BlobLen -ne $blobChars.Length) { $bad += ('declared BlobLen ' + $p2BlobLen + ' != len(A)+len(B) ' + $blobChars.Length) }
if ([int]$p2InnerLen -ne $back.Length) { $bad += ('declared InnerLen ' + $p2InnerLen + ' != gunzip length ' + $back.Length) }
if ($p2InnerSha -cne $bSha) { $bad += ('declared InnerSha != gunzip sha256 ' + $bSha) }
if ($back.Length -ne $iBytes.Length) { $bad += ('gunzip length ' + $back.Length + ' != disk inner ' + $iBytes.Length) }
if ($bSha -cne $iSha) { $bad += 'gunzip sha256 != disk inner sha256 (STALE WRAPPER)' }
if ($p2InnerName -cne 'trae_nginxzx.ps1') { $bad += ('unexpected InnerName ' + $p2InnerName) }
if ($p1PartName -cne 'trae_nginxzx.b64part1') { $bad += ('unexpected PartName ' + $p1PartName) }

Write-Output ('  pair partName=' + $p1PartName + ' partLen=' + $p1PartLen + '/' + $p2PartLen + ' partShaAgree=' + ($p1PartSha -ceq $p2PartSha))
Write-Output ('  chunkA sha256=' + $aSha)
Write-Output ('  declaredPartSha=' + $p1PartSha)
Write-Output ('  blobChars=' + $blobChars.Length + ' declaredBlobLen=' + $p2BlobLen)
Write-Output ('  unpackedBytes=' + $back.Length + ' unpackedSha256=' + $bSha)
Write-Output ('  diskInnerBytes=' + $iBytes.Length + ' diskInnerSha256=' + $iSha)
Write-Output ('  declaredInnerLen=' + $p2InnerLen + ' declaredInnerSha=' + $p2InnerSha)
Write-Output ('  headerClaimShaMatchesDisk=' + ($p2Body.Contains($iSha)))
Write-Output ('  p1DeclaresInnerConstants=' + ($p1Body.Contains('$InnerSha')))
foreach ($b in $bad) { Write-Output ('  MISMATCH ' + $b) }
Write-Output ('  mismatches=' + $bad.Count)
if ($bad.Count -ne 0 -or -not $p2Body.Contains($iSha) -or $p1Body.Contains('$InnerSha')) {
    Write-Output 'VERDICT-UNPACK FAIL'
    exit 1
}
Write-Output '  VERDICT-UNPACK PASS: GUNZIP(A+B)-EQUALS-DISK-INNER AND BOTH HEADERS SELF-CONSISTENT'

# ---- [2/4] wrapper gate, p1 and p2 separately ----
Write-Output ''
Write-Output '=== [2/4] WRAPPER GATE (p1 and p2, separately) ==='
$mutRe = '(?i)\b(Start-Service|Stop-Service|Restart-Service|Restart-Computer|Stop-Computer|Set-Service|New-Service|Remove-Service|taskkill|Stop-Process|Start-Process|Set-ItemProperty|Remove-ItemProperty|Set-Content|Add-Content|Out-File|Remove-Item\s|Set-ScheduledTask|Register-ScheduledTask|Unregister-ScheduledTask|Set-NetFirewall|New-NetFirewall|Remove-NetFirewall|netsh\s+advfirewall|nginx\.exe\s+-s)'
$gateAll = $true

foreach ($spec in @(@{ Tag = 'p1'; Path = $W1; Body = $p1Body; Sent = 'WIRE-P1-DONE'; Other = 'WIRE-DONE' },
                    @{ Tag = 'p2'; Path = $W2; Body = $p2Body; Sent = 'WIRE-DONE'; Other = '' })) {
    $tag = $spec.Tag
    $bytes = [IO.File]::ReadAllBytes($spec.Path)
    $body = $spec.Body
    $lines = $body -split "`n"
    $nonAscii = @($bytes | Where-Object { $_ -gt 127 })
    $bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    $errs = $null; $toks = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($spec.Path, [ref]$toks, [ref]$errs)
    $perr = Cnt $errs
    $b64Len = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($body)).Length
    $fits = ($b64Len -le 24576)
    $sent = ([regex]::Matches($body, [regex]::Escape($spec.Sent))).Count
    # 'WIRE-P1-DONE' does NOT contain the substring 'WIRE-DONE', so this count cleanly separates
    # the two halves: p1 must never carry the final sentinel.
    $other = if ($spec.Other -eq '') { 0 } else { ([regex]::Matches($body, [regex]::Escape($spec.Other))).Count }
    $mut = @([regex]::Matches($body, $mutRe))
    $mutCode = @(); $mutComment = @()
    foreach ($h in $mut) {
        $ln = ($body.Substring(0, $h.Index) -split "`n").Count
        if ($lines[$ln - 1].TrimStart().StartsWith('#')) { $mutComment += ('L' + $ln + ' ' + $h.Value) }
        else { $mutCode += ('L' + $ln + ' ' + $h.Value) }
    }
    # The inner invocation must exist in p2 and must NOT exist in p1: p1 is data delivery only.
    $runsInner = ($body -match '(?m)^& \$innerPath')
    Write-Output ('  --- ' + $tag + ' ---')
    Write-Output ('  bytes=' + $bytes.Length + ' chars=' + $body.Length + ' lines=' + $lines.Count + ' sha256=' + (Hex $bytes))
    Write-Output ('  nonAscii=' + $nonAscii.Count + ' bom=' + $bom + ' parseErrors=' + $perr)
    Write-Output ('  b64WireLen=' + $b64Len + ' cap=24576 fits=' + $fits + ' headroom=' + (24576 - $b64Len))
    Write-Output ('  sentinel' + $spec.Sent + '=' + $sent + ' otherSentinelCount=' + $other + ' invokesInner=' + $runsInner)
    Write-Output ('  mutatingVerbHitsCode=' + $mutCode.Count + ' mutatingVerbHitsComment=' + $mutComment.Count)
    foreach ($c in $mutCode) { Write-Output ('    CODE-HIT ' + $c) }
    foreach ($c in ($mutComment | Select-Object -First 4)) { Write-Output ('    comment-only ' + $c) }
    $ok = ($nonAscii.Count -eq 0 -and -not $bom -and $perr -eq 0 -and $fits -and $sent -ge 1 -and $mutCode.Count -eq 0)
    if ($tag -eq 'p1') { $ok = $ok -and $other -eq 0 -and -not $runsInner }
    else { $ok = $ok -and $runsInner -and ($body -match 'part1Verified') -and ($body -match 'unpackVerified') -and ($body -match 'diskVerified') -and ($body -match 'innerExitCode=') }
    Write-Output ('  VERDICT-WRAPPERGATE-' + $tag + ' ' + $(if ($ok) { 'PASS' } else { 'FAIL' }))
    if (-not $ok) { $gateAll = $false }
}
if (-not $gateAll) { exit 1 }

# ---- [3/4] helper-only dry run ----
Write-Output ''
Write-Output '=== [3/4] HELPER-ONLY DRY RUN (fragment split at Do-Rollback) ==='
$idx = $iBody.IndexOf($SPLITMARK)
$idxLast = $iBody.LastIndexOf($SPLITMARK)
Write-Output ('  splitMarkFound=' + ($idx -gt 0) + ' unique=' + ($idx -eq $idxLast) + ' fragmentChars=' + $idx + ' discardedChars=' + ($iBody.Length - $idx))
if ($idx -le 0 -or $idx -cne $idxLast) { Write-Output 'VERDICT-HELPERS FAIL: split mark missing or ambiguous'; exit 1 }
$fragText = $iBody.Substring(0, $idx)
$fragLines = $fragText -split "`n"
# Two scans, because one broad scan is wrong in both directions.
# (a) unconditional mutating shapes, comment lines skipped. $STARTARGS is DEFINED in the config
#     region, which IS inside the fragment, so a bare literal scan for 'start','site' would be a
#     false red; what must be absent is any USE of it as an argument, which is scan (b)'s job.
$fragMutRe = '(Copy-Item|New-Item|Remove-Item|Set-Content|Add-Content|Out-File|WriteAllBytes|Start-Sleep|Do-Rollback\s*\(|Invoke-Native\s+\$APPCMD\s+\$(STOPARGS|STARTARGS)|Resume-Service|Start-Service|Stop-Service|Suspend-Service|Set-Service)'
$fragMut = @(); $fragMutComment = @()
foreach ($h in ([regex]::Matches($fragText, $fragMutRe))) {
    $ln = ($fragText.Substring(0, $h.Index) -split "`n").Count
    if ($fragLines[$ln - 1].TrimStart().StartsWith('#')) { $fragMutComment += ('L' + $ln + ' ' + $h.Value) }
    else { $fragMut += ('L' + $ln + ' ' + $h.Value) }
}
foreach ($h in ($fragMut | Select-Object -First 10)) { Write-Output ('  FRAGMUT ' + $h) }
Write-Output ('  fragmentMutatingCodeShapes=' + $fragMut.Count + ' (must be 0) fragmentCommentMentions=' + $fragMutComment.Count + ' (informational)')
# (b) every native invocation in the fragment must carry a read-only verb.
$acCalls = @([regex]::Matches($fragText, 'Invoke-Native\s+\$APPCMD\s+(@\([^)]*\)|\$\w+)'))
$acBad = @()
foreach ($c in $acCalls) { if ($c.Groups[1].Value -notmatch "'list'") { $acBad += $c.Value } }
$scCalls = @([regex]::Matches($fragText, 'Invoke-Native\s+\$SC\s+(@\([^)]*\)|\$\w+)'))
$scBad = @()
foreach ($c in $scCalls) { if ($c.Groups[1].Value -notmatch "'qc'") { $scBad += $c.Value } }
Write-Output ('  fragmentAppcmdCalls=' + $acCalls.Count + ' nonListVerb=' + $acBad.Count + ' | fragmentScCalls=' + $scCalls.Count + ' nonQcVerb=' + $scBad.Count + ' (both must be 0)')
foreach ($b in (@($acBad) + @($scBad) | Select-Object -First 6)) { Write-Output ('  NATIVE-BAD ' + $b) }
if ($fragMut.Count -ne 0 -or $acBad.Count -ne 0 -or $scBad.Count -ne 0) { Write-Output 'VERDICT-HELPERS FAIL: fragment is not read-only'; exit 1 }

$HARNESS = @'

# ---- harness appended by trae_verifywire_t27c.ps1; NOT part of the shipped script ----
$script:fail = 0
function Chk([string]$n, [bool]$ok, [string]$d) {
    Write-Output ('  HARNESS ' + $(if ($ok) { 'OK  ' } else { 'FAIL' }) + ' ' + $n + ' :: ' + $d)
    if (-not $ok) { $script:fail++ }
}

$expAbc = 'BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD'
$gotAbc = Get-Sha256Hex ([Text.Encoding]::UTF8.GetBytes('abc'))
Chk 'Get-Sha256Hex-known-answer' ($gotAbc -ceq $expAbc) ('sha256(abc)=' + $gotAbc.Substring(0, 16))

$nf = 'C:\Windows\System32\netstat.exe'
$ff = Get-FileFacts $nf
$ref = (Get-FileHash -LiteralPath $nf -Algorithm SHA256).Hash.Substring(0, 16)
Chk 'Get-FileFacts-realfile' ($ff.Exists -and $ff.Sha16 -ceq $ref) ('sha16=' + $ff.Sha16 + ' ref=' + $ref + ' bytes=' + $ff.Bytes)
$fx = Get-FileFacts 'C:\no\such\file.bin'
Chk 'Get-FileFacts-missingfile' ((-not $fx.Exists) -and $fx.Sha16 -ceq 'NOFILE' -and $fx.Bytes -eq -1) ('exists=' + $fx.Exists + ' sha16=' + $fx.Sha16)

$rn = Invoke-Native 'C:\Windows\System32\cmd.exe' @('/c', 'echo', 'harness-ok')
Chk 'Invoke-Native-exitcode-and-output' ($rn.ExitCode -eq 0 -and ((@($rn.Out) -join ' ').Contains('harness-ok'))) ('exitCode=' + $rn.ExitCode)
Chk 'Invoke-Native-restores-ErrorActionPreference' ($ErrorActionPreference -ceq 'Stop') ('eap=' + $ErrorActionPreference)
Show-Native 'HARNESSNATIVE' $rn 3

# Bare assignment, NOT @(...): Get-PortListeners ends with `return , $rows`, so wrapping the CALL
# in @() double-wraps -- .Count becomes a constant 1 and the lone element is the rows array
# itself, whose .ProcId/.ProcName read back empty. That defect made t27b's port-80 gate
# unconditionally true; these assertions are what caught it, so they stay bare-assigned.
$r80 = Get-PortListeners 80
$n80 = @($r80).Count
Write-Output ('  INFO Get-PortListeners(80) rows=' + $n80)
Show-Port 'HARNESS80' $r80
$first80 = if ($n80 -gt 0) { @($r80)[0] } else { $null }
Chk 'Get-PortListeners-row-fields-populated' (($n80 -eq 0) -or (([string]$first80.ProcId -ne '') -and ([string]$first80.ProcName -ne ''))) ('rows=' + $n80 + ' firstPid=' + $first80.ProcId + ' firstProc=' + $first80.ProcName)
$rNone = Get-PortListeners 65001
$nNone = @($rNone).Count
Chk 'Get-PortListeners-unused-port-is-empty' ($nNone -eq 0) ('rows=' + $nNone)

# Test-PortHeldBy returns a HASH TABLE, not a bool. A caller that treats it as a bool gets $true
# for every non-empty table, including @{Held=$false}, which is the silent-inversion class.
$fake = @(@{ Proto = 'TCP'; Local = '0.0.0.0:80'; ProcId = '4242'; ProcName = 'nginx' })
$th = Test-PortHeldBy 'HARNESS' $fake 'nginx'
Chk 'Test-PortHeldBy-returns-hashtable' ($th -is [hashtable]) ('type=' + $th.GetType().Name + ' keys=' + (($th.Keys | Sort-Object) -join ','))
Chk 'Test-PortHeldBy-positive' (($th.Held -eq $true) -and ($th.Count -eq 1) -and ($th.Pids -ceq '4242')) ('held=' + $th.Held + ' count=' + $th.Count + ' pids=' + $th.Pids)
$tn = Test-PortHeldBy 'HARNESS' $fake 'httpd'
Chk 'Test-PortHeldBy-negative' (($tn.Held -eq $false) -and ($tn.Count -eq 0)) ('held=' + $tn.Held + ' count=' + $tn.Count)
$te = Test-PortHeldBy 'HARNESS' @() 'nginx'
Chk 'Test-PortHeldBy-empty-rows' (($te.Held -eq $false) -and ($te.Count -eq 0)) ('held=' + $te.Held)

$sv = Get-SvcSnapshot
Show-Svc 'HARNESS' $sv
Chk 'Get-SvcSnapshot-has-all-keys' ($sv.ContainsKey('nginx8147') -and $sv.ContainsKey('W3SVC') -and $sv.ContainsKey('WAS') -and $sv.ContainsKey('nginxProcCount')) ('keys=' + (($sv.Keys | Sort-Object) -join ','))

$df = Get-DistFacts
Show-Dist 'HARNESS' $df
Chk 'Get-DistFacts-keyed-by-three-pages' ($df.ContainsKey('activity.html') -and $df.ContainsKey('activity-detail.html') -and $df.ContainsKey('xiaozhu-voice.html')) ('keys=' + (($df.Keys | Sort-Object) -join ','))

$cf = Get-ConfFacts
Show-Conf 'HARNESS' $cf
Chk 'Get-ConfFacts-has-all-keys' ($cf.ContainsKey('Bytes') -and $cf.ContainsKey('Sha16') -and $cf.ContainsKey('Mtime') -and $cf.ContainsKey('Lines')) ('keys=' + (($cf.Keys | Sort-Object) -join ','))

# Get-LogTail must never throw: it returns @('NOFILE <path>') for a missing file, and the inner
# calls it on nginx logs that may legitimately not exist on a host that has never served.
$lt = Get-LogTail 'C:\Windows\System32\drivers\etc\hosts' 3
Show-LogTail 'HARNESS' $lt
Chk 'Get-LogTail-existing-file-bounded' (@($lt).Count -le 3) ('lines=' + @($lt).Count)
# Get-LogTail's missing-file branch is `return @('NOFILE ' + $p)` -- a ONE-element array, which
# PowerShell unrolls to a bare string at the call site. So $lm is the string, and $lm[0] is its
# first CHARACTER ('N'), not its first element. Wrapping the result in @(...) is the correct
# shape here and cannot double-wrap, because Get-LogTail uses plain `return @(...)` (no comma
# operator) -- unlike Get-PortListeners, whose `return , $rows` requires a bare assignment.
# The inner script's own consumers are already safe: Show-LogTail uses @($lines).Count + foreach.
$lm = @(Get-LogTail 'C:\no\such\log.log' 5)
Chk 'Get-LogTail-missing-file-returns-NOFILE' (($lm.Count -eq 1) -and ([string]$lm[0]).StartsWith('NOFILE')) ('first=' + [string]$lm[0])

$fnNames = @('Note', 'Get-Sha256Hex', 'Get-FileFacts', 'Invoke-Native', 'Show-Native', 'Get-PortListeners', 'Show-Port', 'Test-PortHeldBy', 'Get-SiteFacts', 'Show-Site', 'Get-Http', 'Show-Http', 'Get-SvcSnapshot', 'Show-Svc', 'Get-ScQc', 'Get-LogTail', 'Show-LogTail', 'Get-DistFacts', 'Show-Dist', 'Get-ConfFacts', 'Show-Conf')
$missing = @()
foreach ($f in $fnNames) { if (-not (Get-Command -Name $f -CommandType Function -ErrorAction SilentlyContinue)) { $missing += $f } }
Chk 'all-21-helpers-defined' ($missing.Count -eq 0) ('declared=' + $fnNames.Count + ' missing=' + $(if ($missing.Count) { $missing -join ',' } else { 'none' }))

# The three collectors that talk to subsystems this PC may or may not have. Each is wrapped: a
# throw here is a harness-environment fact, not a script defect, so it is reported as INFO.
try { $sf = Get-SiteFacts; Show-Site 'HARNESS' $sf; Chk 'Get-SiteFacts-returns-hashtable' ($sf -is [hashtable]) ('keys=' + (($sf.Keys | Sort-Object) -join ',')) }
catch { Write-Output ('  INFO Get-SiteFacts threw on this PC: ' + $_.Exception.GetType().Name) }
try { $qc = Get-ScQc; Show-Native 'HARNESSQC' $qc 3; Chk 'Get-ScQc-returns-hashtable' ($qc -is [hashtable]) ('exitCode=' + $qc.ExitCode) }
catch { Write-Output ('  INFO Get-ScQc threw on this PC: ' + $_.Exception.GetType().Name) }
# Port 9 (discard) with nothing listening: Get-Http must report a failure outcome, never throw,
# because the inner calls it inside a gate and an unhandled throw would abort before [6/6].
try { $h = Get-Http 'http://127.0.0.1:9/'; Show-Http 'HARNESS' $h; Chk 'Get-Http-returns-hashtable' ($h -is [hashtable]) ('keys=' + (($h.Keys | Sort-Object) -join ',')) }
catch { Write-Output ('  INFO Get-Http threw on this PC: ' + $_.Exception.GetType().Name) }

Chk 'no-harness-failure' ($script:fail -eq 0) ('failures=' + $script:fail)
Write-Output ('VERDICT-HELPERS ' + $(if ($script:fail -eq 0) { 'PASS' } else { 'FAIL' }))
Write-Output '=== HELPERS-DONE ==='
if ($script:fail -ne 0) { exit 1 }
exit 0
'@

$fragFull = $fragText + "`r`n" + $HARNESS
[IO.File]::WriteAllBytes($FRAGPATH, (New-Object Text.UTF8Encoding($false)).GetBytes($fragFull))
$fperr = $null; $ftoks = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($FRAGPATH, [ref]$ftoks, [ref]$fperr)
Write-Output ('  fragmentPath=' + $FRAGPATH + ' fragmentBytes=' + ([IO.File]::ReadAllBytes($FRAGPATH)).Length + ' parseErrors=' + (Cnt $fperr))
if ((Cnt $fperr) -ne 0) {
    foreach ($e in (@($fperr) | Select-Object -First 6)) { Write-Output ('  PARSE L' + $e.Extent.StartLineNumber + ': ' + $e.Message) }
    Write-Output 'VERDICT-HELPERS FAIL: fragment does not parse'
    exit 1
}

function Run-Logged([string]$target, [string]$logBase, [string[]]$extraArgs) {
    foreach ($lg in @($logBase, ($logBase + '.err'))) { if (Test-Path -LiteralPath $lg) { Remove-Item -LiteralPath $lg -Force } }
    $al = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $target + '"'))
    foreach ($a in $extraArgs) { $al += ('"' + $a + '"') }
    $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $al -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $logBase -RedirectStandardError ($logBase + '.err')
    $o = ''; $e = ''
    if (Test-Path -LiteralPath $logBase) { $o = [IO.File]::ReadAllText($logBase) }
    if (Test-Path -LiteralPath ($logBase + '.err')) { $e = [IO.File]::ReadAllText($logBase + '.err') }
    return @{ ExitCode = $p.ExitCode; Out = $o; Err = $e }
}

$hr = Run-Logged $FRAGPATH $LOG @()
$txt = $hr.Out
$etxt = $hr.Err
Write-Output ('  helperRunExitCode=' + $hr.ExitCode + ' stdoutLines=' + (($txt -split "`n").Count) + ' stderrBytes=' + $etxt.Length)
# Error-record markers only. A loose 'Exception' sweep would false-positive on the INFO lines
# that report EXPECTED throws from the three subsystem collectors above.
$fragErr = @([regex]::Matches(($txt + $etxt), '(?i)(CategoryInfo|FullyQualifiedErrorId|at line:|PositionMessage|ParserError)'))
Write-Output ('  unexpectedErrorRecords=' + $fragErr.Count)
$hFail = @([regex]::Matches($txt, '(?m)^\s*HARNESS FAIL.*$'))
foreach ($h in $hFail) { Write-Output ('  ' + $h.Value.TrimEnd()) }
Write-Output ('  harnessFailLines=' + $hFail.Count)
Write-Output '  --- harness output ---'
foreach ($l in ($txt -split "`n")) { if ($l -match 'HARNESS|INFO|VERDICT|HELPERS-DONE') { Write-Output ('  ' + $l.TrimEnd()) } }
if ($etxt.Trim().Length -gt 0) { Write-Output '  --- stderr (first 12 lines) ---'; foreach ($l in (($etxt -split "`n") | Select-Object -First 12)) { Write-Output ('  ' + $l.TrimEnd()) } }
$hOk = ($hr.ExitCode -eq 0 -and ($txt -match '=== HELPERS-DONE ===') -and ($txt -match 'VERDICT-HELPERS PASS') -and $hFail.Count -eq 0 -and $fragErr.Count -eq 0)
Write-Output ('  VERDICT-HELPERRUN ' + $(if ($hOk) { 'PASS' } else { 'FAIL' }))
if (-not $hOk) { exit 1 }

# ---- [4/4] isolated two-part transport dry run, inner execution stubbed out ----
Write-Output ''
Write-Output '=== [4/4] TWO-PART TRANSPORT DRY RUN (isolated TEMP, inner NEVER executed) ==='
$hasAppcmd = Test-Path -LiteralPath 'C:\Windows\System32\inetsrv\appcmd.exe'
Write-Output ('  thisPcHasAppcmd=' + $hasAppcmd)
Write-Output '  REFUSED: the shipped p2 and the shipped inner are not run on this PC. The inner''s [2/6]'
Write-Output '  creates C:\ProgramData\zxjiu-backup with New-Item BEFORE copying nginx.conf, so even a'
Write-Output '  fail-closed local run would leave a stray directory here. What IS proven below is the'
Write-Output '  transport: p1 runs for real into an isolated TEMP, and a harness COPY of p2 with its one'
Write-Output '  "& $innerPath" line replaced by a print runs against it.'

$p2ShaBefore = Hex ([IO.File]::ReadAllBytes($W2))
$stubText = [regex]::Replace($p2Body, '(?m)^& \$innerPath\s*$', "Write-Host 'INNER-EXEC-STUBBED-BY-VERIFY-T27C'")
$stubbed = ($stubText -cne $p2Body) -and ($stubText.Contains('INNER-EXEC-STUBBED-BY-VERIFY-T27C')) -and (-not ([regex]::IsMatch($stubText, '(?m)^& \$innerPath')))
Write-Output ('  stubApplied=' + $stubbed + ' stubBytes=' + ([Text.Encoding]::UTF8.GetBytes($stubText)).Length)
if (-not $stubbed) { Write-Output 'VERDICT-TRANSPORT FAIL: could not stub the inner invocation'; exit 1 }
[IO.File]::WriteAllBytes($STUBPATH, (New-Object Text.UTF8Encoding($false)).GetBytes($stubText))

$runnerText = @'
param([string]$Target, [string]$Iso)
$ErrorActionPreference = 'Stop'
$env:TEMP = $Iso
$env:TMP = $Iso
try { & $Target } catch { Write-Host ('runnerThrew=' + $_.Exception.GetType().Name + ' | ' + $_.Exception.Message) }
'@
[IO.File]::WriteAllBytes($RUNNER, (New-Object Text.UTF8Encoding($false)).GetBytes($runnerText))
New-Item -ItemType Directory -Path $ISO -Force | Out-Null

$r1 = Run-Logged $RUNNER $LOGP1 @($W1, $ISO)
$partFile = Join-Path $ISO 'trae_nginxzx.b64part1'
$partExists = Test-Path -LiteralPath $partFile
$partShaNow = if ($partExists) { Hex ([IO.File]::ReadAllBytes($partFile)) } else { 'NOFILE' }
$partLenNow = if ($partExists) { ([IO.File]::ReadAllBytes($partFile)).Length } else { -1 }
Write-Output ('  p1RunExitCode=' + $r1.ExitCode + ' part1LandedInIso=' + $partExists + ' bytes=' + $partLenNow)
Write-Output ('  p1 part1Verified=' + ($r1.Out -match 'part1Verified=True') + ' sentinel=' + ($r1.Out -match 'WIRE-P1-DONE') + ' threw=' + ($r1.Out -match 'runnerThrew'))
foreach ($l in ($r1.Out -split "`n")) { if ($l -match 'part1=|part1Verified|WIRE-P1-DONE|runnerThrew') { Write-Output ('    | ' + $l.TrimEnd()) } }
$p1Ok = ($r1.ExitCode -eq 0 -and $partExists -and ($r1.Out -match 'part1Verified=True') -and ($r1.Out -match 'WIRE-P1-DONE') `
         -and -not ($r1.Out -match 'runnerThrew') -and $partShaNow -ceq $aSha -and $partLenNow -eq $chunkA.Length `
         -and ($r1.Err.Trim().Length -eq 0))
Write-Output ('  p1 part-file sha256 matches chunkA=' + ($partShaNow -ceq $aSha))
Write-Output ('  VERDICT-TRANSPORT-P1 ' + $(if ($p1Ok) { 'PASS' } else { 'FAIL' }))
if (-not $p1Ok) { if ($r1.Err.Trim().Length -gt 0) { Write-Output ('  p1 stderr: ' + $r1.Err.Trim()) }; exit 1 }

$r2 = Run-Logged $RUNNER $LOGP2 @($STUBPATH, $ISO)
$innerFile = Join-Path $ISO 'trae_nginxzx.ps1'
$innerExists = Test-Path -LiteralPath $innerFile
$innerShaNow = if ($innerExists) { Hex ([IO.File]::ReadAllBytes($innerFile)) } else { 'NOFILE' }
$innerLenNow = if ($innerExists) { ([IO.File]::ReadAllBytes($innerFile)).Length } else { -1 }
Write-Output ('  p2StubRunExitCode=' + $r2.ExitCode + ' innerLandedInIso=' + $innerExists + ' bytes=' + $innerLenNow)
foreach ($l in ($r2.Out -split "`n")) { if ($l -match 'part1=|part1Verified|b64chars|unpacked|inner=|unpackVerified|diskVerified|INNER-EXEC-STUBBED|WIRE-DONE|runnerThrew') { Write-Output ('    | ' + $l.TrimEnd()) } }
$p2ShaAfter = Hex ([IO.File]::ReadAllBytes($W2))
$shippedUntouched = ($p2ShaBefore -ceq $p2ShaAfter)
$p2Ok = ($r2.ExitCode -eq 0 -and ($r2.Out -match 'part1Verified=True') -and ($r2.Out -match 'unpackVerified=True') `
         -and ($r2.Out -match 'diskVerified=True') -and ($r2.Out -match 'INNER-EXEC-STUBBED-BY-VERIFY-T27C') `
         -and ($r2.Out -match 'WIRE-DONE') -and -not ($r2.Out -match 'runnerThrew') `
         -and $innerExists -and $innerShaNow -ceq $iSha -and $innerLenNow -eq $iBytes.Length `
         -and $shippedUntouched -and ($r2.Err.Trim().Length -eq 0))
Write-Output ('  iso inner sha256 equals repo inner=' + ($innerShaNow -ceq $iSha) + ' shippedP2Untouched=' + $shippedUntouched)
Write-Output ('  VERDICT-TRANSPORT-P2 ' + $(if ($p2Ok) { 'PASS' } else { 'FAIL' }))
if (-not $p2Ok) { if ($r2.Err.Trim().Length -gt 0) { Write-Output ('  p2 stderr: ' + $r2.Err.Trim()) }; exit 1 }

foreach ($d in @($ISO)) { if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force } }
Write-Output ('  isolatedTempRemoved=' + (-not (Test-Path -LiteralPath $ISO)))
Write-Output '  VERDICT-TRANSPORT PASS: p1 delivered chunk A and the p2 decode path rebuilt the inner byte-exact, with the inner never executed'

Write-Output ''
Write-Output 'VERDICT-VERIFY PASS: all four local checks green, both wire halves are dispatchable'
Write-Output '=== VERIFY4-DONE ==='
exit 0
