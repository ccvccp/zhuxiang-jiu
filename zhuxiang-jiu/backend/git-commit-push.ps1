#requires -Version 5.1
# ============================================================
#  Git Commit & Push Script with Auto Remote Setup
#  ------------------------------------------------------------
#  Features:
#    1. Auto-detects/creates remote origin if missing
#    2. Configurable via parameters or interactive prompts
#    3. Handles HTTPS/SSH authentication hints
#    4. Pre-push validation (staged files, branch tracking)
#    5. Retry logic for transient network errors
#    6. Color output with timestamps
#  ------------------------------------------------------------
#  Usage:
#    Interactive:
#      powershell -NoProfile -ExecutionPolicy Bypass -File git-commit-push.ps1
#    Non-interactive (CI):
#      powershell -NoProfile -ExecutionPolicy Bypass -File git-commit-push.ps1 `
#        -RemoteUrl "https://github.com/user/repo.git" `
#        -CommitMessage "feat: add feature" `
#        -RemoteName "origin" `
#        -Branch "master" `
#        -Force
# ============================================================

param(
    [string]$RemoteUrl = "",
    [string]$CommitMessage = "",
    [string]$RemoteName = "origin",
    [string]$Branch = "",
    [int]$MaxRetries = 3,
    [int]$RetryDelay = 5,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$SkipCommit
)

$ErrorActionPreference = "Stop"

# ---------- Color Output ----------
function Write-Step  { param($msg) Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "[ERR]  $msg" -ForegroundColor Red }
function Write-Act   { param($msg) Write-Host "       $msg" -ForegroundColor DarkGray }

# ---------- Git Path Detection ----------
function Get-GitExecutable {
    $gitExe = Get-Command git -ErrorAction SilentlyContinue
    if ($gitExe) { return "git" }

    $paths = @(
        "${env:ProgramFiles}\Git\bin\git.exe",
        "${env:ProgramFiles}\Git\cmd\git.exe",
        "${env:LOCALAPPDATA}\Programs\Git\bin\git.exe"
    )
    foreach ($p in $paths) {
        if (Test-Path $p) { return $p }
    }

    Write-Err "Git not found in PATH or default locations"
    Write-Err "Install from: https://git-scm.com/downloads"
    exit 1
}

# ---------- Git Wrapper ----------
function Invoke-Git {
    param([string[]]$Args, [switch]$SuppressError)

    $prevEAP = $ErrorActionPreference
    if ($SuppressError) { $ErrorActionPreference = "SilentlyContinue" }
    $output = & $script:GitExe @Args 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP

    return [PSCustomObject]@{
        Output = $output
        ExitCode = $exitCode
        Success = ($exitCode -eq 0)
    }
}

# ============================================================
#  Initialization
# ============================================================
$script:GitExe = Get-GitExecutable
Write-Host ""
Write-Host "============================================" -ForegroundColor White
Write-Host "  Git Commit & Push Script" -ForegroundColor White
Write-Host "  Auto remote setup + retry logic" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White
Write-Host ""
Write-Step "Git executable: $script:GitExe"

# Verify we're in a git repo
$repoCheck = Invoke-Git -Args @("rev-parse", "--is-inside-work-tree") -SuppressError
if (-not $repoCheck.Success) {
    Write-Err "Not inside a git repository"
    Write-Err "Run 'git init' first, then re-run this script"
    exit 1
}

# Get current branch
$branchResult = Invoke-Git -Args @("rev-parse", "--abbrev-ref", "HEAD")
$currentBranch = $branchResult.Output.ToString().Trim()
if (-not $Branch) { $Branch = $currentBranch }
Write-Step "Current branch: $currentBranch"
Write-Step "Target branch:  $Branch"
Write-Host ""

# ============================================================
#  Step 1: Check staged files
# ============================================================
Write-Step "[1/5] Checking staged files..."

if (-not $SkipCommit) {
    $stagedResult = Invoke-Git -Args @("diff", "--cached", "--name-only")
    $stagedFiles = $stagedResult.Output | Where-Object { $_ -match "\S" } | ForEach-Object { $_.Trim() }

    if ($stagedFiles.Count -eq 0) {
        Write-Warn "No staged files found"

        # Check for unstaged changes
        $unstagedResult = Invoke-Git -Args @("status", "--porcelain")
        $unstagedFiles = $unstagedResult.Output | Where-Object { $_ -match "\S" }

        if ($unstagedFiles.Count -gt 0) {
            Write-Warn "Unstaged/untracked files detected:"
            $unstagedFiles | Select-Object -First 10 | ForEach-Object { Write-Act $_ }
            if ($unstagedFiles.Count -gt 10) {
                Write-Act "... and $($unstagedFiles.Count - 10) more"
            }
            Write-Host ""
            Write-Warn "Use 'git add <file>' to stage specific files"
            Write-Warn "Or re-run with -SkipCommit to push existing commits only"
        } else {
            Write-Warn "Working tree clean, nothing to commit"
        }

        if (-not $CommitMessage) {
            $CommitMessage = Read-Host "Enter commit message (or press Enter to skip commit)"
        }

        if ($CommitMessage -ne "skip" -and $CommitMessage -ne "") {
            Write-Step "Staging all changes..."
            Invoke-Git -Args @("add", "-A") | Out-Null
            Write-Ok "All changes staged"
        } else {
            $SkipCommit = $true
            Write-Warn "Skipping commit, will push existing commits only"
        }
    } else {
        Write-Ok "$($stagedFiles.Count) file(s) staged:"
        $stagedFiles | Select-Object -First 10 | ForEach-Object { Write-Act $_ }
        if ($stagedFiles.Count -gt 10) {
            Write-Act "... and $($stagedFiles.Count - 10) more"
        }
    }
} else {
    Write-Warn "SkipCommit flag set, skipping staging"
}
Write-Host ""

# ============================================================
#  Step 2: Commit (if not skipped)
# ============================================================
if (-not $SkipCommit) {
    Write-Step "[2/5] Creating commit..."

    # Re-check staged files
    $recheckResult = Invoke-Git -Args @("diff", "--cached", "--name-only")
    $recheckFiles = $recheckResult.Output | Where-Object { $_ -match "\S" }

    if ($recheckFiles.Count -eq 0) {
        Write-Warn "No staged files to commit"
        $SkipCommit = $true
    } else {
        if (-not $CommitMessage) {
            Write-Host ""
            $CommitMessage = Read-Host "Enter commit message"
        }

        if (-not $CommitMessage) {
            Write-Err "Commit message is required"
            exit 1
        }

        if ($DryRun) {
            Write-Warn "DRY RUN: would commit with message: $CommitMessage"
        } else {
            $commitResult = Invoke-Git -Args @("commit", "-m", $CommitMessage)
            if ($commitResult.Success) {
                $commitLine = ($commitResult.Output | Select-String -Pattern "\[master|\[main").ToString()
                Write-Ok "Commit created: $commitLine"
            } else {
                Write-Err "Commit failed"
                $commitResult.Output | ForEach-Object { Write-Err $_ }
                exit 1
            }
        }
    }
} else {
    Write-Step "[2/5] Skipping commit (SkipCommit)"
}
Write-Host ""

# ============================================================
#  Step 3: Check remote configuration
# ============================================================
Write-Step "[3/5] Checking remote configuration..."

$remoteResult = Invoke-Git -Args @("remote", "-v") -SuppressError
$remoteOutput = $remoteResult.Output | Out-String

$remoteExists = $remoteOutput -match "$RemoteName\s"

if ($remoteExists) {
    # Extract existing remote URL
    $remoteLines = $remoteOutput -split "`n" | Where-Object { $_ -match "$RemoteName\s" }
    $existingUrl = ($remoteLines[0] -split "\s+")[1]

    if ($RemoteUrl -and $existingUrl -ne $RemoteUrl) {
        Write-Warn "Remote '$RemoteName' exists but URL differs:"
        Write-Act "  Existing: $existingUrl"
        Write-Act "  Desired:  $RemoteUrl"

        if ($Force -or $(Read-Host "Update remote URL? (y/N)") -eq 'y') {
            Invoke-Git -Args @("remote", "set-url", $RemoteName, $RemoteUrl) | Out-Null
            Write-Ok "Remote URL updated to: $RemoteUrl"
        } else {
            Write-Warn "Keeping existing remote URL: $existingUrl"
            $RemoteUrl = $existingUrl
        }
    } else {
        Write-Ok "Remote '$RemoteName' configured: $existingUrl"
        if (-not $RemoteUrl) { $RemoteUrl = $existingUrl }
    }
} else {
    Write-Warn "Remote '$RemoteName' not found"

    # Prompt for URL if not provided
    if (-not $RemoteUrl) {
        Write-Host ""
        Write-Host "  Common remote URL formats:" -ForegroundColor White
        Write-Host "    GitHub HTTPS: https://github.com/USER/REPO.git" -ForegroundColor DarkGray
        Write-Host "    GitHub SSH:   git@github.com:USER/REPO.git" -ForegroundColor DarkGray
        Write-Host "    GitLab HTTPS: https://gitlab.com/USER/REPO.git" -ForegroundColor DarkGray
        Write-Host "    Gitee HTTPS:  https://gitee.com/USER/REPO.git" -ForegroundColor DarkGray
        Write-Host ""

        $RemoteUrl = Read-Host "Enter remote URL (or 'skip' to skip push)"

        if ($RemoteUrl -eq "skip" -or $RemoteUrl -eq "") {
            Write-Warn "No remote URL provided, skipping push"
            Write-Host ""
            Write-Ok "Commit created successfully (push skipped)"
            Write-Host ""
            Write-Host "  To add remote later:" -ForegroundColor Cyan
            Write-Host "    git remote add origin https://github.com/USER/REPO.git" -ForegroundColor DarkGray
            Write-Host "    git push -u origin $Branch" -ForegroundColor DarkGray
            Write-Host ""
            exit 0
        }
    }

    # Add remote
    if ($DryRun) {
        Write-Warn "DRY RUN: would add remote '$RemoteName' -> $RemoteUrl"
    } else {
        $addRemoteResult = Invoke-Git -Args @("remote", "add", $RemoteName, $RemoteUrl)
        if ($addRemoteResult.Success) {
            Write-Ok "Remote '$RemoteName' added: $RemoteUrl"
        } else {
            Write-Err "Failed to add remote"
            $addRemoteResult.Output | ForEach-Object { Write-Err $_ }
            exit 1
        }
    }
}
Write-Host ""

# ============================================================
#  Step 4: Check upstream tracking
# ============================================================
Write-Step "[4/5] Checking upstream tracking..."

$upstreamResult = Invoke-Git -Args @("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") -SuppressError

if (-not $upstreamResult.Success) {
    Write-Warn "No upstream tracking branch configured"
    Write-Step "Setting upstream: $RemoteName/$Branch"

    if ($DryRun) {
        Write-Warn "DRY RUN: would set upstream to $RemoteName/$Branch"
    } else {
        # First push with -u to set upstream
        Write-Step "First push (sets upstream)..."
        # Handled in Step 5
        $needSetUpstream = $true
    }
} else {
    $upstreamBranch = $upstreamResult.Output.ToString().Trim()
    Write-Ok "Upstream configured: $upstreamBranch"
    $needSetUpstream = $false
}
Write-Host ""

# ============================================================
#  Step 5: Push with retry logic
# ============================================================
Write-Step "[5/5] Pushing to $RemoteName/$Branch..."

if ($DryRun) {
    Write-Warn "DRY RUN: would push to $RemoteName/$Branch"
    if ($needSetUpstream) {
        Write-Act "git push -u $RemoteName $Branch"
    } else {
        Write-Act "git push"
    }
    Write-Host ""
    Write-Ok "Dry run complete"
    exit 0
}

$pushSuccess = $false
$attempt = 0

while (-not $pushSuccess -and $attempt -lt $MaxRetries) {
    $attempt++
    Write-Host ""
    Write-Step "Push attempt $attempt/$MaxRetries..."

    $pushArgs = @("push")
    if ($needSetUpstream) {
        $pushArgs += @("-u", $RemoteName, $Branch)
    }

    $pushResult = Invoke-Git -Args $pushArgs -SuppressError

    if ($pushResult.Success) {
        $pushSuccess = $true
        Write-Ok "Push successful!"
        $pushResult.Output | ForEach-Object {
            if ($_ -match "branch|->|set up|tracking") { Write-Act $_ }
        }
    } else {
        $pushError = $pushResult.Output | Out-String

        # Check for specific error types
        if ($pushError -match "Could not resolve host|Network is unreachable|Connection timed out") {
            Write-Err "Network error: cannot reach remote"
            if ($attempt -lt $MaxRetries) {
                Write-Warn "Retrying in $RetryDelay seconds..."
                Start-Sleep -Seconds $RetryDelay
            }
        }
        elseif ($pushError -match "Permission denied|Authentication failed|403|401") {
            Write-Err "Authentication failed"
            Write-Host ""
            Write-Host "  Fix options:" -ForegroundColor Yellow
            if ($RemoteUrl -match "https://") {
                Write-Host "    1. GitHub requires Personal Access Token (PAT), not password" -ForegroundColor DarkGray
                Write-Host "       Create at: GitHub -> Settings -> Developer settings -> Tokens" -ForegroundColor DarkGray
                Write-Host "    2. Or switch to SSH:" -ForegroundColor DarkGray
                Write-Host "       git remote set-url $RemoteName git@github.com:USER/REPO.git" -ForegroundColor DarkGray
                Write-Host "    3. Or use credential helper:" -ForegroundColor DarkGray
                Write-Host "       git config --global credential.helper store" -ForegroundColor DarkGray
            } else {
                Write-Host "    1. Generate SSH key: ssh-keygen -t ed25519 -C 'email'" -ForegroundColor DarkGray
                Write-Host "    2. Add public key to GitHub: Settings -> SSH and GPG keys" -ForegroundColor DarkGray
                Write-Host "    3. Test: ssh -T git@github.com" -ForegroundColor DarkGray
            }
            exit 1
        }
        elseif ($pushError -match "rejected|fetch first|non-fast-forward") {
            Write-Err "Push rejected: remote has commits not in local"
            Write-Host ""
            Write-Host "  Fix options:" -ForegroundColor Yellow
            Write-Host "    1. Pull and merge:  git pull --rebase $RemoteName $Branch" -ForegroundColor DarkGray
            Write-Host "    2. Force push:      git push --force (WARNING: overwrites remote!)" -ForegroundColor DarkGray
            $choice = Read-Host "Pull rebase (1) or Force push (2) or Abort (3)?"
            switch ($choice) {
                "1" {
                    Write-Step "Pulling with rebase..."
                    $pullResult = Invoke-Git -Args @("pull", "--rebase", $RemoteName, $Branch)
                    if ($pullResult.Success) {
                        Write-Ok "Rebase successful, retrying push..."
                        continue
                    } else {
                        Write-Err "Pull failed, resolve conflicts manually"
                        $pullResult.Output | ForEach-Object { Write-Err $_ }
                        exit 1
                    }
                }
                "2" {
                    Write-Warn "Force pushing (overwrites remote history)..."
                    $forcePushResult = Invoke-Git -Args @("push", "--force", $RemoteName, $Branch)
                    if ($forcePushResult.Success) {
                        $pushSuccess = $true
                        Write-Ok "Force push successful!"
                    } else {
                        Write-Err "Force push failed"
                        $forcePushResult.Output | ForEach-Object { Write-Err $_ }
                        exit 1
                    }
                }
                default {
                    Write-Warn "Push aborted by user"
                    exit 2
                }
            }
        }
        elseif ($pushError -match "does not appear to be a git repository|Repository not found") {
            Write-Err "Remote repository not found"
            Write-Host ""
            Write-Host "  Possible causes:" -ForegroundColor Yellow
            Write-Host "    1. Repository does not exist on remote (create it first)" -ForegroundColor DarkGray
            Write-Host "    2. URL is incorrect" -ForegroundColor DarkGray
            Write-Host "    3. Repository is private and authentication failed" -ForegroundColor DarkGray
            Write-Host ""
            Write-Host "  Current URL: $RemoteUrl" -ForegroundColor Cyan
            $newUrl = Read-Host "Enter correct URL (or Enter to abort)"
            if ($newUrl) {
                Invoke-Git -Args @("remote", "set-url", $RemoteName, $newUrl) | Out-Null
                $RemoteUrl = $newUrl
                Write-Ok "Remote URL updated: $RemoteUrl"
                continue
            }
            exit 1
        }
        else {
            Write-Err "Push failed (unknown error)"
            $pushResult.Output | ForEach-Object { Write-Err $_ }
            if ($attempt -lt $MaxRetries) {
                Write-Warn "Retrying in $RetryDelay seconds..."
                Start-Sleep -Seconds $RetryDelay
            }
        }
    }
}

Write-Host ""

# ============================================================
#  Summary
# ============================================================
Write-Host "============================================" -ForegroundColor White
Write-Host "  Summary" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White
Write-Host ""

if ($pushSuccess) {
    # Get latest commit
    $logResult = Invoke-Git -Args @("log", "--oneline", "-1")
    $latestCommit = $logResult.Output.ToString().Trim()

    # Get remote URL
    $urlResult = Invoke-Git -Args @("remote", "get-url", $RemoteName)
    $finalUrl = $urlResult.Output.ToString().Trim()

    Write-Ok "Push completed successfully!"
    Write-Host ""
    Write-Host "  Branch:   $Branch" -ForegroundColor Cyan
    Write-Host "  Remote:   $RemoteName ($finalUrl)" -ForegroundColor Cyan
    Write-Host "  Commit:   $latestCommit" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  View on GitHub:" -ForegroundColor White
    if ($finalUrl -match "github\.com[/:]([^/]+)/(.+?)(\.git)?$") {
        $user = $matches[1]
        $repo = $matches[2]
        Write-Host "    https://github.com/$user/$repo" -ForegroundColor Cyan
    }
    Write-Host ""

    exit 0
} else {
    Write-Err "Push failed after $MaxRetries attempts"
    Write-Host ""
    Write-Host "  Manual commands to try:" -ForegroundColor Yellow
    Write-Host "    git push -u $RemoteName $Branch" -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}
