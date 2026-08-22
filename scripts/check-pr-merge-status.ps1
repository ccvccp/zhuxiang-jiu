# PR 合并状态诊断脚本(只读, 不修改任何仓库状态)
# 自动判定: 两个 PR 开合状态 / 本地远程分支是否同步 / 3 个 CI 提交是否已进 master
# 按命中场景输出可直接复制执行的合并命令, 避免手动操作失误
#
# 用法(宿主机 PowerShell):
#   $env:GITHUB_TOKEN = "你的PAT"    # Fine-grained PAT, 需 Contents:Read + Pull requests:Read
#   D:\网站架构设计\scripts\check-pr-merge-status.ps1
#
# 不设置环境变量会提示输入。每次网页操作完成后重跑一遍, 会自动进入下一场景。

param(
    [string]$Repo = "ccvcc/zhuxiang-jiu",
    [string]$RepoPath = "D:\网站架构设计",
    [string]$FlashBranch = "feature/flash-sale",
    [string]$AiBranch = "feature/ai-scoring",
    # 3 个 CI 提交(时间正序): 新workflow / pipefail修复 / 失败日志脚本
    [string[]]$CiCommitShas = @("8588e8b", "9ab49a0", "e399bed")
)

$ErrorActionPreference = "Stop"

# ---------- 认证 ----------
$token = $env:GITHUB_TOKEN
if (-not $token) {
    Write-Host "未检测到 GITHUB_TOKEN 环境变量。" -ForegroundColor Yellow
    $token = Read-Host "请输入 GitHub Token(Fine-grained PAT, 需 Contents+Pull requests 读权限)"
}
if (-not $token) { Write-Host "未提供 Token, 退出。" -ForegroundColor Red; exit 1 }

$Headers = @{
    "Authorization" = "Bearer $token"
    "Accept"        = "application/vnd.github+json"
    "User-Agent"    = "pr-merge-status-script"
}
$ApiBase = "https://api.github.com/repos/$Repo"

function Invoke-GhApi {
    param([string]$Uri)
    try {
        return Invoke-RestMethod -Uri $Uri -Headers $Headers -TimeoutSec 30
    }
    catch {
        Write-Host "API 调用失败: $Uri" -ForegroundColor Red
        Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
        $code = 0
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        if ($code -eq 401) { Write-Host "  401 = Token 无效(Fine-grained PAT 需勾选 Contents+Pull requests 读权限)" -ForegroundColor Red }
        if ($code -eq 404) { Write-Host "  404 = 仓库不存在或 Token 无权访问" -ForegroundColor Red }
        exit 1
    }
}

# ---------- 辅助函数 ----------
function Get-BranchSha {
    param([string]$Branch)
    try { (Invoke-GhApi "$ApiBase/git/ref/heads/$Branch").object.sha } catch { $null }
}

function Test-FileOnMaster {
    param([string]$Path)
    try {
        Invoke-RestMethod -Uri "$ApiBase/contents/${Path}?ref=master" -Headers $Headers -TimeoutSec 30 | Out-Null
        return $true
    } catch { return $false }
}

function Get-MasterFileText {
    param([string]$Path)
    try {
        $r = Invoke-RestMethod -Uri "$ApiBase/contents/${Path}?ref=master" -Headers $Headers -TimeoutSec 30
        if ($r.content) {
            return [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String(($r.content -replace "\s", "")))
        }
    } catch { }
    return ""
}

function Get-LocalSha {
    param([string]$Branch)
    if (-not (Test-Path $RepoPath)) { return $null }
    try {
        $out = git -C $RepoPath rev-parse --verify --quiet $Branch 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return $out.Trim() }
    } catch { }
    return $null
}

# ---------- 1. PR 状态 ----------
Write-Host "`n=== 1. 拉取 PR 列表 ===" -ForegroundColor Cyan
$prs = Invoke-GhApi "$ApiBase/pulls?state=all&per_page=100"
$flashPr = @($prs | Where-Object { $_.head.ref -eq $FlashBranch } | Sort-Object created_at -Descending)[0]
$aiPr = @($prs | Where-Object { $_.head.ref -eq $AiBranch } | Sort-Object created_at -Descending)[0]

function Format-PrState($pr) {
    if (-not $pr) { return "未找到 PR" }
    if ($pr.state -eq "open") { return "OPEN(未合并) #$($pr.number)" }
    if ($pr.merged) { return "MERGED(已合并) #$($pr.number)" }
    return "CLOSED(已关闭未合并) #$($pr.number)"
}

# open 的 PR 单独拉详情, 获取最新的可合并状态
$aiMergeable = $null; $aiMergeState = ""
if ($aiPr -and $aiPr.state -eq "open") {
    $d = Invoke-GhApi "$ApiBase/pulls/$($aiPr.number)"
    $aiMergeable = $d.mergeable; $aiMergeState = $d.mergeable_state
}
$flashMergeable = $null
if ($flashPr -and $flashPr.state -eq "open") {
    $d = Invoke-GhApi "$ApiBase/pulls/$($flashPr.number)"
    $flashMergeable = $d.mergeable
}

# ---------- 2. 远程分支 ----------
Write-Host "=== 2. 检查远程分支 ===" -ForegroundColor Cyan
$masterSha = Get-BranchSha "master"
$flashSha = Get-BranchSha $FlashBranch
$aiSha = Get-BranchSha $AiBranch

# ---------- 3. master 内容检查(判定 3 个 CI 提交是否已进 master) ----------
Write-Host "=== 3. 检查 master 上的 CI 提交内容 ===" -ForegroundColor Cyan
$workflowOnMaster = Test-FileOnMaster ".github/workflows/ai-scoring-flashsale-tests.yml"
$scriptOnMaster = Test-FileOnMaster "scripts/get-ci-failure-logs.ps1"
$ciYmlText = Get-MasterFileText ".github/workflows/ci.yml"
$pipefailOk = ($ciYmlText -match "pipefail")
$ciPending = -not ($workflowOnMaster -and $scriptOnMaster -and $pipefailOk)

# ---------- 4. 本地分支 ----------
$localAiSha = Get-LocalSha $AiBranch
$localFlashSha = Get-LocalSha $FlashBranch

# ---------- 打印诊断报告 ----------
Write-Host ""
Write-Host ("=" * 64) -ForegroundColor Green
Write-Host " PR 合并状态诊断报告  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ("=" * 64) -ForegroundColor Green
Write-Host "[1] PR 状态"
Write-Host ("    {0,-14}: {1}" -f $FlashBranch, (Format-PrState $flashPr))
if ($flashPr) { Write-Host ("      $($flashPr.html_url)") -ForegroundColor DarkGray }
Write-Host ("    {0,-14}: {1}" -f $AiBranch, (Format-PrState $aiPr))
if ($aiPr) { Write-Host ("      $($aiPr.html_url)") -ForegroundColor DarkGray }
if ($null -ne $aiMergeable) {
    $mText = "可合并"; if ($aiMergeable -eq $false) { $mText = "有冲突!" }
    Write-Host ("    ai-scoring PR 可合并状态: {0} ({1})" -f $mText, $aiMergeState) -ForegroundColor $(if ($aiMergeable -eq $false) { "Red" } else { "Gray" })
}
Write-Host "[2] 远程分支 SHA"
Write-Host ("    master            = {0}" -f $(if ($masterSha) { $masterSha.Substring(0,7) } else { "不存在" }))
Write-Host ("    {0,-18} = {1}" -f $FlashBranch, $(if ($flashSha) { $flashSha.Substring(0,7) } else { "不存在" }))
Write-Host ("    {0,-18} = {1}" -f $AiBranch, $(if ($aiSha) { $aiSha.Substring(0,7) } else { "不存在" }))
Write-Host "[3] 本地分支 SHA"
if ($localAiSha -and $aiSha) {
    $sync = "与远程一致"; if ($localAiSha -ne $aiSha) { $sync = "!! 与远程不一致(本地有未推送提交)" }
    Write-Host ("    {0} = {1}  ({2})" -f $AiBranch, $localAiSha.Substring(0,7), $sync) -ForegroundColor $(if ($localAiSha -ne $aiSha) { "Yellow" } else { "Gray" })
} else {
    Write-Host ("    {0} = {1}" -f $AiBranch, $(if ($localAiSha) { $localAiSha.Substring(0,7) } else { "本地无此分支" }))
}
Write-Host "[4] 3 个 CI 提交是否已在 master(按文件内容判定, 不受 rebase 改 SHA 影响)"
$mark = { param($ok) if ($ok) { "[已进 master]" } else { "[缺失]" } }
Write-Host ("    新测试流水线 workflow : {0}" -f (& $mark $workflowOnMaster)) -ForegroundColor $(if ($workflowOnMaster) { "Gray" } else { "Yellow" })
Write-Host ("    CI失败日志脚本       : {0}" -f (& $mark $scriptOnMaster)) -ForegroundColor $(if ($scriptOnMaster) { "Gray" } else { "Yellow" })
Write-Host ("    ci.yml pipefail 修复 : {0}" -f (& $mark $pipefailOk)) -ForegroundColor $(if ($pipefailOk) { "Gray" } else { "Yellow" })

# ---------- 决策树 ----------
$aiPrOpen = ($aiPr -and $aiPr.state -eq "open")
$flashPrOpen = ($flashPr -and $flashPr.state -eq "open")
$needPush = ($aiPrOpen -and $localAiSha -and $aiSha -and ($localAiSha -ne $aiSha))

$title = ""
$steps = New-Object System.Collections.Generic.List[string]

if ($needPush) {
    $title = "场景 0: 本地有未推送提交(远程分支落后) → 先推送"
    $steps.Add("git -C $RepoPath push origin $AiBranch")
    $steps.Add("# 推送成功后重新运行本脚本, 会自动进入下一场景")
}
elseif ($aiPrOpen -and $flashPrOpen) {
    $title = "场景 1: 两个 PR 都未合并 → 严格按 flash-sale → ai-scoring 顺序合并"
    $steps.Add("# 第 1 步(网页): 合并 flash-sale PR")
    $steps.Add("#   打开 $($flashPr.html_url) → 等待 Checks 全绿")
    $steps.Add("#   → 点 [Rebase and merge](切勿选 Squash!) → 点 [Delete branch]")
    $steps.Add("# 第 2 步(网页): 合并 ai-scoring PR")
    $steps.Add("#   打开 $($aiPr.html_url) → Ctrl+F5 刷新 → 确认 Commits 已收敛(不含秒杀重复提交)")
    $steps.Add("#   → 等待 Checks 全绿 → 点 [Rebase and merge] → 点 [Delete branch]")
    $steps.Add("# 第 3 步(终端): 本地同步清理")
    $steps.Add("git -C $RepoPath checkout master")
    $steps.Add("git -C $RepoPath pull origin master")
    $steps.Add("git -C $RepoPath branch -d $FlashBranch $AiBranch")
}
elseif ($aiPrOpen) {
    $title = "场景 2: flash-sale 已合并 → 直接合并 ai-scoring PR"
    $steps.Add("# 第 1 步(网页): 打开 $($aiPr.html_url) → 等待 Checks 全绿")
    $steps.Add("#   → 点 [Rebase and merge](切勿选 Squash!) → 点 [Delete branch]")
    $steps.Add("# 第 2 步(终端): 本地同步清理")
    $steps.Add("git -C $RepoPath checkout master")
    $steps.Add("git -C $RepoPath pull origin master")
    $steps.Add("git -C $RepoPath branch -d $FlashBranch $AiBranch")
}
elseif ($ciPending) {
    if ($aiSha) {
        $title = "场景 3: PR 已合并但 3 个 CI 提交未进 master(远程分支还在) → 补小 PR"
        $steps.Add("# 第 1 步(网页): 打开 compare 页建小 PR(GitHub 会自动算出只差这 3 个提交)")
        $steps.Add("#   https://github.com/$Repo/compare/master...$AiBranch")
        $steps.Add("#   → [Create pull request] → 等 Checks 绿 → [Rebase and merge] → [Delete branch]")
        $steps.Add("# 第 2 步(终端): 本地同步清理")
        $steps.Add("git -C $RepoPath checkout master")
        $steps.Add("git -C $RepoPath pull origin master")
        $steps.Add("git -C $RepoPath branch -d $FlashBranch $AiBranch")
    }
    elseif ($localAiSha) {
        $title = "场景 4: PR 已合并且远程分支已删, 3 个 CI 提交未进 master → 本地 cherry-pick 重建"
        $steps.Add("# 第 1 步(终端): 从本地分支摘出 3 个 CI 提交")
        $steps.Add("git -C $RepoPath fetch origin")
        $steps.Add("git -C $RepoPath checkout master")
        $steps.Add("git -C $RepoPath pull origin master")
        $steps.Add("git -C $RepoPath checkout -b ci/final-fixes")
        $steps.Add("git -C $RepoPath cherry-pick $($CiCommitShas -join ' ')")
        $steps.Add("git -C $RepoPath push -u origin ci/final-fixes")
        $steps.Add("# 第 2 步(网页): 打开 https://github.com/$Repo/compare/master...ci/final-fixes")
        $steps.Add("#   → [Create pull request] → 等 Checks 绿 → [Rebase and merge] → [Delete branch]")
        $steps.Add("# 第 3 步(终端): 清理")
        $steps.Add("git -C $RepoPath checkout master")
        $steps.Add("git -C $RepoPath pull origin master")
        $steps.Add("git -C $RepoPath branch -d $FlashBranch $AiBranch ci/final-fixes")
    }
    else {
        $title = "场景 4b: CI 提交未进 master 且本地无分支(异常状态)"
        $steps.Add("# 本地和远程都没有 $AiBranch 分支, 3 个 CI 提交可能丢失")
        $steps.Add("# 请把本报告发给 AI 助手人工分析")
    }
}
else {
    $title = "场景 5: 全部完成(3 个 CI 提交已在 master) → 只剩本地清理"
    $steps.Add("git -C $RepoPath checkout master")
    $steps.Add("git -C $RepoPath pull origin master")
    $steps.Add("git -C $RepoPath branch -d $FlashBranch $AiBranch")
    if ($flashSha) { $steps.Add("git -C $RepoPath push origin --delete $FlashBranch") }
    if ($aiSha) { $steps.Add("git -C $RepoPath push origin --delete $AiBranch") }
    $steps.Add("# 完成后 master 顶部的提交历史即为最终状态")
}

if ($aiPrOpen -and $aiMergeable -eq $false) {
    $title += "  [警告: ai-scoring PR 存在冲突, 合并前需先解决]"
    $steps.Add("# 冲突排查: git -C $RepoPath fetch origin; git -C $RepoPath log --oneline master..origin/$AiBranch")
}

# ---------- 输出建议 ----------
Write-Host ""
Write-Host ("=" * 64) -ForegroundColor Green
Write-Host " 诊断结论: $title" -ForegroundColor $(if ($title -match "警告|异常") { "Red" } else { "Yellow" })
Write-Host ("=" * 64) -ForegroundColor Green
Write-Host " 推荐操作(按顺序执行):"
foreach ($s in $steps) {
    $color = "White"; if ($s.StartsWith("#")) { $color = "DarkCyan" }
    Write-Host "   $s" -ForegroundColor $color
}
Write-Host ("=" * 64) -ForegroundColor Green
Write-Host " 提示: 每完成一步网页操作后, 重新运行本脚本验证状态推进。" -ForegroundColor DarkGray
