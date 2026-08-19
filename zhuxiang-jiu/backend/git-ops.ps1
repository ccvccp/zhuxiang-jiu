#requires -Version 5.1
# ============================================================
#  Git Operations Unified Script
#  ------------------------------------------------------------
#  Actions:
#    push     - Push to GitHub (delegates to github-push.ps1)
#    pull     - Pull with rebase
#    fetch    - Fetch all branches
#    branch   - List branches
#    stash    - Stash operations (save/pop/list/drop)
#    diff     - Show diff (staged/unstaged)
#    status   - Working tree status
#    log      - Recent commits
#    sync     - Fetch + Pull + Push (full sync)
#  ------------------------------------------------------------
#  Usage:
#    powershell -File git-ops.ps1 -Action push
#    powershell -File git-ops.ps1 -Action pull
#    powershell -File git-ops.ps1 -Action stash -StashAction save
#    powershell -File git-ops.ps1 -Action sync -GitHubUser ccvcc -RepoName zhuxiang-jiu
# ============================================================

param(
    [Parameter(Position=0)]
    [ValidateSet("push","pull","fetch","branch","stash","diff","status","log","sync","help")]
    [string]$Action = "help",

    # Push/Sync params (delegated to github-push.ps1)
    [string]$GitHubUser = "",
    [string]$RepoName = "",
    [string]$Branch = "",
    [switch]$UseSsh,
    [switch]$UseHttps,
    [switch]$DryRun,
    [switch]$Force,

    # Stash params
    [ValidateSet("save","pop","list","drop","apply")]
    [string]$StashAction = "save",
    [string]$StashMessage = "",

    # Diff params
    [ValidateSet("staged","unstaged","stat","name-only")]
    [string]$DiffMode = "unstaged",

    # Log params
    [int]$LogCount = 10,

    # Pull params
    [switch]$UseMerge,

    # General
    [int]$MaxRetries = 3,
    [int]$RetryDelay = 5
)

$ErrorActionPreference = "Stop"

# ---------- Color Output ----------
function Write-Step { param($msg) Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err  { param($msg) Write-Host "[ERR]  $msg" -ForegroundColor Red }

# ---------- Git Path Detection ----------
function Get-GitExecutable {
    # Priority 1: Known install paths (avoid Windows Store alias)
    $candidates = @(
        "C:\Program Files\Git\bin\git.exe",
        "C:\Program Files\Git\cmd\git.exe",
        "C:\Program Files (x86)\Git\bin\git.exe",
        "$env:LOCALAPPDATA\Programs\Git\bin\git.exe",
        "$env:ProgramFiles\Git\bin\git.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    # Priority 2: Get-Command, excluding Windows Store alias
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch "WindowsApps" }
    if ($gitCmd) { return $gitCmd.Source }
    Write-Err "Git not found"
    exit 1
}

# ---------- Git Wrapper ----------
function Invoke-Git {
    param([string[]]$GitArgs, [switch]$SuppressError)
    $prevEAP = $ErrorActionPreference
    if ($SuppressError) { $ErrorActionPreference = "SilentlyContinue" }
    $output = & $script:GitExe @GitArgs 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    return [PSCustomObject]@{
        Output = $output
        ExitCode = $exitCode
        Success = ($exitCode -eq 0)
    }
}

# ---------- Check Git Repo ----------
function Test-GitRepo {
    $check = Invoke-Git -GitArgs @("rev-parse","--is-inside-work-tree") -SuppressError
    if (-not $check.Success) {
        Write-Err "Not inside a git repository"
        exit 1
    }
}

# ---------- Get Current Branch ----------
function Get-CurrentBranch {
    $result = Invoke-Git -GitArgs @("rev-parse","--abbrev-ref","HEAD")
    return $result.Output.ToString().Trim()
}

# ============================================================
#  Action: Help
# ============================================================
function Show-Help {
    Write-Host ""
    Write-Host "Git Operations Unified Script" -ForegroundColor White
    Write-Host "============================================" -ForegroundColor White
    Write-Host ""
    Write-Host "Usage: git-ops.ps1 -Action <action> [options]"
    Write-Host ""
    Write-Host "Actions:"
    Write-Host "  push     Push to GitHub (with PAT guidance)"
    Write-Host "  pull     Pull latest from origin (--rebase by default)"
    Write-Host "  fetch    Fetch all branches (no merge)"
    Write-Host "  branch   List all branches (local + remote)"
    Write-Host "  stash    Stash operations (-StashAction save/pop/list/drop/apply)"
    Write-Host "  diff     Show diff (-DiffMode staged/unstaged/stat/name-only)"
    Write-Host "  status   Working tree status"
    Write-Host "  log      Recent commits (-LogCount N, default 10)"
    Write-Host "  sync     Fetch + Pull + Push (full sync)"
    Write-Host "  help     Show this help"
    Write-Host ""
    Write-Host "Common Options:"
    Write-Host "  -GitHubUser <user>     GitHub username (push/sync)"
    Write-Host "  -RepoName <repo>       Repository name (push/sync)"
    Write-Host "  -Branch <branch>       Target branch (default: current)"
    Write-Host "  -UseSsh                Use SSH protocol"
    Write-Host "  -UseHttps              Use HTTPS protocol"
    Write-Host "  -DryRun                Simulate without executing"
    Write-Host "  -UseMerge              Use merge instead of rebase (pull)"
    Write-Host "  -Force                 Force push with --force-with-lease (safer)"
    Write-Host "  -StashAction <action>  save/pop/list/drop/apply"
    Write-Host "  -DiffMode <mode>       staged/unstaged/stat/name-only"
    Write-Host "  -LogCount <n>          Number of commits (default 10)"
    Write-Host ""
    Write-Host "Examples:"
    Write-Host '  powershell -File git-ops.ps1 -Action push -GitHubUser ccvcc -RepoName zhuxiang-jiu'
    Write-Host '  powershell -File git-ops.ps1 -Action pull'
    Write-Host '  powershell -File git-ops.ps1 -Action stash -StashAction pop'
    Write-Host '  powershell -File git-ops.ps1 -Action diff -DiffMode staged'
    Write-Host '  powershell -File git-ops.ps1 -Action sync -GitHubUser ccvcc -RepoName zhuxiang-jiu'
    Write-Host '  powershell -File git-ops.ps1 -Action push -GitHubUser ccvcc -RepoName zhuxiang-jiu -Force'
    Write-Host ""
}

# ============================================================
#  Action: Push (delegate to github-push.ps1)
# ============================================================
function Do-Push {
    Write-Step "Action: push (delegating to github-push.ps1)..."

    $pushScript = Join-Path $script:ScriptDir "github-push.ps1"
    if (-not (Test-Path $pushScript)) {
        Write-Err "github-push.ps1 not found at: $pushScript"
        exit 1
    }

    $pushArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $pushScript)
    if ($GitHubUser) { $pushArgs += @("-GitHubUser", $GitHubUser) }
    if ($RepoName)   { $pushArgs += @("-RepoName", $RepoName) }
    if ($Branch)     { $pushArgs += @("-Branch", $Branch) }
    if ($UseSsh)     { $pushArgs += "-UseSsh" }
    if ($UseHttps)   { $pushArgs += "-UseHttps" }
    if ($DryRun)     { $pushArgs += "-DryRun" }
    if ($Force)      { $pushArgs += "-Force" }

    $pushArgs += @("-MaxRetries", $MaxRetries, "-RetryDelay", $RetryDelay)

    & powershell @pushArgs
    exit $LASTEXITCODE
}

# ============================================================
#  Action: Pull
# ============================================================
function Do-Pull {
    Write-Step "Action: pull"
    $currentBranch = Get-CurrentBranch
    Write-Step "Branch: $currentBranch"

    if ($DryRun) {
        $pullMode = if ($UseMerge) { 'origin' } else { '--rebase origin' }
        Write-Warn "DRY RUN: git pull $pullMode $currentBranch"
        return
    }

    $pullArgs = @("pull")
    if (-not $UseMerge) { $pullArgs += "--rebase" }
    $pullArgs += @("origin", $currentBranch)

    Write-Step "Executing: git $($pullArgs -join ' ')"
    $result = Invoke-Git -GitArgs $pullArgs

    if ($result.Success) {
        Write-Ok "Pull successful"
        $result.Output | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    } else {
        $errorText = $result.Output | Out-String
        if ($errorText -match "conflict") {
            Write-Err "Merge conflict detected!"
            Write-Host ""
            Write-Host "  Resolve conflicts:" -ForegroundColor Yellow
            Write-Host "    1. git status (see conflicted files)" -ForegroundColor DarkGray
            Write-Host "    2. Edit files, resolve <<<< ==== >>>>" -ForegroundColor DarkGray
            Write-Host "    3. git add <file> (mark resolved)" -ForegroundColor DarkGray
            Write-Host "    4. git rebase --continue (or git merge --continue)" -ForegroundColor DarkGray
            Write-Host "    5. Or abort: git rebase --abort" -ForegroundColor DarkGray
        } elseif ($errorText -match "Authentication failed|Permission denied") {
            Write-Err "Authentication failed"
            Write-Host "  Run: git-ops.ps1 -Action push (to configure PAT)" -ForegroundColor Yellow
        } else {
            Write-Err "Pull failed"
            $result.Output | ForEach-Object { Write-Err "  $_" }
        }
        exit 1
    }
}

# ============================================================
#  Action: Fetch
# ============================================================
function Do-Fetch {
    Write-Step "Action: fetch (all branches, prune)"

    if ($DryRun) {
        Write-Warn "DRY RUN: git fetch --all --prune"
        return
    }

    $result = Invoke-Git -GitArgs @("fetch","--all","--prune")
    if ($result.Success) {
        Write-Ok "Fetch successful"
        $result.Output | Where-Object { $_ -match "\S" } | ForEach-Object {
            Write-Host "  $_" -ForegroundColor DarkGray
        }
    } else {
        Write-Err "Fetch failed"
        $result.Output | ForEach-Object { Write-Err "  $_" }
        exit 1
    }
}

# ============================================================
#  Action: Branch
# ============================================================
function Do-Branch {
    Write-Step "Action: branch (local + remote)"

    $result = Invoke-Git -GitArgs @("branch","-a","-vv")
    if ($result.Success) {
        Write-Ok "Branches:"
        $result.Output | ForEach-Object {
            if ($_ -match "^\*") {
                Write-Host "  $_" -ForegroundColor Green
            } else {
                Write-Host "  $_" -ForegroundColor DarkGray
            }
        }
    } else {
        Write-Err "Branch listing failed"
        exit 1
    }
}

# ============================================================
#  Action: Stash
# ============================================================
function Do-Stash {
    Write-Step "Action: stash ($StashAction)"

    switch ($StashAction) {
        "save" {
            if (-not $StashMessage) {
                $StashMessage = "WIP-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            }
            Write-Step "Saving stash: $StashMessage"
            if ($DryRun) { Write-Warn "DRY RUN: git stash save '$StashMessage'"; return }
            $result = Invoke-Git -GitArgs @("stash","save",$StashMessage)
            if ($result.Success) {
                Write-Ok "Stash saved: $StashMessage"
            } else {
                $msg = $result.Output | Out-String
                if ($msg -match "No local changes to save") {
                    Write-Warn "No local changes to stash"
                } else {
                    Write-Err "Stash save failed"
                    exit 1
                }
            }
        }
        "pop" {
            Write-Step "Popping latest stash..."
            if ($DryRun) { Write-Warn "DRY RUN: git stash pop"; return }
            $result = Invoke-Git -GitArgs @("stash","pop")
            if ($result.Success) {
                Write-Ok "Stash popped successfully"
                $result.Output | Where-Object { $_ -match "\S" } | ForEach-Object {
                    Write-Host "  $_" -ForegroundColor DarkGray
                }
            } else {
                $msg = $result.Output | Out-String
                if ($msg -match "conflict") {
                    Write-Err "Stash pop caused conflicts!"
                    Write-Host "  Resolve with: git status, edit, git add, git commit" -ForegroundColor Yellow
                } else {
                    Write-Err "Stash pop failed"
                    exit 1
                }
            }
        }
        "list" {
            $result = Invoke-Git -GitArgs @("stash","list")
            if ($result.Success) {
                Write-Ok "Stash list:"
                $list = $result.Output | Where-Object { $_ -match "\S" }
                if (-not $list) {
                    Write-Host "  (no stashes)" -ForegroundColor DarkGray
                } else {
                    $list | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
                }
            }
        }
        "drop" {
            Write-Step "Dropping latest stash..."
            if ($DryRun) { Write-Warn "DRY RUN: git stash drop"; return }
            $result = Invoke-Git -GitArgs @("stash","drop")
            if ($result.Success) {
                Write-Ok "Stash dropped"
            } else {
                Write-Err "Stash drop failed"
                exit 1
            }
        }
        "apply" {
            Write-Step "Applying latest stash (keeping stash)..."
            if ($DryRun) { Write-Warn "DRY RUN: git stash apply"; return }
            $result = Invoke-Git -GitArgs @("stash","apply")
            if ($result.Success) {
                Write-Ok "Stash applied (still in stash list)"
            } else {
                Write-Err "Stash apply failed"
                exit 1
            }
        }
    }
}

# ============================================================
#  Action: Diff
# ============================================================
function Do-Diff {
    Write-Step "Action: diff ($DiffMode)"

    $diffArgs = switch ($DiffMode) {
        "staged"    { @("diff","--cached") }
        "unstaged"  { @("diff") }
        "stat"      { @("diff","--stat") }
        "name-only" { @("diff","--name-only") }
    }

    if ($DryRun) {
        Write-Warn "DRY RUN: git $($diffArgs -join ' ')"
        return
    }

    $result = Invoke-Git -GitArgs $diffArgs
    if ($result.Success) {
        $output = $result.Output | Where-Object { $_ -match "\S" }
        if (-not $output) {
            Write-Host "  (no changes)" -ForegroundColor DarkGray
        } else {
            Write-Ok "Diff ($DiffMode):"
            $output | ForEach-Object {
                if ($_ -match "^\+") { Write-Host "  $_" -ForegroundColor Green }
                elseif ($_ -match "^-") { Write-Host "  $_" -ForegroundColor Red }
                else { Write-Host "  $_" -ForegroundColor DarkGray }
            }
        }
    } else {
        Write-Err "Diff failed"
        exit 1
    }
}

# ============================================================
#  Action: Status
# ============================================================
function Do-Status {
    Write-Step "Action: status"

    $result = Invoke-Git -GitArgs @("status")
    if ($result.Success) {
        Write-Ok "Working tree status:"
        $result.Output | ForEach-Object {
            if ($_ -match "Changes to be committed") {
                Write-Host "  $_" -ForegroundColor Green
            } elseif ($_ -match "Changes not staged|Untracked") {
                Write-Host "  $_" -ForegroundColor Yellow
            } elseif ($_ -match "modified:|new file:|deleted:") {
                Write-Host "  $_" -ForegroundColor Cyan
            } else {
                Write-Host "  $_" -ForegroundColor DarkGray
            }
        }
    } else {
        Write-Err "Status failed"
        exit 1
    }
}

# ============================================================
#  Action: Log
# ============================================================
function Do-Log {
    Write-Step "Action: log (recent $LogCount)"

    $result = Invoke-Git -GitArgs @("log","--oneline","-$LogCount","--graph","--decorate")
    if ($result.Success) {
        Write-Ok "Recent $LogCount commits:"
        $result.Output | ForEach-Object {
            Write-Host "  $_" -ForegroundColor DarkGray
        }
    } else {
        Write-Err "Log failed"
        exit 1
    }
}

# ============================================================
#  Action: Sync (Fetch + Pull + Push)
# ============================================================
function Do-Sync {
    Write-Step "Action: sync (fetch + pull + push)"
    Write-Host ""

    # Step 1: Fetch
    Write-Step "[1/3] Fetching from remote..."
    if ($DryRun) {
        Write-Warn "DRY RUN: git fetch --all --prune"
    } else {
        $fetchResult = Invoke-Git -GitArgs @("fetch","--all","--prune")
        if ($fetchResult.Success) {
            Write-Ok "Fetch complete"
        } else {
            Write-Err "Fetch failed, aborting sync"
            exit 1
        }
    }
    Write-Host ""

    # Step 2: Pull
    Write-Step "[2/3] Pulling with rebase..."
    if ($DryRun) {
        Write-Warn "DRY RUN: git pull --rebase"
    } else {
        $currentBranch = Get-CurrentBranch
        $pullResult = Invoke-Git -GitArgs @("pull","--rebase","origin",$currentBranch)
        if ($pullResult.Success) {
            $pullMsg = $pullResult.Output | Out-String
            if ($pullMsg -match "Already up to date|up to date") {
                Write-Ok "Already up to date"
            } else {
                Write-Ok "Pull complete (new commits applied)"
                $pullResult.Output | Where-Object { $_ -match "\S" } | ForEach-Object {
                    Write-Host "  $_" -ForegroundColor DarkGray
                }
            }
        } else {
            $errorText = $pullResult.Output | Out-String
            if ($errorText -match "conflict") {
                Write-Err "Pull conflict! Resolve and re-run sync"
                exit 1
            } else {
                Write-Warn "Pull failed (may not have upstream), continuing to push..."
            }
        }
    }
    Write-Host ""

    # Step 3: Push
    Write-Step "[3/3] Pushing to remote..."
    if ($DryRun) {
        Write-Warn "DRY RUN: would call push action"
    } else {
        # Delegate to push by calling this script recursively
        $pushArgs = @("-NoProfile","-ExecutionPolicy","Bypass","-File",$PSCommandPath,"-Action","push")
        if ($GitHubUser) { $pushArgs += @("-GitHubUser",$GitHubUser) }
        if ($RepoName)   { $pushArgs += @("-RepoName",$RepoName) }
        if ($Branch)     { $pushArgs += @("-Branch",$Branch) }
        if ($UseSsh)     { $pushArgs += "-UseSsh" }
        if ($UseHttps)   { $pushArgs += "-UseHttps" }
        if ($Force)      { $pushArgs += "-Force" }

        & powershell @pushArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Push failed during sync"
            exit 1
        }
    }
    Write-Host ""
    Write-Ok "Sync complete (fetch + pull + push)"
}

# ============================================================
#  Main
# ============================================================
$script:GitExe = Get-GitExecutable
$script:ScriptDir = Split-Path -Parent $PSCommandPath

Write-Host ""
Write-Host "============================================" -ForegroundColor White
Write-Host "  Git Operations: $Action" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White
Write-Host ""

Test-GitRepo

switch ($Action) {
    "push"    { Do-Push }
    "pull"    { Do-Pull }
    "fetch"   { Do-Fetch }
    "branch"  { Do-Branch }
    "stash"   { Do-Stash }
    "diff"    { Do-Diff }
    "status"  { Do-Status }
    "log"     { Do-Log }
    "sync"    { Do-Sync }
    "help"    { Show-Help }
    default   { Write-Err "Unknown action: $Action"; Show-Help; exit 1 }
}

Write-Host ""
