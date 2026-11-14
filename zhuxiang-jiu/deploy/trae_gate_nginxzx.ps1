# trae_gate_nginxzx.ps1 -- pre-wire gate for t27c, the MUTATING script that brings the nginx8147
# service up on the zxjiu.com host so it takes over TCP/80 after t27b stopped the IIS site.
#
# HOW THIS DIFFERS FROM trae_gate_stopiiszx.ps1 (t27b's gate). Every difference below is forced by
# a measurement of trae_nginxzx.ps1, not by taste; carrying t27b's lists over unchanged would have
# produced false reds on exactly the things t27c is authorized to do.
#
# 1. Resume-Service / Stop-Service / Start-Service are REQUIRED here, not forbidden.
#    The user's authorization for step 3 is literally "resume/start nginx8147". Measured in the
#    target: Resume-Service at L603, Stop-Service at L395 (inside Do-Rollback) and L630,
#    Start-Service at L637. t27b vetoed these verbs because t27b's scope was the IIS site only.
#    Restart-Service stays forbidden -- measured zero hits, and the runbook recovery is an explicit
#    stop-then-start, not a restart verb this script never needed.
#
# 2. COMMENT LINES ARE SKIPPED by the forbidden scan. Measured: the words nssm, Set-Service,
#    Resume-Service and Suspend-Service appear in 11 lines of prose (header L8/L11/L16/L37/L39,
#    Do-Rollback L386, escalation block L609/L623-L627) whose whole purpose is to explain WHY those
#    verbs are not used. Scanning them would red-line the script's own scope justification. Every
#    token below was verified to have ZERO hits on non-comment lines, so skipping comments costs no
#    coverage -- it only removes false reds.
#
# 3. The forbidden token is 'nssm.exe', never bare 'nssm'. Section [6/6] prints a self-attestation
#    string ("nssmUntouched=no nssm verb exists in this file") on a CODE line, so bare 'nssm' would
#    hit the one line that exists to prove nssm was never touched. Forbidding the invocation form is
#    what actually pins behaviour. Related: nssm.exe's presence on THIS host was never measured
#    (that fact belongs to the unrelated 8.147.66.237 machine), so invoking it would be a fail-open
#    guess -- which is why the target deliberately does not.
#
# 4. 'applicationHost.config' is NOT a required token here (it was for t27b): measured zero hits in
#    trae_nginxzx.ps1, which backs up nginx.conf instead. The required token for the rollback's IIS
#    side is the appcmd argument shape "'start', 'site'" -- note the space after each comma, which is
#    how L74 literally spells it. t27b's tighter "'start','site'" would have been a false red.
#
# 5. Sentinel and exit counts are 9 each, measured strictly 1:1 -- sentinels at L452/L479/L557/L590/
#    L713/L762/L816/L828 (the eight fail paths) and L834 (pass), against exits at L453/L480/L558/
#    L591/L714/L763/L817/L829/L835. The exit regex is anchored on [ \t] rather than \s because \s
#    also matches \r, which lets a pattern swallow the line ending and match across lines.
#
# $Target resolves through $PSScriptRoot so this file stays 100% ASCII; the repo root contains
# Chinese characters and spelling it out would trip this gate's own ASCII guard.
# fail-closed (security-hardcoded 2.3): an unmeasurable or missing target exits 1.
param(
    [string]$Target = '',
    [string]$Wrapper = ''
)
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Target)) {
    $Target = Join-Path $PSScriptRoot 'trae_nginxzx.ps1'
}
if (-not (Test-Path -LiteralPath $Target)) {
    Write-Output ('TARGET-MISSING | ' + $Target)
    Write-Output 'VERDICT-GATE FAIL'
    Write-Output '=== GATE-NGINXZX-DONE ==='
    exit 1
}

$bytes = [IO.File]::ReadAllBytes($Target)
$nonAscii = @($bytes | Where-Object { $_ -gt 127 })
$bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
$body = [IO.File]::ReadAllText($Target)
$lines = $body -split "`r?`n"

# The wire cap applies to the body encoded UTF-8 then base64. UTF-16LE silently doubles the size
# and has caused CmdContent.ExceedLimit twice already.
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($body))

$errs = $null
$toks = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($Target, [ref]$toks, [ref]$errs)
$perr = if ($errs) { @($errs).Count } else { 0 }

# Forbidden = every verb the user vetoed, every verb that would widen scope past "one named
# service", plus the security-hardcoded.md prohibitions. Scanned on non-comment lines only
# (see header note 2). Case-sensitive: 'Restart-Service' does not contain 'Start-Service', and
# 'Start-Service' does not contain 'Restart-Service', so the required/forbidden split is unambiguous.
$forbidden = @(
    'Set-Service', 'Suspend-Service', 'Restart-Service', 'New-Service', 'Remove-Service',
    'Stop-Process',
    'Remove-WebBinding', 'New-WebBinding', 'Set-WebBinding', 'Clear-ItemProperty',
    'net stop', 'sc stop', 'sc config', 'sc delete',
    'nssm.exe',
    'ServerCertificateValidationCallback', 'SkipCertificateCheck', 'SkipCACheck',
    'Get-WinEvent', 'Invoke-Expression'
)
# Required = tokens that pin the command shape to the authorized granularity. If someone edits the
# script to widen scope (or to skip the escalation the runbook says is necessary), these vanish and
# the gate goes red. Get-Website is deliberately absent: t27c reads service state through
# Get-SvcSnapshot and site state through appcmd, and Get-Website has zero hits in the target.
$required = @(
    'nginx8147', 'netstat.exe', 'NGINXZX-DONE', 'zxjiu-backup', 'hashEqual', 'nginx.conf',
    "'start', 'site'", 'Resume-Service', 'Start-Service', 'Stop-Service', 'Get-SvcSnapshot'
)

$forbidHits = @()
for ($i = 0; $i -lt $lines.Count; $i++) {
    $ln = $lines[$i]
    if ($ln.TrimStart().StartsWith('#')) { continue }
    foreach ($f in $forbidden) {
        if ($ln.Contains($f)) { $forbidHits += ('L' + ($i + 1) + ' [' + $f + '] ' + $ln.Trim()) }
    }
}
$missingReq = @()
foreach ($r in $required) { if (-not $body.Contains($r)) { $missingReq += $r } }

$sentinel = ([regex]::Matches($body, [regex]::Escape('NGINXZX-DONE'))).Count
$secretScan = ([regex]::Matches($body, '(?i)(password|passwd|secret|token)\s*[:=]\s*[''"][^''"]{4,}')).Count
$phoneScan = ([regex]::Matches($body, '(?<!\d)1[3-9]\d{9}(?!\d)')).Count
$exitCount = ([regex]::Matches($body, '(?m)^[ \t]*exit[ \t]+[01][ \t]*\r?$')).Count

# The wire cap applies to WHATEVER IS ACTUALLY TRANSMITTED. The inner script is far over the
# 18432-byte raw limit, so it ships gzip-compressed inside a wrapper. When -Wrapper is supplied,
# fits= is measured on the wrapper (the real payload) and the inner number is reported separately
# as informational. Without -Wrapper the inner is measured, which will legitimately report
# fits=False for t27c -- that is a statement about the inner, not a defect in it.
$wireLen = $b64.Length
$wireWhat = 'inner'
if (-not [string]::IsNullOrWhiteSpace($Wrapper)) {
    if (-not (Test-Path -LiteralPath $Wrapper)) {
        Write-Output ('WRAPPER-MISSING | ' + $Wrapper)
        Write-Output 'VERDICT-GATE FAIL'
        Write-Output '=== GATE-NGINXZX-DONE ==='
        exit 1
    }
    $wireLen = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([IO.File]::ReadAllText($Wrapper))).Length
    $wireWhat = 'wrapper'
}
$fits = ($wireLen -le 24576)

Write-Output ('target=' + $Target)
Write-Output ('bytes=' + $bytes.Length)
Write-Output ('chars=' + $body.Length)
Write-Output ('lines=' + $lines.Count)
Write-Output ('nonAscii=' + $nonAscii.Count)
Write-Output ('bom=' + $bom)
Write-Output ('crlfPairs=' + ([regex]::Matches($body, "`r`n")).Count)
Write-Output ('parseErrors=' + $perr)
if ($perr -gt 0) {
    foreach ($e in (@($errs) | Select-Object -First 6)) {
        Write-Output ('  PARSE L' + $e.Extent.StartLineNumber + ': ' + $e.Message)
    }
}
Write-Output ('innerB64Len=' + $b64.Length + ' (informational: the inner ships gzipped, not raw)')
Write-Output ('b64WireLen=' + $wireLen + ' measured=' + $wireWhat + ' cap=24576 fits=' + $fits + ' headroom=' + (24576 - $wireLen))
Write-Output ('sentinelCount=' + $sentinel + ' expected=9')
Write-Output ('exitStatementCount=' + $exitCount + ' expected=9')
Write-Output ('forbiddenVerbHits=' + $forbidHits.Count + ' (comment lines skipped by design)')
foreach ($h in ($forbidHits | Select-Object -First 12)) { Write-Output ('  FORBIDDEN ' + $h) }
Write-Output ('missingRequiredTokens=' + $missingReq.Count + $(if ($missingReq.Count) { ' -> ' + ($missingReq -join ',') } else { '' }))
Write-Output ('hardcodedSecretScan=' + $secretScan)
Write-Output ('barePhoneLiteralScan=' + $phoneScan + ' (must be 0: t27a already removed those literals, this script has no reason to carry one)')

$gate = ($nonAscii.Count -eq 0 -and -not $bom -and $perr -eq 0 -and $fits `
        -and $sentinel -eq 9 -and $exitCount -eq 9 `
        -and $forbidHits.Count -eq 0 -and $missingReq.Count -eq 0 `
        -and $secretScan -eq 0 -and $phoneScan -eq 0)
Write-Output ('VERDICT-GATE ' + $(if ($gate) { 'PASS' } else { 'FAIL' }))
Write-Output '=== GATE-NGINXZX-DONE ==='
if (-not $gate) { exit 1 }
exit 0
