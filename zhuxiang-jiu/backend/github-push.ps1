#requires -Version 5.1
# ============================================================
#  GitHub Auto Push Script with PAT Guidance
#  ------------------------------------------------------------
#  Features:
#    1. Auto-detects current remote URL
#    2. Configurable GitHub user/repo via params or interactive
#    3. Push with retry (3 attempts)
#    4. Auth failure detection -> PAT generation guide
#    5. Credential helper auto-config
#    6. Network error retry with backoff
#    7. Color output with timestamps
#  ------------------------------------------------------------
#  Usage:
#    Interactive:
#      powershell -NoProfile -ExecutionPolicy Bypass -File github-push.ps1
#    Non-interactive:
#      powershell -NoProfile -ExecutionPolicy Bypass -File github-push.ps1 `
#        -GitHubUser "ccvcc" -RepoName "zhuxiang-jiu" -UseSsh
#    Force push (with lease, safer than --force):
#      powershell -NoProfile -ExecutionPolicy Bypass -File github-push.ps1 `
#        -GitHubUser "ccvcc" -RepoName "zhuxiang-jiu" -Force
# ============================================================

param(
    [string]$GitHubUser = "",
    [string]$RepoName = "",
    [string]$Branch = "",
    [int]$MaxRetries = 3,
    [int]$RetryDelay = 5,
    [switch]$UseSsh,
    [switch]$UseHttps,
    [switch]$DryRun,
    [switch]$SkipRemoteUpdate,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# ---------- Color Output ----------
function Write-Step  { param($msg) Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "[ERR]  $msg" -ForegroundColor Red }
function Write-Act   { param($msg) Write-Host "       $msg" -ForegroundColor DarkGray }

# ---------- Git Path Detection (exclude WindowsApps) ----------
function Get-GitExecutable {
    # Skip Windows Store alias by checking Program Files first
    $candidates = @(
        "${env:ProgramFiles}\Git\bin\git.exe",
        "${env:ProgramFiles}\Git\cmd\git.exe",
        "${env:LOCALAPPDATA}\Programs\Git\bin\git.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }

    # Fallback to PATH but exclude WindowsApps
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch "WindowsApps" }
    if ($gitCmd) { return $gitCmd.Source }

    Write-Err "Git not found"
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
Write-Host "  GitHub Auto Push Script" -ForegroundColor White
Write-Host "  PAT guidance + retry logic" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White
Write-Host ""
Write-Step "Git: $script:GitExe"

# Verify git repo
$repoCheck = Invoke-Git -Args @("rev-parse", "--is-inside-work-tree") -SuppressError
if (-not $repoCheck.Success) {
    Write-Err "Not inside a git repository"
    exit 1
}

# Get current branch
$branchResult = Invoke-Git -Args @("rev-parse", "--abbrev-ref", "HEAD")
$currentBranch = $branchResult.Output.ToString().Trim()
if (-not $Branch) { $Branch = $currentBranch }
Write-Step "Branch: $Branch"
Write-Host ""

# ============================================================
#  Step 1: Detect / Configure Remote URL
# ============================================================
Write-Step "[1/3] Detecting remote configuration..."

$remoteResult = Invoke-Git -Args @("remote", "get-url", "origin") -SuppressError
$remoteExists = $remoteResult.Success

if ($remoteExists) {
    $currentUrl = $remoteResult.Output.ToString().Trim()
    Write-Ok "Current origin: $currentUrl"

    # Check if it's GitHub
    if ($currentUrl -match "github\.com") {
        Write-Ok "Remote is already GitHub"
        if (-not $SkipRemoteUpdate) {
            # Extract user/repo from URL
            if ($currentUrl -match "github\.com[/:]([^/]+)/(.+?)(\.git)?$") {
                if (-not $GitHubUser) { $GitHubUser = $matches[1] }
                if (-not $RepoName) { $RepoName = $matches[2] }
            }
        }
    } else {
        Write-Warn "Remote is not GitHub: $currentUrl"
        if (-not $GitHubUser) {
            $GitHubUser = Read-Host "Enter GitHub username"
        }
        if (-not $RepoName) {
            $RepoName = Read-Host "Enter repository name"
        }
    }
} else {
    Write-Warn "Remote 'origin' not configured"
    if (-not $GitHubUser) {
        Write-Host ""
        Write-Host "  Example: https://github.com/USER/REPO.git" -ForegroundColor DarkGray
        $GitHubUser = Read-Host "Enter GitHub username"
    }
    if (-not $RepoName) {
        $RepoName = Read-Host "Enter repository name"
    }
}

# Determine protocol
if ($UseSsh) {
    $protocol = "ssh"
} elseif ($UseHttps) {
    $protocol = "https"
} elseif ($remoteExists -and $currentUrl -match "^git@") {
    $protocol = "ssh"
} elseif ($remoteExists -and $currentUrl -match "^https?://") {
    $protocol = "https"
} else {
    # Default: HTTPS (easier for first-time users)
    $protocol = "https"
}

# Build target URL
if ($protocol -eq "ssh") {
    $targetUrl = "git@github.com:${GitHubUser}/${RepoName}.git"
} else {
    $targetUrl = "https://github.com/${GitHubUser}/${RepoName}.git"
}

Write-Host ""
Write-Step "Protocol: $protocol"
Write-Step "Target URL: $targetUrl"

# Update remote if needed
if ($remoteExists) {
    if ($currentUrl -ne $targetUrl) {
        Write-Warn "Updating remote URL..."
        if (-not $DryRun) {
            Invoke-Git -Args @("remote", "set-url", "origin", $targetUrl) | Out-Null
            Write-Ok "Remote URL updated"
        } else {
            Write-Warn "DRY RUN: would update remote URL"
        }
    }
} else {
    Write-Warn "Adding remote 'origin'..."
    if (-not $DryRun) {
        $addResult = Invoke-Git -Args @("remote", "add", "origin", $targetUrl)
        if ($addResult.Success) {
            Write-Ok "Remote 'origin' added"
        } else {
            Write-Err "Failed to add remote"
            exit 1
        }
    } else {
        Write-Warn "DRY RUN: would add remote"
    }
}
Write-Host ""

# ============================================================
#  Step 2: Configure Credential Helper (HTTPS only)
# ============================================================
if ($protocol -eq "https") {
    Write-Step "[2/3] Checking credential helper..."

    $credHelperResult = Invoke-Git -Args @("config", "--global", "credential.helper") -SuppressError
    $credHelper = $credHelperResult.Output.ToString().Trim()

    if (-not $credHelper) {
        Write-Warn "No credential helper configured"
        Write-Step "Setting credential.helper=store (saves PAT after first input)..."
        if (-not $DryRun) {
            Invoke-Git -Args @("config", "--global", "credential.helper", "store") | Out-Null
            Write-Ok "Credential helper configured: store"
        } else {
            Write-Warn "DRY RUN: would set credential.helper=store"
        }
    } else {
        Write-Ok "Credential helper: $credHelper"
    }
} else {
    Write-Step "[2/3] SSH mode, checking SSH key..."

    $sshKeyPath = "$env:USERPROFILE\.ssh\id_ed25519"
    $sshKeyPath2 = "$env:USERPROFILE\.ssh\id_rsa"
    if (Test-Path $sshKeyPath) {
        Write-Ok "SSH key found: id_ed25519"
    } elseif (Test-Path $sshKeyPath2) {
        Write-Ok "SSH key found: id_rsa"
    } else {
        Write-Warn "No SSH key found"
        Write-Host ""
        Write-Host "  Generate SSH key:" -ForegroundColor Yellow
        Write-Host "    ssh-keygen -t ed25519 -C 'your_email@example.com'" -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "  Add public key to GitHub:" -ForegroundColor Yellow
        Write-Host "    1. cat ~/.ssh/id_ed25519.pub" -ForegroundColor DarkGray
        Write-Host "    2. GitHub -> Settings -> SSH and GPG keys -> New SSH key" -ForegroundColor DarkGray
        Write-Host "    3. Paste public key" -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "  Test SSH connection:" -ForegroundColor Yellow
        Write-Host "    ssh -T git@github.com" -ForegroundColor DarkGray
        Write-Host ""
        exit 1
    }
}
Write-Host ""

# ============================================================
#  Step 3: Push with Retry
# ============================================================
Write-Step "[3/3] Pushing to GitHub..."

if ($Force) {
    Write-Warn "Force mode: using --force-with-lease (safer than --force)"
}

if ($DryRun) {
    $dryRunArgs = @("push")
    if ($Force) { $dryRunArgs += "--force-with-lease" }
    $dryRunArgs += "origin/$Branch"
    Write-Warn "DRY RUN: git $($dryRunArgs -join ' ')"
    Write-Host ""
    Write-Ok "Dry run complete"
    exit 0
}

$pushSuccess = $false
$attempt = 0
$authFailed = $false

while (-not $pushSuccess -and $attempt -lt $MaxRetries) {
    $attempt++
    Write-Host ""
    Write-Step "Push attempt $attempt/$MaxRetries..."

    # Check upstream
    $upstreamResult = Invoke-Git -Args @("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") -SuppressError
    $needSetUpstream = -not $upstreamResult.Success

    $pushArgs = @("push")
    if ($Force) { $pushArgs += "--force-with-lease" }
    if ($needSetUpstream) {
        $pushArgs += @("-u", "origin", $Branch)
    }

    $pushResult = Invoke-Git -Args $pushArgs -SuppressError
    $pushError = $pushResult.Output | Out-String

    if ($pushResult.Success) {
        $pushSuccess = $true
        Write-Ok "Push successful!"
        $pushResult.Output | ForEach-Object {
            if ($_ -match "branch|->|set up|tracking") { Write-Act $_ }
        }
    } else {
        # Classify error
        if ($pushError -match "Could not resolve host|Network is unreachable|Connection timed out|Failed to connect") {
            Write-Err "Network error: cannot reach github.com"
            if ($attempt -lt $MaxRetries) {
                Write-Warn "Retrying in $RetryDelay seconds..."
                Start-Sleep -Seconds $RetryDelay
            }
        }
        elseif ($pushError -match "Permission denied|Authentication failed|403|401|Invalid username|could not read Username") {
            $authFailed = $true
            Write-Err "Authentication failed"
            Write-Host ""
            Write-Host "============================================" -ForegroundColor Yellow
            Write-Host "  GitHub Authentication Guide" -ForegroundColor Yellow
            Write-Host "============================================" -ForegroundColor Yellow
            Write-Host ""

            if ($protocol -eq "https") {
                Write-Host "  GitHub requires Personal Access Token (PAT), not password." -ForegroundColor White
                Write-Host ""
                Write-Host "  Step 1: Generate PAT" -ForegroundColor Cyan
                Write-Host "    a. Open: https://github.com/settings/tokens" -ForegroundColor DarkGray
                Write-Host "    b. Click: 'Generate new token (classic)'" -ForegroundColor DarkGray
                Write-Host "    c. Note: zhuxiang-push-token" -ForegroundColor DarkGray
                Write-Host "    d. Expiration: 90 days (or custom)" -ForegroundColor DarkGray
                Write-Host "    e. Scopes: Check 'repo' (full repo access)" -ForegroundColor DarkGray
                Write-Host "    f. Click: 'Generate token'" -ForegroundColor DarkGray
                Write-Host "    g. COPY token immediately (won't show again)" -ForegroundColor DarkGray
                Write-Host "       Format: ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" -ForegroundColor DarkGray
                Write-Host ""
                Write-Host "  Step 2: Push again" -ForegroundColor Cyan
                Write-Host "    When prompted:" -ForegroundColor DarkGray
                Write-Host "      Username: $GitHubUser" -ForegroundColor DarkGray
                Write-Host "      Password: [paste PAT token]" -ForegroundColor DarkGray
                Write-Host ""
                Write-Host "  Step 3: Token is saved by credential.helper=store" -ForegroundColor Cyan
                Write-Host "    Future pushes won't ask for credentials" -ForegroundColor DarkGray
                Write-Host ""
                Write-Host "  Or use Git Credential Manager:" -ForegroundColor Cyan
                Write-Host "    git config --global credential.helper manager" -ForegroundColor DarkGray
                Write-Host "    (opens browser for OAuth login)" -ForegroundColor DarkGray
                Write-Host ""
                Write-Host "  Or switch to SSH:" -ForegroundColor Cyan
                Write-Host "    git remote set-url origin git@github.com:${GitHubUser}/${RepoName}.git" -ForegroundColor DarkGray
                Write-Host "    (requires SSH key setup)" -ForegroundColor DarkGray
            } else {
                Write-Host "  SSH authentication failed." -ForegroundColor White
                Write-Host ""
                Write-Host "  Fix:" -ForegroundColor Cyan
                Write-Host "    1. Generate key: ssh-keygen -t ed25519 -C 'email'" -ForegroundColor DarkGray
                Write-Host "    2. Copy public key:" -ForegroundColor DarkGray
                Write-Host "       Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub | Set-Clipboard" -ForegroundColor DarkGray
                Write-Host "    3. Add to GitHub: Settings -> SSH and GPG keys -> New SSH key" -ForegroundColor DarkGray
                Write-Host "    4. Test: ssh -T git@github.com" -ForegroundColor DarkGray
            }
            Write-Host ""
            Write-Host "============================================" -ForegroundColor Yellow
            break  # Don't retry auth errors
        }
        elseif ($pushError -match "rejected|fetch first|non-fast-forward") {
            if ($Force) {
                # --force-with-lease was rejected: remote has new commits since last fetch
                # This is the SAFETY feature - do NOT escalate to plain --force
                Write-Err "Force-with-lease rejected: remote has newer commits than expected"
                Write-Host ""
                Write-Host "  --force-with-lease protects against overwriting others' work." -ForegroundColor Yellow
                Write-Host "  Remote has commits you don't have locally." -ForegroundColor Yellow
                Write-Host ""
                Write-Host "  Recommended fix:" -ForegroundColor Cyan
                Write-Host "    1. git fetch origin" -ForegroundColor DarkGray
                Write-Host "    2. git log HEAD..origin/$Branch --oneline (see remote commits)" -ForegroundColor DarkGray
                Write-Host "    3. git pull --rebase origin $Branch" -ForegroundColor DarkGray
                Write-Host "    4. Re-run push (with or without -Force)" -ForegroundColor DarkGray
                break
            }
            Write-Err "Push rejected: remote has newer commits"
            Write-Host ""
            Write-Host "  Fix options:" -ForegroundColor Yellow
            Write-Host "    1. Pull and rebase:  git pull --rebase origin $Branch" -ForegroundColor DarkGray
            Write-Host "    2. Force push:       git push --force origin $Branch" -ForegroundColor DarkGray
            Write-Host "       (WARNING: overwrites remote history!)" -ForegroundColor DarkGray
            $choice = Read-Host "Pull rebase (1) / Force push (2) / Abort (3)?"
            switch ($choice) {
                "1" {
                    Write-Step "Pulling with rebase..."
                    $pullResult = Invoke-Git -Args @("pull", "--rebase", "origin", $Branch)
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
                    Write-Warn "Force pushing..."
                    $forceResult = Invoke-Git -Args @("push", "--force", "origin", $Branch)
                    if ($forceResult.Success) {
                        $pushSuccess = $true
                        Write-Ok "Force push successful!"
                    } else {
                        Write-Err "Force push failed"
                        $forceResult.Output | ForEach-Object { Write-Err $_ }
                        exit 1
                    }
                }
                default {
                    Write-Warn "Push aborted"
                    exit 2
                }
            }
        }
        elseif ($pushError -match "does not appear to be a git repository|Repository not found|404") {
            Write-Err "Repository not found on GitHub"
            Write-Host ""
            Write-Host "  Possible causes:" -ForegroundColor Yellow
            Write-Host "    1. Repository does not exist" -ForegroundColor DarkGray
            Write-Host "       Create at: https://github.com/new" -ForegroundColor DarkGray
            Write-Host "       Name: $RepoName" -ForegroundColor DarkGray
            Write-Host "       Visibility: Private or Public" -ForegroundColor DarkGray
            Write-Host "       DO NOT initialize with README (causes conflict)" -ForegroundColor DarkGray
            Write-Host ""
            Write-Host "    2. URL is incorrect" -ForegroundColor DarkGray
            Write-Host "       Current: $targetUrl" -ForegroundColor DarkGray
            Write-Host "       Check: https://github.com/$GitHubUser" -ForegroundColor DarkGray
            Write-Host ""
            Write-Host "    3. Repository is private and auth failed" -ForegroundColor DarkGray
            break
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
    $logResult = Invoke-Git -Args @("log", "--oneline", "-1")
    $latestCommit = $logResult.Output.ToString().Trim()

    Write-Ok "Push to GitHub successful!"
    Write-Host ""
    Write-Host "  Branch:   $Branch" -ForegroundColor Cyan
    Write-Host "  Remote:   origin ($targetUrl)" -ForegroundColor Cyan
    Write-Host "  Commit:   $latestCommit" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  View on GitHub:" -ForegroundColor White
    Write-Host "    https://github.com/$GitHubUser/$RepoName" -ForegroundColor Cyan
    Write-Host ""
    exit 0
} elseif ($authFailed) {
    Write-Warn "Authentication failed - follow the guide above"
    Write-Host ""
    Write-Host "  After generating PAT, re-run:" -ForegroundColor Yellow
    Write-Host "    powershell -File github-push.ps1 -GitHubUser '$GitHubUser' -RepoName '$RepoName'" -ForegroundColor DarkGray
    Write-Host ""
    exit 1
} else {
    Write-Err "Push failed after $MaxRetries attempts"
    Write-Host ""
    Write-Host "  Manual debug:" -ForegroundColor Yellow
    Write-Host "    git remote -v" -ForegroundColor DarkGray
    Write-Host "    git push -u origin $Branch" -ForegroundColor DarkGray
    Write-Host "    git push -v origin $Branch 2>&1 (verbose)" -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}
