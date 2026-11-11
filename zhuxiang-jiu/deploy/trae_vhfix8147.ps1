# Deploy the patched voice_health_agent.py onto 8.147.66.237 (Server Core, no desktop).
# Sentinel: VHFIX8147-DONE
#
# WHY. td12 proved a 9:9 alignment between voice-health.log "[critical] tts=http=503 bytes=75"
# lines and System 7036 stop->run pairs for backend8147 on 2026-11-11: the TTS vendor returned
# 503 (business code 1113, out of credit) and the watchdog answered by restarting the
# production backend nine times in one day, every 60-70 minutes. Restarting backend8147 cannot
# fix an upstream billing fault, so each restart only cut in-flight requests. This is the fix
# the user authorized ("???" / change the criterion).
#
# WHAT CHANGED IN THE .py (10327 -> 12037 bytes, LF-only, no BOM, sha below):
#   - new UPSTREAM_HTTP_STATUS {502,503,504} and UPSTREAM_BODY_RE ("code":1113 with a digit
#     boundary, or insufficient / arrear). ASCII markers only, never CJK literals.
#   - probe_tts returns a 4th element `upstream`; its except branch returns False, because a
#     refused connection or timeout means the backend itself is unreachable and IS restartable.
#   - main() splits the restart criterion from the reported health:
#       restart_core_ok = ok_svc and (tts_ok or tts_upstream) and ws_ok
#     so an upstream fault no longer feeds consec_fail, and actions records
#     "tts-upstream-fault(no-restart)". `status` is still computed from core_ok, so the alert
#     signal (critical + exit 2 + red panel + task lastResult) is fully preserved.
#   - 500 is deliberately NOT in the upstream set: a 500 can be the local zombie
#     connection-pool state that a restart does cure (recorded in the module docstring).
#   - the ws_wake criterion is untouched: the 2026-11-12 03:18:23 "TimeoutError opening
#     handshake" was a local transport fault, i.e. restartable.
#
# FAIL-CLOSED ORDER (security-hard-coded 2.3 fail-closed / 2.4 back up before writing): every
# gate below aborts BEFORE a byte is written, and a py_compile failure restores the backup and
# says so. Nothing here claims a pass it did not measure.
$ErrorActionPreference = 'Stop'
$Sentinel = 'VHFIX8147-DONE'
$Target   = 'C:\backend\scripts\voice_health_agent.py'
$OldSha   = 'DC50E99F3E6997700D2AA3FFF920EB77F99BC22DBE928551E0CEDEAF6E52A107'
$NewSha   = '77276416877A719FC6FB4E93BC46AC1F54AB0151A928FF0F5523726E13362136'
$NewLen   = 12037
$Py       = 'C:\Python312\python.exe'

function ShaOf([byte[]]$x) {
    return (([BitConverter]::ToString(
        [Security.Cryptography.SHA256]::Create().ComputeHash($x))) -replace '-', '')
}
function Esc([string]$s) {
    # keep output pure ASCII: zh-CN bodies become \uXXXX, never mojibake
    $sb = New-Object Text.StringBuilder
    foreach ($ch in $s.ToCharArray()) {
        if ([int]$ch -gt 126) { [void]$sb.Append('\u' + ([int]$ch).ToString('x4')) }
        else { [void]$sb.Append($ch) }
    }
    return $sb.ToString()
}

Write-Output ('localNow=' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Output ('host=' + $env:COMPUTERNAME)

# ---- [1/7] precheck: disk must still hold the exact revision we diffed against
Write-Output '[1/7] precheck'
if (-not (Test-Path -LiteralPath $Target)) {
    Write-Output ('ABORT target-missing=' + $Target); Write-Output $Sentinel; exit 1
}
$cur = [IO.File]::ReadAllBytes($Target)
$curSha = ShaOf $cur
Write-Output ('  diskBytes=' + $cur.Length + ' diskSha=' + $curSha)
if ($curSha -ne $OldSha) {
    Write-Output 'ABORT disk-sha-mismatch: target is not the audited revision, nothing written'
    Write-Output $Sentinel; exit 1
}
if (-not (Test-Path -LiteralPath $Py)) {
    Write-Output ('ABORT python-missing=' + $Py); Write-Output $Sentinel; exit 1
}

# ---- [2/7] unpack the embedded payload and verify it before it touches disk
Write-Output '[2/7] unpack'
$b64 = ''
$b64 += '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'
$b64 += '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'
$b64 += 'aw2bO965p3Iuy/6FJnR49Mp7CIZ8GJ8n1vgba3rdXrxnjY+x01X5jZHyOQOmpIZvArAC6UWWLi+9pCA4Y3qr1PmNGax8X3rkHCPa/B2yycLvW5EGx7R4rMxXnaOYvO49E+CKL1fKG57a8tTFy8e9viLewne55u1UyRkSTpU85IzSGg6HTncR++IVbP5uRLAvOkX4P0fOOwXT/NZ04erzMjaF1RvkwwTAyQbfuW/NvrFWp+xr66yU7tbOXWZtiCnvgApB0+sAQg3KTl2t7Jvuq3zWe1dSRwcNuJ0C7JU83QlSjYSK/9ToWBFomD1cDGvYMZoGUXNlsZ4y6Efb4at4w2SbfO8zPwkLAuz1KpE4KdFomYBwWemCN0VXMdOg3RCncFVk4OtH/jpKdjkfVF+xQbOuSmaoKl+TRoeIajIWHLhWx97q5KiufWMod1m92pBcX7rgKL8YAZahBoazu8w7554rqOicUakCn3PAgsX6qoMGDuG4LycIjwyGy+EaOQLL+KxGa/aWOS7361IcGNzbhz2K6YqpxKQk575BpAkEkiTgG6mZAd32oQ70CV7URhuO8S08d1MvzoEZbjlX9X08y4SCG9swY2YzlcbTdZXjcUHcaIr0hNAwz9EjcUF2vsxp7j2G4dkCsJl+6gRGo2BafyjDHcnZx/Ie9dhDjEcJrifrvGjpRSaJZhlETnXVNPb6ITsV2E+IRHuFHKnUORIQHKt7OMGyQS+whGjWjWdVMpwViGadi1x52WmyHabJH7MyrBeXjYMZSwuk33i8ySmb0BqiKGIyLopOEREPYcE2xuRZii74/h87UjwjBS8AAA=='
$raw = [Convert]::FromBase64String($b64)
$msIn = New-Object IO.MemoryStream(, $raw)
$gzd = New-Object IO.Compression.GZipStream($msIn, [IO.Compression.CompressionMode]::Decompress)
$msOut = New-Object IO.MemoryStream
$gzd.CopyTo($msOut); $gzd.Close()
$new = $msOut.ToArray()
$newShaGot = ShaOf $new
Write-Output ('  b64Len=' + $b64.Length + ' gzLen=' + $raw.Length + ' newBytes=' + $new.Length)
Write-Output ('  newSha=' + $newShaGot)
if ($newShaGot -ne $NewSha -or $new.Length -ne $NewLen) {
    Write-Output 'ABORT payload-sha-mismatch: nothing written'; Write-Output $Sentinel; exit 1
}

# ---- [3/7] backup first, then verify the backup by reading it back
Write-Output '[3/7] backup'
$ts = Get-Date -Format 'yyyyMMddHHmmss'
$bak = $Target + '.bak-' + $ts
Copy-Item -LiteralPath $Target -Destination $bak -Force
$bakSha = ShaOf ([IO.File]::ReadAllBytes($bak))
Write-Output ('  backup=' + $bak + ' bytes=' + (Get-Item -LiteralPath $bak).Length + ' sha=' + $bakSha)
if ($bakSha -ne $OldSha) {
    Write-Output 'ABORT backup-unverified: refusing to overwrite a file we cannot restore'
    Write-Output $Sentinel; exit 1
}

# ---- [4/7] write
Write-Output '[4/7] write'
[IO.File]::WriteAllBytes($Target, $new)
Write-Output '  written'

# ---- [5/7] read back and verify content plus shape (LF only, no BOM, trailing newline)
Write-Output '[5/7] verify'
$rb = [IO.File]::ReadAllBytes($Target)
$rbSha = ShaOf $rb
$crlf = 0
for ($i = 0; $i -lt $rb.Length - 1; $i++) { if ($rb[$i] -eq 13 -and $rb[$i + 1] -eq 10) { $crlf++ } }
$lf = 0
foreach ($x in $rb) { if ($x -eq 10) { $lf++ } }
$bom = ($rb.Length -ge 3 -and $rb[0] -eq 0xEF -and $rb[1] -eq 0xBB -and $rb[2] -eq 0xBF)
Write-Output ('  bytes=' + $rb.Length + ' sha=' + $rbSha + ' bom=' + $bom + ' crlf=' + $crlf + ' bareLf=' + ($lf - $crlf) + ' lastByte=' + $rb[$rb.Length - 1])
$shapeOk = ($rbSha -eq $NewSha) -and ($rb.Length -eq $NewLen) -and (-not $bom) -and ($crlf -eq 0) -and ($rb[$rb.Length - 1] -eq 10)
if (-not $shapeOk) {
    Write-Output 'SHAPE-FAIL restoring backup'
    Copy-Item -LiteralPath $bak -Destination $Target -Force
    Write-Output ('  restoredSha=' + (ShaOf ([IO.File]::ReadAllBytes($Target))))
    Write-Output 'ROLLED-BACK'; Write-Output $Sentinel; exit 1
}

# ---- [6/7] syntax gate with the very interpreter nssm launches; compile to TEMP so the
#            deploy dir gains no __pycache__
Write-Output '[6/7] py_compile'
$tmpPyc = Join-Path $env:TEMP ('vhfix-' + $ts + '.pyc')
& $Py -c 'import py_compile,sys; py_compile.compile(sys.argv[1],cfile=sys.argv[2],doraise=True)' $Target $tmpPyc 2>&1 | ForEach-Object { Write-Output ('  py| ' + (Esc ([string]$_))) }
$pyExit = $LASTEXITCODE
Write-Output ('  pyExit=' + $pyExit)
if (Test-Path -LiteralPath $tmpPyc) { Remove-Item -LiteralPath $tmpPyc -Force }
if ($pyExit -ne 0) {
    Write-Output 'PYCOMPILE-FAIL restoring backup'
    Copy-Item -LiteralPath $bak -Destination $Target -Force
    $rsha = ShaOf ([IO.File]::ReadAllBytes($Target))
    Write-Output ('  restoredSha=' + $rsha + ' matchesOld=' + ($rsha -eq $OldSha))
    Write-Output 'ROLLED-BACK'; Write-Output $Sentinel; exit 1
}

# ---- [7/7] read-only post snapshot: the 10-minute task must still be armed
Write-Output '[7/7] post-snapshot (read-only)'
try {
    $tk = Get-ScheduledTask -TaskName 'voice-health-agent' -ErrorAction Stop
    $ti = Get-ScheduledTaskInfo -InputObject $tk
    Write-Output ('  taskState=' + $tk.State + ' nextRun=' + $ti.NextRunTime + ' lastResult=' + $ti.LastTaskResult + ' missed=' + $ti.NumberOfMissedRuns)
} catch { Write-Output ('  taskQuery=UNKNOWN ' + (Esc $_.Exception.Message)) }
try {
    $svc = Get-CimInstance Win32_Service -Filter "Name='backend8147'" -ErrorAction Stop
    Write-Output ('  backend8147=' + $svc.State + ' startMode=' + $svc.StartMode + ' pid=' + $svc.ProcessId)
} catch { Write-Output ('  svcQuery=UNKNOWN ' + (Esc $_.Exception.Message)) }
try {
    $rp = Get-Content -LiteralPath 'C:\backend\logs\voice-health.json' -Raw -ErrorAction Stop
    if ($rp.Length -gt 900) { $rp = $rp.Substring(0, 900) + '...TRUNC' }
    Write-Output ('  report=' + (Esc $rp))
} catch { Write-Output ('  reportQuery=UNKNOWN ' + (Esc $_.Exception.Message)) }

Write-Output ('DEPLOYED sha=' + $rbSha + ' bytes=' + $rb.Length + ' backup=' + $bak)
Write-Output $Sentinel
exit 0