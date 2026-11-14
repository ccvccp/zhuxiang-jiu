# trae_gate_pub3xzx.ps1 - pre-wire gate for t27a (the zxjiu.com publish script).
#
# Derived from trae_gate_pub3x.ps1 (the t23 gate) with three deliberate changes:
#   1) $Target resolves through $PSScriptRoot instead of a hardcoded Temp path, so
#      this file itself stays 100% ASCII. The repo root contains Chinese characters;
#      spelling it out here would trip this gate's own ASCII guard.
#   2) The sentinel pattern is PUB3XZX-DONE, not PUB3X-DONE. The zx script renamed all
#      four sentinels, so reusing the t23 pattern reads sentinelCount=0 -- a false red
#      that would have blocked a good script. Expected value = 4:
#        PREFLIGHT FAIL / BACKUP FAIL / SUMMARY FAIL / SUMMARY PASS.
#   3) barePhoneLiteralScan is REPORTED, NOT GATED. Unlike t25's verifier (which builds
#      every sensitive literal from codepoints), this script's whole job is to find and
#      delete the seed phone literal, so it must spell it out. Gating it at 0 would be
#      a false red. The value is echoed so a future reader can see the count moved.
#
# fail-closed (security-hard-coded 2.3): an unmeasurable or missing target exits 1.
param(
    [string]$Target = ''
)
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Target)) {
    $Target = Join-Path $PSScriptRoot 'trae_pub3xzx.ps1'
}
if (-not (Test-Path -LiteralPath $Target)) {
    Write-Output ('TARGET-MISSING | ' + $Target)
    Write-Output 'VERDICT-GATE FAIL'
    Write-Output '=== GATE-ZX-DONE ==='
    exit 1
}

$bytes = [IO.File]::ReadAllBytes($Target)
$nonAscii = @($bytes | Where-Object { $_ -gt 127 })
$bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
$body = [IO.File]::ReadAllText($Target)

# The wire cap applies to the script body encoded as UTF-8 then base64. UTF-16LE would
# silently double the size and has caused CmdContent.ExceedLimit twice already.
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($body))

$errs = $null
$toks = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($Target, [ref]$toks, [ref]$errs)
$perr = if ($errs) { @($errs).Count } else { 0 }

$sentinel = ([regex]::Matches($body, [regex]::Escape('PUB3XZX-DONE'))).Count
$secretScan = ([regex]::Matches($body, '(?i)(password|passwd|secret|token)\s*[:=]\s*[''"][^''"]{4,}')).Count
$phoneScan = ([regex]::Matches($body, '(?<!\d)1[3-9]\d{9}(?!\d)')).Count
$fits = ($b64.Length -le 24576)

Write-Output ('target=' + $Target)
Write-Output ('bytes=' + $bytes.Length)
Write-Output ('chars=' + $body.Length)
Write-Output ('lines=' + ($body.Split("`n")).Count)
Write-Output ('nonAscii=' + $nonAscii.Count)
Write-Output ('bom=' + $bom)
Write-Output ('crlfPairs=' + ([regex]::Matches($body, "`r`n")).Count)
Write-Output ('parseErrors=' + $perr)
if ($perr -gt 0) {
    foreach ($e in (@($errs) | Select-Object -First 6)) {
        Write-Output ('  PARSE L' + $e.Extent.StartLineNumber + ': ' + $e.Message)
    }
}
Write-Output ('b64WireLen=' + $b64.Length + ' cap=24576 fits=' + $fits + ' headroom=' + (24576 - $b64.Length))
Write-Output ('sentinelCount=' + $sentinel + ' expected=4')
Write-Output ('hardcodedSecretScan=' + $secretScan)
Write-Output ('barePhoneLiteralScan=' + $phoneScan + ' (informational: this script deletes that literal by design)')

$gate = ($nonAscii.Count -eq 0 -and -not $bom -and $perr -eq 0 -and $fits -and $sentinel -eq 4 -and $secretScan -eq 0)
Write-Output ('VERDICT-GATE ' + $(if ($gate) { 'PASS' } else { 'FAIL' }))
Write-Output '=== GATE-ZX-DONE ==='
if (-not $gate) { exit 1 }
exit 0
