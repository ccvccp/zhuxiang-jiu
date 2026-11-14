# trae_gate_pre27zx.ps1 -- gate for the t27 PRE-FLIGHT READ-ONLY probe
# (trae_pre27zx.ps1, target host 39.107.43.149 / zxjiu.com).
#
# Derived from trae_gate_t24.ps1 with three deliberate changes:
#   1) the target is resolved from $PSScriptRoot (or -Target) instead of a hardcoded
#      Temp path, so this gate can never measure a stale copy by accident (td12d);
#   2) the sentinel is '=== PRE27ZX-DONE ===' and the expected count is 4, because the
#      probe has exactly four exits: stamp-changed FAIL / INCONCLUSIVE FAIL / drift FAIL /
#      PASS. Expecting >=3 (the t24 form) would silently tolerate a lost exit path -- that
#      class of false-green is recorded in trae_gate_pub3xzx.ps1's header;
#   3) mutatingVerbScan == 0 stays a HARD gate. This probe's whole value is that it proves
#      it wrote nothing, so a mutating verb disqualifies it rather than warning. t27b/t27c
#      are the write scripts and need the opposite gate.
# READ-ONLY over both files.
param([string]$Target = '')

$ErrorActionPreference = 'Stop'

if ($Target -eq '') { $Target = Join-Path $PSScriptRoot 'trae_pre27zx.ps1' }
if (-not (Test-Path -LiteralPath $Target)) { Write-Output ('MISSING: ' + $Target); exit 1 }

$bytes = [IO.File]::ReadAllBytes($Target)
$body  = [IO.File]::ReadAllText($Target)
$lines = ($body -split "`n")

$nonAscii = @($bytes | Where-Object { $_ -gt 127 })
$bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
$crlfPairs = ([regex]::Matches($body, "`r`n")).Count

$errs = $null
$toks = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($Target, [ref]$toks, [ref]$errs)
$parseErrors = if ($errs) { @($errs).Count } else { 0 }

$sentinel = '=== PRE27ZX-DONE ==='
$sentinelExpected = 4
$sentinelCount = ([regex]::Matches($body, [regex]::Escape($sentinel))).Count

# wire size: exactly what aliyun-ecs-rpc.ps1 'Send' computes, base64(UTF-8). Not UTF-16LE:
# two historical attempts blew CmdContent.ExceedLimit that way.
$b64Len = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($body)).Length
$b64Limit = 24576
$fits = ($b64Len -le $b64Limit)

# secret scan: assignment-ish forms of credential keywords followed by a quoted literal.
# The keyword list deliberately has no bare 'pass', so the probe's output labels
# (seedPass= / seedPhone=) do not self-trip it.
$secretRe = '(?i)(api[_-]?key|apikey|secret|password|passwd|pwd|token|private[_-]?key|access[_-]?key)\s*[:=]\s*[''"][^''"]{4,}[''"]'
$secretHits = @([regex]::Matches($body, $secretRe))
$secretLineNos = @($secretHits | ForEach-Object {
    $idx = $body.Substring(0, $_.Index)
    ($idx -split "`n").Count
})

# bare 11-digit phone-looking literal scan (must be zero: assemble from fragments)
$phoneRe = '(?<!\d)1[3-9]\d{9}(?!\d)'
$phoneHits = @([regex]::Matches($body, $phoneRe))
$phoneLineNos = @($phoneHits | ForEach-Object {
    $idx = $body.Substring(0, $_.Index)
    ($idx -split "`n").Count
})

# read-only self-attestation: the user authorized a READ-ONLY probe before the first
# write, so any mutating verb exceeds the grant even if everything else is clean.
$mutRe = '(?i)\b(Start-Service|Stop-Service|Restart-Service|Restart-Computer|Stop-Computer|Set-Service|New-Service|Remove-Service|sc\.exe\s+(start|stop|delete|config)|net\s+stop|net\s+start|nginx\.exe\s+-s|taskkill|Stop-Process|Kill\s*\(|Start-Process|Set-ItemProperty|Remove-ItemProperty|New-ItemProperty|Set-Item\s|Remove-Item\s|Set-Content|Add-Content|Out-File|New-Item\s|Set-ScheduledTask|Register-ScheduledTask|Unregister-ScheduledTask|Set-NetFirewall|New-NetFirewall|Remove-NetFirewall|netsh\s+advfirewall)'
$mutHits = @([regex]::Matches($body, $mutRe))
$mutLineNos = @($mutHits | ForEach-Object {
    $idx = $body.Substring(0, $_.Index)
    ($idx -split "`n").Count
})

# TLS-validation bypass scan: security-hard-coded forbid #9 bans disabling certificate
# validation outright. The https-loopback probe was deleted for exactly this reason, so
# any reappearance here is a regression, not a style note.
$tlsRe = '(?i)(ServerCertificateValidationCallback|DangerousAcceptAnyServerCertificateValidator|CheckCertificateRevocationList\s*=\s*\$false|ssl\._create_unverified_context|verify\s*=\s*\$false)'
$tlsHits = @([regex]::Matches($body, $tlsRe))
$tlsLineNos = @($tlsHits | ForEach-Object {
    $idx = $body.Substring(0, $_.Index)
    ($idx -split "`n").Count
})

Write-Output '=== GATE pre27zx (t27 read-only pre-flight probe) ==='
Write-Output ('file=' + $Target)
Write-Output ('bytes=' + $bytes.Length + ' chars=' + $body.Length + ' lines=' + $lines.Count)
Write-Output ('nonAscii=' + $nonAscii.Count)
Write-Output ('bom=' + $bom)
Write-Output ('crlfPairs=' + $crlfPairs)
Write-Output ('parseErrors=' + $parseErrors)
Write-Output ('sentinelCount=' + $sentinelCount + ' expected=' + $sentinelExpected)
Write-Output ('b64WireLen=' + $b64Len + ' limit=' + $b64Limit + ' fits=' + $fits + $(if ($fits) { ' headroom=' + ($b64Limit - $b64Len) } else { ' -> OVER LIMIT, needs the gzip+base64 wrapper (t24 pattern)' }))
Write-Output ('hardcodedSecretScan=' + $secretHits.Count + $(if ($secretHits.Count) { ' atLines=' + ($secretLineNos -join ',') } else { '' }))
Write-Output ('barePhoneLiteralScan=' + $phoneHits.Count + $(if ($phoneHits.Count) { ' atLines=' + ($phoneLineNos -join ',') } else { '' }))
Write-Output ('mutatingVerbScan=' + $mutHits.Count + $(if ($mutHits.Count) { ' atLines=' + ($mutLineNos -join ',') } else { '' }))
Write-Output ('tlsBypassScan=' + $tlsHits.Count + $(if ($tlsHits.Count) { ' atLines=' + ($tlsLineNos -join ',') } else { '' }))

if ($parseErrors -gt 0) {
    foreach ($e in @($errs) | Select-Object -First 8) {
        Write-Output ('  PARSE L' + $e.Extent.StartLineNumber + ': ' + $e.Message)
    }
}
if ($nonAscii.Count -gt 0) {
    $shown = 0
    for ($i = 0; $i -lt $lines.Count -and $shown -lt 8; $i++) {
        $bad = @([Text.Encoding]::UTF8.GetBytes($lines[$i]) | Where-Object { $_ -gt 127 })
        if ($bad.Count -gt 0) {
            Write-Output ('  NONASCII L' + ($i + 1) + ' count=' + $bad.Count)
            $shown++
        }
    }
}
if ($mutHits.Count -gt 0) {
    $shown = 0
    for ($i = 0; $i -lt $lines.Count -and $shown -lt 8; $i++) {
        $m = @([regex]::Matches($lines[$i], $mutRe))
        if ($m.Count -gt 0) {
            Write-Output ('  MUTATING L' + ($i + 1) + ': ' + (($m | ForEach-Object { $_.Value }) -join ',') + ' | ' + $lines[$i].Trim())
            $shown++
        }
    }
}

$gate = ($nonAscii.Count -eq 0 -and -not $bom -and $crlfPairs -eq 0 -and $parseErrors -eq 0 -and $sentinelCount -eq $sentinelExpected -and $fits -and $secretHits.Count -eq 0 -and $phoneHits.Count -eq 0 -and $mutHits.Count -eq 0 -and $tlsHits.Count -eq 0)
Write-Output ''
if ($gate) {
    Write-Output 'VERDICT-GATE PASS'
} else {
    Write-Output 'VERDICT-GATE FAIL'
}
Write-Output '=== GATE-DONE ==='
if ($gate) { exit 0 } else { exit 1 }
