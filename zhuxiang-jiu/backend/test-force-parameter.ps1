#Requires -Version 5.1
# ============================================================
#  Unit Tests: -Force Parameter for git-ops.ps1 & github-push.ps1
#  ------------------------------------------------------------
#  Approach: Source Code Analysis (no script execution needed)
#    - Pattern matching on source code to verify logic paths
#    - Works in sandboxed environments where child process
#      working directory / env vars are restricted
#  ------------------------------------------------------------
#  Coverage:
#    A. Parameter Definition Tests     - 4 tests
#    B. Force Logic Flow Tests          - 6 tests
#    C. DryRun Integration Tests        - 5 tests
#    D. Safety / Rejection Tests        - 4 tests
#    E. Help / Documentation Tests     - 3 tests
#    Total: 22 test cases
#  ------------------------------------------------------------
#  Usage:
#    powershell -NoProfile -ExecutionPolicy Bypass -File test-force-parameter.ps1
#    powershell -NoProfile -ExecutionPolicy Bypass -File test-force-parameter.ps1 -Verbose
# ============================================================

param([switch]$Verbose)

$ErrorActionPreference = "Stop"
$script:ScriptDir  = Split-Path -Parent $PSCommandPath
$script:GitOps     = Join-Path $ScriptDir "git-ops.ps1"
$script:GithubPush = Join-Path $ScriptDir "github-push.ps1"
$script:Passed = 0
$script:Failed = 0
$script:Results = @()

# ---------- Helpers ----------
function Add-Pass { param($id, $name, $detail="")
    $script:Passed++
    $script:Results += [PSCustomObject]@{ Id=$id; Name=$name; Status='PASS'; Detail=$detail }
    if ($Verbose) { Write-Host "  [PASS] $id : $name" -ForegroundColor Green }
    else { Write-Host "." -NoNewline -ForegroundColor Green }
}

function Add-Fail { param($id, $name, $detail="")
    $script:Failed++
    $script:Results += [PSCustomObject]@{ Id=$id; Name=$name; Status='FAIL'; Detail=$detail }
    Write-Host "F" -NoNewline -ForegroundColor Red
    if ($Verbose) { Write-Host "" ; Write-Host "  [FAIL] $id : $name" -ForegroundColor Red; Write-Host "         $detail" -ForegroundColor DarkGray }
}

function Get-Source { param($file)
    return Get-Content $file -Raw
}

function Test-Pattern { param($content, $pattern, $id, $name)
    if ($content -match $pattern) { Add-Pass $id $name; return $true }
    else { Add-Fail $id $name "Pattern not found: $pattern"; return $false }
}

function Test-NotPattern { param($content, $pattern, $id, $name)
    if ($content -notmatch $pattern) { Add-Pass $id $name; return $true }
    else { Add-Fail $id $name "Unexpected pattern found: $pattern"; return $false }
}

# ============================================================
#  A. Parameter Definition Tests
# ============================================================
Write-Host "=== A. Parameter Definition Tests ===" -ForegroundColor Cyan

$gitOpsSrc = Get-Source $GitOps
$pushSrc = Get-Source $GithubPush

# TC01: git-ops.ps1 param block has [switch]$Force
Test-Pattern $gitOpsSrc '\[switch\]\$Force' "TC01" "git-ops.ps1 param has [switch]`$Force" | Out-Null

# TC02: github-push.ps1 param block has [switch]$Force
Test-Pattern $pushSrc '\[switch\]\$Force' "TC02" "github-push.ps1 param has [switch]`$Force" | Out-Null

# TC03: git-ops.ps1 Do-Push passes -Force to github-push.ps1
Test-Pattern $gitOpsSrc 'if \(\$Force\)\s*\{\s*\$pushArgs \+= "-Force"\s*\}' "TC03" "git-ops.ps1 Do-Push passes -Force" | Out-Null

# TC04: git-ops.ps1 Do-Sync passes -Force
$syncSection = $gitOpsSrc -split "function Do-Sync" | Select-Object -Last 1
if ($syncSection -match 'if \(\$Force\)\s*\{\s*\$pushArgs \+= "-Force"\s*\}') {
    Add-Pass "TC04" "git-ops.ps1 Do-Sync passes -Force"
} else {
    Add-Fail "TC04" "git-ops.ps1 Do-Sync passes -Force" "Pattern not in Do-Sync section"
}

# ============================================================
#  B. Force Logic Flow Tests
# ============================================================
Write-Host ""
Write-Host "=== B. Force Logic Flow Tests ===" -ForegroundColor Cyan

# TC05: github-push.ps1 adds --force-with-lease to push args when $Force
Test-Pattern $pushSrc 'if \(\$Force\)\s*\{\s*\$pushArgs \+= "--force-with-lease"\s*\}' "TC05" "push args include --force-with-lease" | Out-Null

# TC06: github-push.ps1 DryRun args include --force-with-lease when $Force
Test-Pattern $pushSrc 'if \(\$Force\)\s*\{\s*\$dryRunArgs \+= "--force-with-lease"\s*\}' "TC06" "DryRun args include --force-with-lease" | Out-Null

# TC07: github-push.ps1 shows "Force mode" warning when $Force
Test-Pattern $pushSrc 'Force mode: using --force-with-lease' "TC07" "shows Force mode warning" | Out-Null

# TC08: github-push.ps1 Force warning is conditional (if $Force)
Test-Pattern $pushSrc 'if \(\$Force\)\s*\{[^}]*Force mode' "TC08" "Force mode is conditional on `$Force" | Out-Null

# TC09: github-push.ps1 DryRun output uses $dryRunArgs (which includes --force-with-lease)
# The DryRun output is built dynamically: "DRY RUN: git $($dryRunArgs -join ' ')"
Test-Pattern $pushSrc 'DRY RUN.*git.*\$dryRunArgs' "TC09" "DryRun output uses dryRunArgs array" | Out-Null

# TC10: github-push.ps1 DryRun WITHOUT Force shows "git push" (normal, no lease)
# Verify the dryRunArgs array starts with "push" and conditionally adds --force-with-lease
$dryRunSection = $pushSrc -split 'if \(\$DryRun\)' | Select-Object -Skip 1 -First 1
if ($dryRunSection -match '\$dryRunArgs = @\("push"\)') {
    Add-Pass "TC10" "DryRun base command is 'git push' (without lease)"
} else {
    Add-Fail "TC10" "DryRun base command is 'git push' (without lease)" "Base push args not found"
}

# ============================================================
#  C. DryRun Integration Tests (Source Analysis)
# ============================================================
Write-Host ""
Write-Host "=== C. DryRun Integration Tests ===" -ForegroundColor Cyan

# TC11: DryRun exit happens AFTER Force mode warning
# The Force warning should come before the DryRun exit
$forceWarningPos = $pushSrc.IndexOf("Force mode: using --force-with-lease")
$dryRunExitPos = $pushSrc.IndexOf("exit 0", $forceWarningPos)
if ($forceWarningPos -ge 0 -and $dryRunExitPos -gt $forceWarningPos) {
    Add-Pass "TC11" "Force warning appears before DryRun exit"
} else {
    Add-Fail "TC11" "Force warning appears before DryRun exit" "Warning pos: $forceWarningPos, Exit pos: $dryRunExitPos"
}

# TC12: DryRun WITHOUT -Force does NOT include "Force mode" warning
# The Force mode warning is inside "if ($Force)" block, so without -Force it's skipped
Test-Pattern $pushSrc 'if \(\$Force\)\s*\{\s*Write-Warn.*Force mode' "TC12" "Force mode guarded by if(`$Force)" | Out-Null

# TC13: DryRun WITHOUT -Force does NOT include "--force-with-lease"
# The --force-with-lease addition is inside "if ($Force)" block
Test-Pattern $pushSrc 'if \(\$Force\)\s*\{\s*\$dryRunArgs \+= "--force-with-lease"' "TC13" "lease addition guarded by if(`$Force)" | Out-Null

# TC14: git-ops.ps1 Do-Push delegates -Force (full chain verification)
# Verify that Do-Push adds -Force to pushArgs when $Force is set
Test-Pattern $gitOpsSrc 'if \(\$Force\)\s*\{\s*\$pushArgs \+= "-Force"\s*\}' "TC14" "Do-Push delegates -Force to github-push.ps1" | Out-Null

# TC15: git-ops.ps1 help mentions -Force with --force-with-lease
Test-Pattern $gitOpsSrc '-Force.*--force-with-lease' "TC15" "help links -Force to --force-with-lease" | Out-Null

# ============================================================
#  D. Safety / Rejection Tests
# ============================================================
Write-Host ""
Write-Host "=== D. Safety / Rejection Tests ===" -ForegroundColor Cyan

# TC16: github-push.ps1 rejected handler has Force guard
$rejectedSection = $pushSrc -split "rejected\|fetch first\|non-fast-forward" | Select-Object -Last 1
if ($rejectedSection -match 'if \(\$Force\)' -and ($rejectedSection -match 'do NOT escalate' -or $rejectedSection -match 'SAFETY feature')) {
    Add-Pass "TC16" "rejected handler guards Force (no escalation)"
} else {
    Add-Fail "TC16" "rejected handler guards Force (no escalation)" "Force guard not found in rejected section"
}

# TC17: Force-with-lease rejection does NOT fall back to --force
# Verify the Force path in rejected handler uses 'break' not '--force'
if ($rejectedSection -match 'if \(\$Force\)' -and $rejectedSection -match 'break') {
    $forceBlock = $rejectedSection -split 'if \(\$Force\)' | Select-Object -Skip 1 -First 1
    $forceBlock = $forceBlock -split 'Write-Err "Push rejected' | Select-Object -First 1
    if ($forceBlock -match 'break' -and $forceBlock -notmatch 'Invoke-Git.*push.*--force(?!-with-lease)') {
        Add-Pass "TC17" "Force rejection uses break (no --force fallback)"
    } else {
        Add-Fail "TC17" "Force rejection uses break (no --force fallback)" "Force block may fall back to --force"
    }
} else {
    Add-Fail "TC17" "Force rejection uses break (no --force fallback)" "Force guard or break not found"
}

# TC18: Interactive force push (option 2) is separate from -Force path
# The interactive --force is in the Read-Host choice, not in the $Force path
Test-Pattern $pushSrc 'Read-Host.*Force push' "TC18" "interactive --force is separate from -Force" | Out-Null

# TC19: Force-with-lease rejection provides recovery guidance
Test-Pattern $pushSrc 'Force-with-lease rejected.*remote has newer commits' "TC19" "lease rejection provides guidance" | Out-Null

# ============================================================
#  E. Help / Documentation Tests
# ============================================================
Write-Host ""
Write-Host "=== E. Help / Documentation Tests ===" -ForegroundColor Cyan

# TC20: git-ops.ps1 help documents -Force option
Test-Pattern $gitOpsSrc '-Force\s+.*Force push with --force-with-lease' "TC20" "help documents -Force option" | Out-Null

# TC21: git-ops.ps1 help has -Force usage example
Test-Pattern $gitOpsSrc 'git-ops\.ps1.*-Action push.*-Force' "TC21" "help has -Force example" | Out-Null

# TC22: github-push.ps1 header documents -Force usage
Test-Pattern $pushSrc 'Force push.*with lease.*safer' "TC22" "github-push.ps1 header documents -Force" | Out-Null

# ============================================================
#  Summary
# ============================================================
Write-Host ""
Write-Host ""
Write-Host "==========================================" -ForegroundColor White
Write-Host "  Test Summary: -Force Parameter" -ForegroundColor White
Write-Host "==========================================" -ForegroundColor White
Write-Host "  Total:   $($script:Passed + $script:Failed)" -ForegroundColor White
Write-Host "  Passed:  $script:Passed" -ForegroundColor Green
Write-Host "  Failed:  $script:Failed" -ForegroundColor $(if($script:Failed -gt 0){'Red'}else{'White'})
Write-Host "==========================================" -ForegroundColor White
Write-Host ""

if ($script:Failed -gt 0) {
    Write-Host "Failed Tests:" -ForegroundColor Red
    $script:Results | Where-Object { $_.Status -eq 'FAIL' } | ForEach-Object {
        Write-Host "  [$($_.Id)] $($_.Name)" -ForegroundColor Red
        if ($_.Detail) { Write-Host "         $($_.Detail)" -ForegroundColor DarkGray }
    }
    Write-Host ""
}

if ($script:Failed -eq 0) {
    Write-Host "All $($script:Passed) tests PASSED!" -ForegroundColor Green
} else {
    Write-Host "$($script:Failed) test(s) FAILED" -ForegroundColor Red
}

Write-Host ""
Write-Host "Note: Tests use source code analysis (no script execution)." -ForegroundColor DarkGray
Write-Host "      For runtime verification, run manually in VS Code terminal:" -ForegroundColor DarkGray
Write-Host "      powershell -NoProfile -ExecutionPolicy Bypass -File git-ops.ps1 -Action push -Force -DryRun" -ForegroundColor DarkGray
Write-Host ""

# Generate JSON report for CI parsing
$report = [PSCustomObject]@{
    testSuite = "-Force Parameter (Source Analysis)"
    timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    total = $script:Passed + $script:Failed
    passed = $script:Passed
    failed = $script:Failed
    success = ($script:Failed -eq 0)
    results = $script:Results
}
$reportPath = Join-Path $ScriptDir "test-force-parameter-report.json"
$report | ConvertTo-Json -Depth 3 | Out-File -FilePath $reportPath -Encoding UTF8
Write-Host "Report saved: $reportPath" -ForegroundColor DarkGray

exit $(if ($script:Failed -gt 0) { 1 } else { 0 })
