# ============================================================
# install-hooks.ps1 - Git Hooks Installer (one-click setup)
# ------------------------------------------------------------
# Usage (after Git for Windows installed):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-hooks.ps1
# Function:
#   1. Check git installed (fail with hint if not)
#   2. Auto-run `git init` if .git dir missing
#   3. Copy pre-commit script to .git\hooks\pre-commit
#   4. Verify hooks ready
# ============================================================

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$hooksDir = Join-Path $repoRoot '.git\hooks'

Write-Host '[install-hooks] Git Hooks Installer' -ForegroundColor Cyan
Write-Host "  Repo root: $repoRoot" -ForegroundColor Gray

# ---------- 1. Check git ----------
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    Write-Host 'FAIL: git not installed. Install Git for Windows first:' -ForegroundColor Red
    Write-Host '  https://git-scm.com/download/win' -ForegroundColor Cyan
    Write-Host '  After install, restart terminal and run:' -ForegroundColor Yellow
    Write-Host "    cd `"$repoRoot`"" -ForegroundColor Gray
    Write-Host '    git init' -ForegroundColor Gray
    Write-Host '    powershell -File scripts\install-hooks.ps1' -ForegroundColor Gray
    exit 1
}
Write-Host "OK: git installed: $($gitCmd.Source)" -ForegroundColor Green

# ---------- 2. Init git repo if needed ----------
if (-not (Test-Path (Join-Path $repoRoot '.git'))) {
    Write-Host 'WARN: .git dir not found, running git init...' -ForegroundColor Yellow
    Push-Location $repoRoot
    try {
        & git init 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'git init failed' }
    } finally { Pop-Location }
    Write-Host 'OK: git repo initialized' -ForegroundColor Green
} else {
    Write-Host 'OK: .git dir exists' -ForegroundColor Green
}

# ---------- 3. Create hooks dir ----------
if (-not (Test-Path $hooksDir)) {
    New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null
}
Write-Host "OK: hooks dir ready: $hooksDir" -ForegroundColor Green

# ---------- 4. Copy pre-commit script (bash version for Git Bash) ----------
$srcScript = Join-Path $PSScriptRoot 'pre-commit'
$dstScript = Join-Path $hooksDir 'pre-commit'

if (-not (Test-Path $srcScript)) {
    Write-Host "FAIL: source script not found: $srcScript" -ForegroundColor Red
    exit 1
}

Copy-Item $srcScript $dstScript -Force
Write-Host "OK: pre-commit installed (bash): $dstScript" -ForegroundColor Green

# ---------- 5. Verify ----------
if (Test-Path $dstScript) {
    Write-Host ''
    Write-Host 'DONE: Git Hooks installed!' -ForegroundColor Green
    Write-Host ''
    Write-Host 'Usage:' -ForegroundColor Cyan
    Write-Host '  * Next git commit auto-triggers CI check' -ForegroundColor Gray
    Write-Host '  * Checks: static (paren/brace/BOM) + CI pipeline confirm' -ForegroundColor Gray
    Write-Host '  * Skip CI: git commit --no-verify' -ForegroundColor Yellow
    Write-Host '  * CI pipeline: browser open module-test.html -> CI Pipeline button' -ForegroundColor Gray
    Write-Host ''
    Write-Host 'Test: modify any zhuxiang-jiu/js/*.js then git commit' -ForegroundColor Yellow
} else {
    Write-Host 'FAIL: install failed' -ForegroundColor Red
    exit 1
}
