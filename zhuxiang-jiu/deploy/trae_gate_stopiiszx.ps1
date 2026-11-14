# trae_gate_stopiiszx.ps1 -- pre-wire gate for t27b, the MUTATING script that releases TCP/80
# on the zxjiu.com host by stopping exactly one IIS site.
#
# WHY THIS IS NOT THE READ-ONLY GATE: trae_gate_pre27zx.ps1 enforces mutatingVerbScan == 0.
# t27b is authorized to mutate one thing, so a zero-mutation gate would be a false red. This
# gate instead does WHITELIST-STYLE VERB AUDITING:
#   * $forbidden -- every service/process/binding/TLS/event verb the user explicitly vetoed or
#     that security-hardcoded.md forbids. Any hit is a hard FAIL, with the offending lines
#     printed so a reviewer can see the shape, not just a count.
#   * $required  -- tokens that MUST be present, pinning the command shape to the authorized
#     granularity. If someone edits the script to widen scope, these tokens disappear and the
#     gate goes red.
#
# The user's veto list, restated as forbidden tokens: no stopping/starting/resuming/restarting/
# reconfiguring any Windows service, no killing processes, no adding/removing/editing site
# bindings, no legacy service control verbs, no disabling TLS validation, no event-log dumping
# (it blew the 24 KB output budget once), no dynamic code evaluation.
#
# Sentinel expected count = 7, NOT 5. Section [1/6] has TWO independent red exits (a required
# path is missing, and the live site drifted from the authorized baseline), so the real exit
# inventory is: before-missing-tools / before-drift / backup-hash / readback / after /
# summary-fail / summary-pass. Counting 5 here would have been a false red on a good script --
# exactly the mistake the PUB3XZX-DONE rename caused once already.
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
    $Target = Join-Path $PSScriptRoot 'trae_stopiiszx.ps1'
}
if (-not (Test-Path -LiteralPath $Target)) {
    Write-Output ('TARGET-MISSING | ' + $Target)
    Write-Output 'VERDICT-GATE FAIL'
    Write-Output '=== GATE-SI-DONE ==='
    exit 1
}

$bytes = [IO.File]::ReadAllBytes($Target)
$nonAscii = @($bytes | Where-Object { $_ -gt 127 })
$bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
$body = [IO.File]::ReadAllText($Target)
$lines = $body -split "`r?`n"

# The wire cap applies to the body encoded UTF-8 then base64. UTF-16LE silently doubles the
# size and has caused CmdContent.ExceedLimit twice already.
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($body))

$errs = $null
$toks = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($Target, [ref]$toks, [ref]$errs)
$perr = if ($errs) { @($errs).Count } else { 0 }

$forbidden = @(
    'Stop-Service', 'Start-Service', 'Resume-Service', 'Restart-Service', 'Set-Service',
    'New-Service', 'Remove-Service', 'Stop-Process',
    'Remove-WebBinding', 'New-WebBinding', 'Set-WebBinding', 'Clear-ItemProperty',
    'net stop', 'sc stop', 'sc config', 'sc delete',
    'ServerCertificateValidationCallback', 'SkipCertificateCheck', 'SkipCACheck',
    'Get-WinEvent', 'Invoke-Expression'
)
$required = @(
    "'stop','site'", "'start','site'", 'applicationHost.config', 'Get-Website',
    'netstat.exe', 'nginx8147', 'STOPIISZX-DONE', 'zxjiu-backup', 'hashEqual'
)

$forbidHits = @()
foreach ($f in $forbidden) {
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].Contains($f)) { $forbidHits += ('L' + ($i + 1) + ' [' + $f + '] ' + $lines[$i].Trim()) }
    }
}
$missingReq = @()
foreach ($r in $required) { if (-not $body.Contains($r)) { $missingReq += $r } }

$sentinel = ([regex]::Matches($body, [regex]::Escape('STOPIISZX-DONE'))).Count
$secretScan = ([regex]::Matches($body, '(?i)(password|passwd|secret|token)\s*[:=]\s*[''"][^''"]{4,}')).Count
$phoneScan = ([regex]::Matches($body, '(?<!\d)1[3-9]\d{9}(?!\d)')).Count
$exitCount = ([regex]::Matches($body, '(?m)^\s*exit\s+[01]\s*$')).Count
# The wire cap applies to WHATEVER IS ACTUALLY TRANSMITTED. The inner script is 21238 bytes =>
# b64 28320, over the 24576 cap, so it ships gzip-compressed inside trae_stopiiszx_wire.ps1.
# When -Wrapper is supplied, fits= is measured on the wrapper (the real payload) and the inner
# number is reported separately as informational. Without -Wrapper the inner is measured, which
# is correct only for a script small enough to go over the wire on its own.
$wireLen = $b64.Length
$wireWhat = 'inner'
if (-not [string]::IsNullOrWhiteSpace($Wrapper)) {
    if (-not (Test-Path -LiteralPath $Wrapper)) {
        Write-Output ('WRAPPER-MISSING | ' + $Wrapper)
        Write-Output 'VERDICT-GATE FAIL'
        Write-Output '=== GATE-SI-DONE ==='
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
Write-Output ('sentinelCount=' + $sentinel + ' expected=7')
Write-Output ('exitStatementCount=' + $exitCount + ' expected=7')
Write-Output ('forbiddenVerbHits=' + $forbidHits.Count)
foreach ($h in ($forbidHits | Select-Object -First 12)) { Write-Output ('  FORBIDDEN ' + $h) }
Write-Output ('missingRequiredTokens=' + $missingReq.Count + $(if ($missingReq.Count) { ' -> ' + ($missingReq -join ',') } else { '' }))
Write-Output ('hardcodedSecretScan=' + $secretScan)
Write-Output ('barePhoneLiteralScan=' + $phoneScan + ' (must be 0: t27a already removed those literals, this script has no reason to carry one)')

$gate = ($nonAscii.Count -eq 0 -and -not $bom -and $perr -eq 0 -and $fits `
        -and $sentinel -eq 7 -and $exitCount -eq 7 `
        -and $forbidHits.Count -eq 0 -and $missingReq.Count -eq 0 `
        -and $secretScan -eq 0 -and $phoneScan -eq 0)
Write-Output ('VERDICT-GATE ' + $(if ($gate) { 'PASS' } else { 'FAIL' }))
Write-Output '=== GATE-SI-DONE ==='
if (-not $gate) { exit 1 }
exit 0
