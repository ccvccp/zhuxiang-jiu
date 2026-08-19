# ============================================================
# pre-commit.ps1 - Git Pre-commit CI Check (PowerShell)
# ------------------------------------------------------------
# Usage: install via scripts\install-hooks.ps1 (auto-copies to .git\hooks\)
#   or manually copy to .git\hooks\pre-commit (no extension)
# Skip:  git commit --no-verify
# ------------------------------------------------------------
# Checks:
#   1. Staged zhuxiang-jiu JS files (skip if none)
#   2. Static check (paren/brace match, BOM)
#   3. Prompt to run CI pipeline (185 cases) in browser
# ============================================================
param([switch]$SkipCI)

$ErrorActionPreference = 'Stop'
$repoRoot = (git rev-parse --show-toplevel 2>$null)
if (-not $repoRoot) { $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }

Write-Host '[pre-commit] CI check starting...' -ForegroundColor Cyan

# ---------- 1. Get staged JS files ----------
$stagedFiles = git diff --cached --name-only --diff-filter ACM 2>$null
if (-not $stagedFiles -or $stagedFiles.Count -eq 0) {
    Write-Host 'OK: No staged files, passing' -ForegroundColor Green
    exit 0
}

$jsFiles = $stagedFiles | Where-Object { $_ -match '\.js$' -and $_ -match 'zhuxiang-jiu[\\/]js' }
if (-not $jsFiles -or $jsFiles.Count -eq 0 -or $SkipCI) {
    Write-Host 'OK: No zhuxiang-jiu JS changes (or -SkipCI), skip CI' -ForegroundColor Green
    exit 0
}

Write-Host 'JS files changed:' -ForegroundColor Yellow
$jsFiles | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }

# ---------- 2. Static check ----------
$errors = @()
foreach ($f in $jsFiles) {
    $fullPath = Join-Path $repoRoot $f
    if (-not (Test-Path $fullPath)) { continue }
    $content = Get-Content $fullPath -Raw -ErrorAction SilentlyContinue
    if (-not $content) { continue }

    # Paren match (rough)
    $opens = ([regex]::Matches($content, '\(')).Count
    $closes = ([regex]::Matches($content, '\)')).Count
    if ($opens -ne $closes) {
        $errors += "$f : paren mismatch ( $opens vs ) $closes )"
    }

    # Brace match (rough)
    $bOpens = ([regex]::Matches($content, '\{')).Count
    $bCloses = ([regex]::Matches($content, '\}')).Count
    if ($bOpens -ne $bCloses) {
        $errors += "$f : brace mismatch { $bOpens vs } $bCloses"
    }

    # BOM check
    $bytes = [System.IO.File]::ReadAllBytes($fullPath)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $errors += "$f : has UTF-8 BOM, recommend remove"
    }

    # Debug code check (non-test files only)
    if ($f -notmatch '-test\.js$' -and $f -notmatch '-regression') {
        $debugLines = $content -split "`n" | Select-String 'console\.log\('
        if ($debugLines) {
            $count = $debugLines.Count
            Write-Host "  WARN $f : $count console.log found (recommend cleanup in prod)" -ForegroundColor DarkYellow
        }
    }
}

if ($errors.Count -gt 0) {
    Write-Host 'FAIL: Static check failed:' -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Write-Host ''
    Write-Host 'Fix then re: git add + git commit' -ForegroundColor Yellow
    exit 1
}

Write-Host 'OK: Static check passed (paren/brace match, no BOM)' -ForegroundColor Green

# ---------- 3. Prompt to run CI pipeline ----------
Write-Host ''
Write-Host 'Run CI pipeline in browser to confirm no regression:' -ForegroundColor Yellow
Write-Host '  1. Start server: powershell -File scripts\serve-ci.ps1' -ForegroundColor Cyan
Write-Host '  2. Open: http://localhost:8080/module-test.html' -ForegroundColor Cyan
Write-Host '  3. Click [CI Pipeline (6 stages, 185 cases)] button' -ForegroundColor Cyan
Write-Host '  4. Confirm 185/185 PASS (100.0%)' -ForegroundColor Cyan
Write-Host ''
$response = Read-Host 'CI pipeline passed 185/185? (Y/n)'
if ($response -eq 'n' -or $response -eq 'N') {
    Write-Host 'FAIL: CI not passed, commit blocked. Fix and re-commit.' -ForegroundColor Red
    exit 1
}

Write-Host 'OK: CI check passed, commit allowed' -ForegroundColor Green
exit 0
