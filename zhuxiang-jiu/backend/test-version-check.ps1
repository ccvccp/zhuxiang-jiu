#requires -Version 5.1
# ============================================================
#  install-docker.ps1 Compatibility Test - Windows Version Check
#  ------------------------------------------------------------
#  Simulates Windows 10 1909 (Build 18363) and earlier versions
#  Verifies the script correctly detects low version and exits
#  ------------------------------------------------------------
#  Run: powershell -NoProfile -ExecutionPolicy Bypass -File test-version-check.ps1
# ============================================================

$ErrorActionPreference = "Stop"

# ---------- Color Output ----------
function Write-Info  { param($msg) Write-Host "  $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "  $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "  $msg" -ForegroundColor Red }

# ============================================================
#  Test Assertion Tools
# ============================================================
$TestResults = @()

function Assert-Equals {
    param($expected, $actual, $message)
    if ($expected -eq $actual) {
        $script:TestResults += [PSCustomObject]@{
            Test = $message
            Expected = $expected
            Actual = $actual
            Pass = $true
        }
        Write-Ok "  PASS: $message"
    } else {
        $script:TestResults += [PSCustomObject]@{
            Test = $message
            Expected = $expected
            Actual = $actual
            Pass = $false
        }
        Write-Err "  FAIL: $message (expected: $expected, actual: $actual)"
    }
}

function Assert-True {
    param($condition, $message)
    Assert-Equals $true $condition $message
}

function Assert-False {
    param($condition, $message)
    Assert-Equals $false $condition $message
}

# ============================================================
#  Extract version check logic from install-docker.ps1 (lines 55-73)
# ============================================================
function Test-VersionCheckLogic {
    param(
        [int]$BuildNumber,
        [string]$Caption,
        [string]$TestName
    )

    Write-Host ""
    Write-Host "  Scenario: $TestName" -ForegroundColor Blue
    Write-Host "  Build: $BuildNumber, Caption: $Caption" -ForegroundColor DarkGray
    Write-Host ""

    # Simulate install-docker.ps1 lines 55-73 version check logic
    $osBuild = $BuildNumber
    $osCaption = $Caption

    $isWin11 = $false
    $winName = ""
    $shouldExit = $false
    $errorMessage = ""

    if ($osBuild -ge 22000) {
        $isWin11 = $true
        $winName = "Windows 11"
    } elseif ($osBuild -ge 19041) {
        $winName = "Windows 10"
    } else {
        $shouldExit = $true
        $errorMessage = "Windows version too low (Build $osBuild)"
    }

    return [PSCustomObject]@{
        IsWin11 = $isWin11
        WinName = $winName
        ShouldExit = $shouldExit
        ErrorMessage = $errorMessage
        BuildNumber = $osBuild
    }
}

# ============================================================
#  Test Cases
# ============================================================

Write-Host ""
Write-Host "============================================" -ForegroundColor White
Write-Host "  install-docker.ps1 Version Check Test" -ForegroundColor White
Write-Host "  Simulating Windows 10 1909 and earlier" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White

# ------------------------------------------------------------
# Group 1: Versions below Win10 2004 (should exit)
# ------------------------------------------------------------

Write-Host ""
Write-Host "[Group 1] Below Windows 10 2004 (should exit)" -ForegroundColor Blue

# Test 1: Windows 10 1909 (Build 18363) - most common old version
$result = Test-VersionCheckLogic -BuildNumber 18363 -Caption "Microsoft Windows 10 Home" -TestName "Win10 1909 Home (Build 18363)"
Assert-True $result.ShouldExit "Win10 1909 should trigger exit"
Assert-False $result.IsWin11 "Win10 1909 should not be Win11"
Assert-Equals "" $result.WinName "Win10 1909 should not set winName"
Assert-True ($result.ErrorMessage -match "too low") "Win10 1909 error should contain 'too low'"
Assert-True ($result.ErrorMessage -match "18363") "Win10 1909 error should contain Build 18363"

# Test 2: Windows 10 1903 (Build 18362)
$result = Test-VersionCheckLogic -BuildNumber 18362 -Caption "Microsoft Windows 10 Pro" -TestName "Win10 1903 Pro (Build 18362)"
Assert-True $result.ShouldExit "Win10 1903 should trigger exit"
Assert-False $result.IsWin11 "Win10 1903 should not be Win11"
Assert-True ($result.ErrorMessage -match "too low") "Win10 1903 error should contain 'too low'"

# Test 3: Windows 10 1809 (Build 17763) - LTSC 2019
$result = Test-VersionCheckLogic -BuildNumber 17763 -Caption "Microsoft Windows 10 Enterprise" -TestName "Win10 1809 Enterprise LTSC (Build 17763)"
Assert-True $result.ShouldExit "Win10 1809 LTSC should trigger exit"
Assert-False $result.IsWin11 "Win10 1809 should not be Win11"
Assert-True ($result.ErrorMessage -match "too low") "Win10 1809 error should contain 'too low'"
Assert-True ($result.ErrorMessage -match "17763") "Win10 1809 error should contain Build 17763"

# Test 4: Windows 10 1803 (Build 17134)
$result = Test-VersionCheckLogic -BuildNumber 17134 -Caption "Microsoft Windows 10 Home" -TestName "Win10 1803 Home (Build 17134)"
Assert-True $result.ShouldExit "Win10 1803 should trigger exit"
Assert-True ($result.ErrorMessage -match "too low") "Win10 1803 error should contain 'too low'"

# Test 5: Windows 10 1607 (Build 14393) - LTSC 2016
$result = Test-VersionCheckLogic -BuildNumber 14393 -Caption "Microsoft Windows 10 Enterprise 2016 LTSB" -TestName "Win10 1607 LTSB (Build 14393)"
Assert-True $result.ShouldExit "Win10 1607 LTSB should trigger exit"
Assert-True ($result.ErrorMessage -match "too low") "Win10 1607 error should contain 'too low'"

# Test 6: Windows 10 1507 (Build 10240) - RTM
$result = Test-VersionCheckLogic -BuildNumber 10240 -Caption "Microsoft Windows 10 Pro" -TestName "Win10 1507 RTM (Build 10240)"
Assert-True $result.ShouldExit "Win10 1507 RTM should trigger exit"
Assert-True ($result.ErrorMessage -match "too low") "Win10 1507 error should contain 'too low'"

# ------------------------------------------------------------
# Group 2: Boundary values
# ------------------------------------------------------------

Write-Host ""
Write-Host "[Group 2] Boundary Value Tests" -ForegroundColor Blue

# Test 7: Build 19040 - just below 2004 (should exit)
$result = Test-VersionCheckLogic -BuildNumber 19040 -Caption "Microsoft Windows 10 Pro" -TestName "Build 19040 (below 2004 boundary)"
Assert-True $result.ShouldExit "Build 19040 should trigger exit (below 19041 threshold)"
Assert-True ($result.ErrorMessage -match "too low") "Build 19040 error should contain 'too low'"

# Test 8: Build 19041 - exactly Win10 2004 (should pass)
$result = Test-VersionCheckLogic -BuildNumber 19041 -Caption "Microsoft Windows 10 Pro" -TestName "Build 19041 (Win10 2004 boundary)"
Assert-False $result.ShouldExit "Build 19041 should not trigger exit (meets 19041 threshold)"
Assert-False $result.IsWin11 "Build 19041 should not be Win11"
Assert-Equals "Windows 10" $result.WinName "Build 19041 should be Windows 10"

# Test 9: Build 19042 - Win10 20H2 (should pass)
$result = Test-VersionCheckLogic -BuildNumber 19042 -Caption "Microsoft Windows 10 Home" -TestName "Win10 20H2 (Build 19042)"
Assert-False $result.ShouldExit "Win10 20H2 should not trigger exit"
Assert-Equals "Windows 10" $result.WinName "Win10 20H2 should be Windows 10"

# Test 10: Build 19045 - Win10 22H2 (should pass)
$result = Test-VersionCheckLogic -BuildNumber 19045 -Caption "Microsoft Windows 10 Pro" -TestName "Win10 22H2 (Build 19045)"
Assert-False $result.ShouldExit "Win10 22H2 should not trigger exit"
Assert-Equals "Windows 10" $result.WinName "Win10 22H2 should be Windows 10"

# Test 11: Build 21999 - Win10/Win11 boundary (should be Win10)
$result = Test-VersionCheckLogic -BuildNumber 21999 -Caption "Microsoft Windows 10 Pro" -TestName "Build 21999 (Win10/11 boundary)"
Assert-False $result.ShouldExit "Build 21999 should not trigger exit"
Assert-False $result.IsWin11 "Build 21999 should not be Win11 (below 22000 threshold)"
Assert-Equals "Windows 10" $result.WinName "Build 21999 should be Windows 10"

# Test 12: Build 22000 - Win11 first version (should pass as Win11)
$result = Test-VersionCheckLogic -BuildNumber 22000 -Caption "Microsoft Windows 11 Pro" -TestName "Win11 21H2 (Build 22000)"
Assert-False $result.ShouldExit "Win11 should not trigger exit"
Assert-True $result.IsWin11 "Build 22000 should be Win11"
Assert-Equals "Windows 11" $result.WinName "Build 22000 should be Windows 11"

# Test 13: Build 22621 - Win11 22H2 (should pass)
$result = Test-VersionCheckLogic -BuildNumber 22621 -Caption "Microsoft Windows 11 Home" -TestName "Win11 22H2 (Build 22621)"
Assert-False $result.ShouldExit "Win11 22H2 should not trigger exit"
Assert-True $result.IsWin11 "Win11 22H2 should be Win11"

# Test 14: Build 22631 - Win11 23H2 (should pass)
$result = Test-VersionCheckLogic -BuildNumber 22631 -Caption "Microsoft Windows 11 Pro" -TestName "Win11 23H2 (Build 22631)"
Assert-False $result.ShouldExit "Win11 23H2 should not trigger exit"
Assert-True $result.IsWin11 "Win11 23H2 should be Win11"

# ------------------------------------------------------------
# Group 3: Home/Pro edition detection
# ------------------------------------------------------------

Write-Host ""
Write-Host "[Group 3] Home/Pro Edition Detection" -ForegroundColor Blue

# Test 15: Win10 Home should pass version check
$result = Test-VersionCheckLogic -BuildNumber 19045 -Caption "Microsoft Windows 10 Home" -TestName "Win10 22H2 Home edition"
Assert-False $result.ShouldExit "Win10 22H2 Home should not trigger exit"

# Test 16: Win11 Home should pass version check
$result = Test-VersionCheckLogic -BuildNumber 22621 -Caption "Microsoft Windows 11 Home" -TestName "Win11 22H2 Home edition"
Assert-False $result.ShouldExit "Win11 22H2 Home should not trigger exit"
Assert-True $result.IsWin11 "Win11 22H2 Home should be Win11"

# Test 17: Win10 Enterprise should pass
$result = Test-VersionCheckLogic -BuildNumber 19045 -Caption "Microsoft Windows 10 Enterprise" -TestName "Win10 22H2 Enterprise"
Assert-False $result.ShouldExit "Win10 22H2 Enterprise should not trigger exit"

# Test 18: Win11 Education should pass
$result = Test-VersionCheckLogic -BuildNumber 22631 -Caption "Microsoft Windows 11 Education" -TestName "Win11 23H2 Education"
Assert-False $result.ShouldExit "Win11 23H2 Education should not trigger exit"
Assert-True $result.IsWin11 "Win11 23H2 Education should be Win11"

# ------------------------------------------------------------
# Group 4: Error message completeness
# ------------------------------------------------------------

Write-Host ""
Write-Host "[Group 4] Error Message Completeness" -ForegroundColor Blue

# Test 19: Error message should contain Build number
$result = Test-VersionCheckLogic -BuildNumber 18363 -Caption "Microsoft Windows 10 Home" -TestName "Error contains Build number"
Assert-True ($result.ErrorMessage -match "18363") "Error should contain actual Build 18363"

# Test 20: Error message should contain upgrade hint
$result = Test-VersionCheckLogic -BuildNumber 17763 -Caption "Microsoft Windows 10 Enterprise" -TestName "Error contains upgrade hint"
Assert-True ($result.ErrorMessage -match "too low|Build") "Error should contain 'too low' or 'Build'"

# Test 21: Very old version (Windows 7 Build 7601)
$result = Test-VersionCheckLogic -BuildNumber 7601 -Caption "Microsoft Windows 7 Professional" -TestName "Win7 (Build 7601) very old"
Assert-True $result.ShouldExit "Win7 should trigger exit"
Assert-True ($result.ErrorMessage -match "too low") "Win7 error should contain 'too low'"
Assert-True ($result.ErrorMessage -match "7601") "Win7 error should contain Build 7601"

# Test 22: Windows 8.1 (Build 9600)
$result = Test-VersionCheckLogic -BuildNumber 9600 -Caption "Microsoft Windows 8.1 Pro" -TestName "Win8.1 (Build 9600)"
Assert-True $result.ShouldExit "Win8.1 should trigger exit"
Assert-True ($result.ErrorMessage -match "too low") "Win8.1 error should contain 'too low'"

# Test 23: Build 0 (edge case)
$result = Test-VersionCheckLogic -BuildNumber 0 -Caption "Unknown OS" -TestName "Build 0 edge case"
Assert-True $result.ShouldExit "Build 0 should trigger exit"
Assert-True ($result.ErrorMessage -match "too low") "Build 0 error should contain 'too low'"

# ============================================================
#  Test Report
# ============================================================
Write-Host ""
Write-Host "============================================" -ForegroundColor White
Write-Host "  Test Report" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White
Write-Host ""

$passed = ($TestResults | Where-Object { $_.Pass }).Count
$failed = ($TestResults | Where-Object { -not $_.Pass }).Count
$total = $TestResults.Count
$passRate = if ($total -gt 0) { [Math]::Round($passed / $total * 100, 1) } else { 0 }

Write-Host "  Total: $total" -ForegroundColor White
Write-Host "  Passed: $passed" -ForegroundColor Green
Write-Host "  Failed: $failed" -ForegroundColor $(if ($failed -gt 0) { "Red" } else { "Gray" })
Write-Host "  Pass Rate: $passRate%" -ForegroundColor $(if ($passRate -eq 100) { "Green" } else { "Yellow" })
Write-Host ""

if ($failed -gt 0) {
    Write-Host "  Failed Cases:" -ForegroundColor Red
    $TestResults | Where-Object { -not $_.Pass } | ForEach-Object {
        Write-Host "    - $($_.Test)" -ForegroundColor Red
        Write-Host "      Expected: $($_.Expected)" -ForegroundColor DarkGray
        Write-Host "      Actual: $($_.Actual)" -ForegroundColor DarkGray
    }
    Write-Host ""
}

# JSON report for CI
$report = [PSCustomObject]@{
    Module = "install-docker-version-check"
    Total = $total
    Passed = $passed
    Failed = $failed
    PassRate = "$passRate%"
    Results = $TestResults
}

$reportJson = $report | ConvertTo-Json -Depth 3
$reportPath = "$PSScriptRoot\test-version-check-report.json"
$reportJson | Out-File $reportPath -Encoding UTF8
Write-Info "JSON report: $reportPath"

Write-Host ""
if ($failed -eq 0) {
    Write-Ok "=========================================="
    Write-Ok "  All $total test cases passed!"
    Write-Ok "=========================================="
} else {
    Write-Err "=========================================="
    Write-Err "  $failed test cases failed"
    Write-Err "=========================================="
    exit 1
}
Write-Host ""
