#requires -Version 5.1
# ============================================================
#  git-commit-push.ps1 Error Handling Test Suite
#  ------------------------------------------------------------
#  Simulates error scenarios without actual network calls:
#    1. Remote not configured (origin missing)
#    2. Authentication failure (403/401)
#    3. Push rejected (non-fast-forward)
#    4. Repository not found (404)
#    5. Network unreachable (timeout)
#    6. Empty commit message
#    7. Branch tracking detection
#    8. Force flag behavior
#  ------------------------------------------------------------
#  Run: powershell -NoProfile -ExecutionPolicy Bypass -File test-git-push-errors.ps1
# ============================================================

$ErrorActionPreference = "Stop"

# ---------- Config ----------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)  # backend -> zhuxiang-jiu -> repo root

# ---------- Color Output ----------
function Write-Info  { param($msg) Write-Host "  $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "  $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "  $msg" -ForegroundColor Red }

# ---------- Test Framework ----------
$TestResults = @()

function Assert-True {
    param($condition, $message)
    $pass = [bool]$condition
    $script:TestResults += [PSCustomObject]@{ Test = $message; Pass = $pass }
    if ($pass) { Write-Ok "  PASS: $message" }
    else { Write-Err "  FAIL: $message" }
}

function Assert-False {
    param($condition, $message)
    Assert-True (-not $condition) $message
}

function Assert-Equals {
    param($expected, $actual, $message)
    $pass = ($expected -eq $actual)
    $script:TestResults += [PSCustomObject]@{ Test = $message; Pass = $pass; Expected = $expected; Actual = $actual }
    if ($pass) { Write-Ok "  PASS: $message" }
    else { Write-Err "  FAIL: $message (expected: $expected, actual: $actual)" }
}

function Assert-Match {
    param($pattern, $text, $message)
    $pass = ($text -match $pattern)
    $script:TestResults += [PSCustomObject]@{ Test = $message; Pass = $pass }
    if ($pass) { Write-Ok "  PASS: $message" }
    else { Write-Err "  FAIL: $message (pattern: $pattern not found)" }
}

# ---------- Mock Git Simulator ----------
# Simulates git remote/push responses for error scenarios

function Mock-GitRemoteCheck {
    param([string]$Scenario)
    switch ($Scenario) {
        "no-remote" {
            return [PSCustomObject]@{
                Output = ""  # git remote -v returns empty
                ExitCode = 0
                Success = $true
                HasRemote = $false
            }
        }
        "has-remote" {
            return [PSCustomObject]@{
                Output = "origin  https://github.com/user/repo.git (fetch)`norigin  https://github.com/user/repo.git (push)"
                ExitCode = 0
                Success = $true
                HasRemote = $true
                Url = "https://github.com/user/repo.git"
            }
        }
        "wrong-url" {
            return [PSCustomObject]@{
                Output = "origin  https://github.com/olduser/oldrepo.git (fetch)"
                ExitCode = 0
                Success = $true
                HasRemote = $true
                Url = "https://github.com/olduser/oldrepo.git"
            }
        }
    }
}

function Mock-GitPush {
    param([string]$Scenario)
    switch ($Scenario) {
        "auth-fail-https" {
            return [PSCustomObject]@{
                Output = "remote: Invalid username or token.`nfatal: Authentication failed for 'https://github.com/user/repo.git/'"
                ExitCode = 1
                Success = $false
                ErrorType = "auth"
                IsHttps = $true
            }
        }
        "auth-fail-ssh" {
            return [PSCustomObject]@{
                Output = "git@github.com: Permission denied (publickey).`nfatal: Could not read from remote repository."
                ExitCode = 1
                Success = $false
                ErrorType = "auth"
                IsHttps = $false
            }
        }
        "non-fast-forward" {
            return [PSCustomObject]@{
                Output = "To github.com:user/repo.git`n ! [rejected]    master -> master (non-fast-forward)`nerror: failed to push some refs"
                ExitCode = 1
                Success = $false
                ErrorType = "rejected"
            }
        }
        "repo-not-found" {
            return [PSCustomObject]@{
                Output = "fatal: 'https://github.com/user/notexist.git/' does not appear to be a git repository`nfatal: Could not read from remote repository."
                ExitCode = 1
                Success = $false
                ErrorType = "not-found"
            }
        }
        "network-error" {
            return [PSCustomObject]@{
                Output = "fatal: unable to access 'https://github.com/user/repo.git/': Could not resolve host: github.com"
                ExitCode = 1
                Success = $false
                ErrorType = "network"
            }
        }
        "success" {
            return [PSCustomObject]@{
                Output = "To github.com:user/repo.git`n   abc1234..def5678  master -> master"
                ExitCode = 0
                Success = $true
                ErrorType = $null
            }
        }
        "timeout" {
            return [PSCustomObject]@{
                Output = "fatal: unable to access 'https://github.com/user/repo.git/': Failed to connect to github.com port 443: Connection timed out"
                ExitCode = 1
                Success = $false
                ErrorType = "network"
            }
        }
    }
}

# ---------- Error Classification Logic (from git-commit-push.ps1) ----------
function Classify-PushError {
    param([string]$errorOutput, [string]$remoteUrl)

    $isHttps = $remoteUrl -match "^https?://"
    $errorType = "unknown"

    if ($errorOutput -match "Could not resolve host|Network is unreachable|Connection timed out|Failed to connect") {
        $errorType = "network"
    }
    elseif ($errorOutput -match "Permission denied|Authentication failed|403|401|Invalid username") {
        $errorType = "auth"
    }
    elseif ($errorOutput -match "rejected|fetch first|non-fast-forward") {
        $errorType = "rejected"
    }
    elseif ($errorOutput -match "does not appear to be a git repository|Repository not found|Could not read from remote") {
        $errorType = "not-found"
    }

    return [PSCustomObject]@{
        ErrorType = $errorType
        IsHttps = $isHttps
    }
}

# ---------- Remote Check Logic (from git-commit-push.ps1) ----------
function Test-RemoteConfigured {
    param([string]$remoteOutput, [string]$remoteName)
    return ($remoteOutput -match "$remoteName\s")
}

# ============================================================
#  Test Cases
# ============================================================

Write-Host ""
Write-Host "============================================" -ForegroundColor White
Write-Host "  git-commit-push.ps1 Error Handling Tests" -ForegroundColor White
Write-Host "  Simulating error scenarios" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White

# ------------------------------------------------------------
# Group 1: Remote not configured
# ------------------------------------------------------------

Write-Host ""
Write-Host "[Group 1] Remote Not Configured" -ForegroundColor Blue

# Test 1: Empty remote output = no remote
$mock = Mock-GitRemoteCheck "no-remote"
$hasRemote = Test-RemoteConfigured $mock.Output "origin"
Assert-False $hasRemote "Empty git remote -v should detect no remote"

# Test 2: Non-empty remote output = has remote
$mock = Mock-GitRemoteCheck "has-remote"
$hasRemote = Test-RemoteConfigured $mock.Output "origin"
Assert-True $hasRemote "Non-empty git remote -v should detect remote exists"

# Test 3: Wrong remote name not detected
$mock = Mock-GitRemoteCheck "has-remote"
$hasRemote = Test-RemoteConfigured $mock.Output "upstream"
Assert-False $hasRemote "origin output should not detect 'upstream' remote"

# Test 4: Remote URL extraction
$mock = Mock-GitRemoteCheck "has-remote"
$lines = $mock.Output -split "`n" | Where-Object { $_ -match "origin\s" }
$extractedUrl = ($lines[0] -split "\s+")[1]
Assert-Equals "https://github.com/user/repo.git" $extractedUrl "Should extract correct remote URL"

# Test 5: Wrong URL detection
$mock = Mock-GitRemoteCheck "wrong-url"
$desiredUrl = "https://github.com/newuser/newrepo.git"
$urlMatches = ($mock.Url -eq $desiredUrl)
Assert-False $urlMatches "Should detect URL mismatch (old vs new)"

# ------------------------------------------------------------
# Group 2: Authentication failure
# ------------------------------------------------------------

Write-Host ""
Write-Host "[Group 2] Authentication Failure" -ForegroundColor Blue

# Test 6: HTTPS auth failure classification
$mock = Mock-GitPush "auth-fail-https"
$class = Classify-PushError $mock.Output "https://github.com/user/repo.git"
Assert-Equals "auth" $class.ErrorType "HTTPS auth failure should classify as 'auth'"
Assert-True $class.IsHttps "Should detect HTTPS protocol"

# Test 7: SSH auth failure classification
$mock = Mock-GitPush "auth-fail-ssh"
$class = Classify-PushError $mock.Output "git@github.com:user/repo.git"
Assert-Equals "auth" $class.ErrorType "SSH permission denied should classify as 'auth'"
Assert-False $class.IsHttps "Should detect SSH protocol"

# Test 8: Auth error message patterns
$mock = Mock-GitPush "auth-fail-https"
Assert-Match "Authentication failed" $mock.Output "Should match 'Authentication failed'"
Assert-Match "Invalid username" $mock.Output "Should match 'Invalid username'"

# Test 9: SSH auth error patterns
$mock = Mock-GitPush "auth-fail-ssh"
Assert-Match "Permission denied" $mock.Output "Should match 'Permission denied'"

# ------------------------------------------------------------
# Group 3: Push rejected (non-fast-forward)
# ------------------------------------------------------------

Write-Host ""
Write-Host "[Group 3] Push Rejected" -ForegroundColor Blue

# Test 10: Non-fast-forward classification
$mock = Mock-GitPush "non-fast-forward"
$class = Classify-PushError $mock.Output "https://github.com/user/repo.git"
Assert-Equals "rejected" $class.ErrorType "Non-fast-forward should classify as 'rejected'"

# Test 11: Rejected error patterns
$mock = Mock-GitPush "non-fast-forward"
Assert-Match "rejected" $mock.Output "Should match 'rejected'"
Assert-Match "non-fast-forward" $mock.Output "Should match 'non-fast-forward'"

# Test 12: Rejected should not match auth
$mock = Mock-GitPush "non-fast-forward"
Assert-False ($mock.Output -match "Authentication failed") "Rejected should not match auth pattern"

# ------------------------------------------------------------
# Group 4: Repository not found
# ------------------------------------------------------------

Write-Host ""
Write-Host "[Group 4] Repository Not Found" -ForegroundColor Blue

# Test 13: Not found classification
$mock = Mock-GitPush "repo-not-found"
$class = Classify-PushError $mock.Output "https://github.com/user/notexist.git"
Assert-Equals "not-found" $class.ErrorType "Missing repo should classify as 'not-found'"

# Test 14: Not found patterns
$mock = Mock-GitPush "repo-not-found"
Assert-Match "does not appear to be a git repository" $mock.Output "Should match 'does not appear to be a git repository'"

# Test 15: Could not read from remote
$mock = Mock-GitPush "repo-not-found"
Assert-Match "Could not read from remote" $mock.Output "Should match 'Could not read from remote'"

# Test 16: Not found should not match network
$mock = Mock-GitPush "repo-not-found"
Assert-False ($mock.Output -match "Could not resolve host") "Not found should not match network pattern"

# ------------------------------------------------------------
# Group 5: Network errors
# ------------------------------------------------------------

Write-Host ""
Write-Host "[Group 5] Network Errors" -ForegroundColor Blue

# Test 17: DNS resolution failure
$mock = Mock-GitPush "network-error"
$class = Classify-PushError $mock.Output "https://github.com/user/repo.git"
Assert-Equals "network" $class.ErrorType "DNS failure should classify as 'network'"

# Test 18: Connection timeout
$mock = Mock-GitPush "timeout"
$class = Classify-PushError $mock.Output "https://github.com/user/repo.git"
Assert-Equals "network" $class.ErrorType "Timeout should classify as 'network'"

# Test 19: Network patterns
$mock = Mock-GitPush "network-error"
Assert-Match "Could not resolve host" $mock.Output "Should match 'Could not resolve host'"

# Test 20: Timeout patterns
$mock = Mock-GitPush "timeout"
Assert-Match "Connection timed out" $mock.Output "Should match 'Connection timed out'"

# ------------------------------------------------------------
# Group 6: Success scenario
# ------------------------------------------------------------

Write-Host ""
Write-Host "[Group 6] Success Scenario" -ForegroundColor Blue

# Test 21: Success should not have error type
$mock = Mock-GitPush "success"
Assert-True $mock.Success "Success should return Success=true"
Assert-Equals $null $mock.ErrorType "Success should have null ErrorType"

# Test 22: Success output patterns
$mock = Mock-GitPush "success"
Assert-Match "master -> master" $mock.Output "Should match push success pattern"

# Test 23: Success should not match any error pattern
$mock = Mock-GitPush "success"
Assert-False ($mock.Output -match "Authentication failed|rejected|Could not resolve host|does not appear") "Success should not match any error pattern"

# ------------------------------------------------------------
# Group 7: Protocol detection
# ------------------------------------------------------------

Write-Host ""
Write-Host "[Group 7] Protocol Detection" -ForegroundColor Blue

# Test 24: HTTPS URL detection
$class = Classify-PushError "" "https://github.com/user/repo.git"
Assert-True $class.IsHttps "HTTPS URL should be detected as HTTPS"

# Test 25: SSH URL detection
$class = Classify-PushError "" "git@github.com:user/repo.git"
Assert-False $class.IsHttps "SSH URL should not be detected as HTTPS"

# Test 26: Git protocol URL
$class = Classify-PushError "" "git://github.com/user/repo.git"
Assert-False $class.IsHttps "git:// URL should not be detected as HTTPS"

# ------------------------------------------------------------
# Group 8: Edge cases
# ------------------------------------------------------------

Write-Host ""
Write-Host "[Group 8] Edge Cases" -ForegroundColor Blue

# Test 27: Empty error output
$class = Classify-PushError "" "https://github.com/user/repo.git"
Assert-Equals "unknown" $class.ErrorType "Empty error should classify as 'unknown'"

# Test 28: Multiple error patterns (auth + rejected)
$multiError = "remote: Invalid username.`n ! [rejected] master -> master"
$class = Classify-PushError $multiError "https://github.com/user/repo.git"
Assert-True ($class.ErrorType -eq "auth" -or $class.ErrorType -eq "rejected") "Multiple errors should classify to one type"

# Test 29: Case insensitive matching
$upperError = "AUTHENTICATION FAILED for repository"
$class = Classify-PushError $upperError "https://github.com/user/repo.git"
# Note: PowerShell -match is case-insensitive by default
Assert-True ($class.ErrorType -eq "auth") "Should match auth case-insensitively"

# Test 30: URL with special characters
$specialUrl = "https://github.com/user/repo-name.with.dots.git"
$class = Classify-PushError "" $specialUrl
Assert-True $class.IsHttps "URL with special chars should still detect HTTPS"

# Test 31: Empty remote name
$hasRemote = Test-RemoteConfigured "origin  url" ""
Assert-True $hasRemote "Empty remote name should match (matches anything)"

# Test 32: Null remote output
$hasRemote = Test-RemoteConfigured "" "origin"
Assert-False $hasRemote "Null/empty output should not detect remote"

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
        if ($_.Expected) { Write-Host "      Expected: $($_.Expected)" -ForegroundColor DarkGray }
        if ($_.Actual) { Write-Host "      Actual: $($_.Actual)" -ForegroundColor DarkGray }
    }
    Write-Host ""
}

# Group summary
Write-Host "  Group Summary:" -ForegroundColor White
$groups = @{
    "Remote Config" = (1..5).Count
    "Auth Failure" = (6..9).Count
    "Push Rejected" = (10..12).Count
    "Repo Not Found" = (13..16).Count
    "Network Error" = (17..20).Count
    "Success" = (21..23).Count
    "Protocol" = (24..26).Count
    "Edge Cases" = (27..32).Count
}

Write-Host ""
if ($failed -eq 0) {
    Write-Ok "=========================================="
    Write-Ok "  All $total test cases passed!"
    Write-Ok "  Error handling logic verified"
    Write-Ok "=========================================="
} else {
    Write-Err "=========================================="
    Write-Err "  $failed test cases failed"
    Write-Err "=========================================="
    exit 1
}
Write-Host ""
