# 从 GitHub Actions 拉取失败 job 日志并提取关键报错行
# 适用于: CI 跑完后快速定位失败原因, 输出可直接粘贴给 AI 分析
#
# 用法(在宿主机 PowerShell 执行):
#   .\scripts\get-ci-failure-logs.ps1                    # 分析最近一次失败的 run
#   .\scripts\get-ci-failure-logs.ps1 -Limit 5           # 扫描最近 5 次 run(默认 3)
#   .\scripts\get-ci-failure-logs.ps1 -RunId 1234567890  # 分析指定 run
#   .\scripts\get-ci-failure-logs.ps1 -Workflow ci.yml   # 只看指定 workflow(默认全部)
#
# 前提:
#   1. 环境变量 GITHUB_TOKEN 已设置(需 repo/actions 读权限), 或脚本会提示输入
#      (就是你推送用的 Fine-grained PAT, 需勾选 Actions: Read)
#   2. PowerShell 5.1 兼容(UTF-8 BOM + CRLF)
#
# 输出:
#   控制台: 失败 job 列表 + 每个 job 的关键报错行(前后文 2 行)
#   文件:   ci-failure-report.md(与脚本同目录, 可直接整个发给别人分析)

param(
    [string]$Repo = "zhuxiang-jiu-dev/zhuxiang-jiu",
    [string]$Workflow = "",
    [int]$Limit = 3,
    [int64]$RunId = 0,
    [int]$ContextLines = 2
)

$ErrorActionPreference = "Stop"

# ---------- 认证 ----------
$token = $env:GITHUB_TOKEN
if (-not $token) {
    Write-Host "未检测到 GITHUB_TOKEN 环境变量。" -ForegroundColor Yellow
    $token = Read-Host "请输入 GitHub Token(Fine-grained PAT, 需 Actions 读权限)"
}
if (-not $token) { Write-Host "未提供 Token, 退出。" -ForegroundColor Red; exit 1 }

$Headers = @{
    "Authorization" = "Bearer $token"
    "Accept"        = "application/vnd.github+json"
    "User-Agent"    = "ci-failure-log-script"
}
$ApiBase = "https://api.github.com/repos/$Repo"

function Invoke-GhApi {
    param([string]$Uri)
    try {
        return Invoke-RestMethod -Uri $Uri -Headers $Headers -TimeoutSec 30
    }
    catch {
        Write-Host "API 调用失败: $Uri" -ForegroundColor Red
        Write-Host "  HTTP 错误: $($_.Exception.Message)" -ForegroundColor Red
        if ($_.Exception.Response.StatusCode.value__ -eq 401) {
            Write-Host "  401 = Token 无效或无权限(Fine-grained PAT 需勾选 Actions: Read)" -ForegroundColor Red
        }
        if ($_.Exception.Response.StatusCode.value__ -eq 404) {
            Write-Host "  404 = 仓库不存在或 Token 无权访问该仓库" -ForegroundColor Red
        }
        exit 1
    }
}

# ---------- 定位目标 run ----------
$targetRun = $null

if ($RunId -gt 0) {
    Write-Host "`n=== 拉取指定 run: $RunId ===" -ForegroundColor Cyan
    $targetRun = Invoke-GhApi "$ApiBase/actions/runs/$RunId"
}
else {
    # 扫描最近 N 次 run, 找最新失败的
    $statusFilter = "failure"
    $query = "$ApiBase/actions/runs?status=$statusFilter&per_page=$Limit"
    if ($Workflow) { $query += "&event=push" }
    Write-Host "`n=== 扫描最近 $Limit 次失败的 run ===" -ForegroundColor Cyan
    $runsResp = Invoke-GhApi $query
    $failedRuns = @($runsResp.workflow_runs)
    if ($failedRuns.Count -eq 0) {
        Write-Host "最近 $Limit 次 run 中没有失败的(全绿)。恭喜, 无需分析!" -ForegroundColor Green
        exit 0
    }
    if ($Workflow) {
        $filtered = $failedRuns | Where-Object { $_.path -like "*$Workflow*" }
        if ($filtered.Count -gt 0) { $failedRuns = @($filtered) }
    }
    $targetRun = $failedRuns[0]
    Write-Host ("找到 {0} 个失败 run, 取最新: " -f $failedRuns.Count) -NoNewline
}

$runId = $targetRun.id
$runName = $targetRun.name
$runUrl = $targetRun.html_url
$runTitle = ($targetRun.head_commit.message -split "`n")[0]
$runBranch = $targetRun.head_branch
$runConclusion = $targetRun.conclusion

Write-Host "[$runName] #$runId ($runConclusion)" -ForegroundColor Yellow
Write-Host "  分支: $runBranch"
Write-Host "  提交: $runTitle"
Write-Host "  链接: $runUrl"

# ---------- 拉取该 run 的 jobs ----------
Write-Host "`n=== 拉取 run 的 job 列表 ===" -ForegroundColor Cyan
$jobsResp = Invoke-GhApi "$ApiBase/actions/runs/$runId/jobs?per_page=100"
$allJobs = @($jobsResp.jobs)
$failedJobs = @($allJobs | Where-Object { $_.conclusion -eq "failure" })

if ($failedJobs.Count -eq 0) {
    Write-Host "该 run 没有失败的 job(可能 run 整体取消/无结论)。全部 job 状态:" -ForegroundColor Yellow
    $allJobs | ForEach-Object { Write-Host ("  {0} -> {1}" -f $_.name, $_.conclusion) }
    exit 0
}

Write-Host ("共 {0} 个 job, 其中 {1} 个失败:" -f $allJobs.Count, $failedJobs.Count) -ForegroundColor Yellow
$failedJobs | ForEach-Object { Write-Host ("  [X] {0}" -f $_.name) -ForegroundColor Red }

# ---------- 下载失败 job 日志并提取关键报错 ----------
# 关键报错匹配模式(顺序即优先级)
$patterns = @(
    'AssertionError',
    'ModuleNotFoundError',
    'ImportError',
    'Traceback \(most recent call last\)',
    '^\s*\[FAIL\]',
    'FAILED',
    'ERROR\b',
    'Error:',
    'Exception',
    'exit code [1-9]',
    'Process completed with exit code'
)

$report = New-Object System.Text.StringBuilder
[void]$report.AppendLine("# CI 失败分析报告")
[void]$report.AppendLine("")
[void]$report.AppendLine("- **Run**: [$runName #$runId]($runUrl)")
[void]$report.AppendLine("- **分支**: $runBranch")
[void]$report.AppendLine("- **提交**: $runTitle")
[void]$report.AppendLine("- **失败 Job 数**: $($failedJobs.Count)")
[void]$report.AppendLine("- **生成时间**: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
[void]$report.AppendLine("")

$outDir = Join-Path $PSScriptRoot "ci-failure-logs"
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }

foreach ($job in $failedJobs) {
    $jobName = $job.name
    Write-Host "`n------------------------------------------------------------" -ForegroundColor Cyan
    Write-Host "失败 Job: $jobName" -ForegroundColor Red
    Write-Host "  失败步骤:" -NoNewline
    $failedSteps = @($job.steps | Where-Object { $_.conclusion -eq "failure" })
    if ($failedSteps.Count -gt 0) {
        Write-Host ""
        $failedSteps | ForEach-Object { Write-Host ("    - {0}" -f $_.name) -ForegroundColor Red }
    }
    else { Write-Host " (无, run 级失败)" }

    # 下载日志(该 API 返回纯文本 zip 或纯文本, PowerShell 5.1 处理)
    $logUrl = "$ApiBase/actions/jobs/$($job.id)/logs"
    Write-Host "  下载日志..." -NoNewline
    try {
        $logText = Invoke-RestMethod -Uri $logUrl -Headers $Headers -TimeoutSec 60
        # 保存完整日志
        $safeName = ($jobName -replace '[\\/:*?"<>| ]', '_')
        $logFile = Join-Path $outDir "$safeName.log"
        $logText | Out-File -FilePath $logFile -Encoding utf8
        Write-Host " OK ($([math]::Round((Get-Item $logFile).Length/1KB,1)) KB)" -ForegroundColor Green
    }
    catch {
        Write-Host " 失败: $($_.Exception.Message)" -ForegroundColor Red
        [void]$report.AppendLine("## $jobName")
        [void]$report.AppendLine("")
        [void]$report.AppendLine("日志下载失败: $($_.Exception.Message)")
        [void]$report.AppendLine("")
        continue
    }

    # 提取关键报错行 + 前后文
    [void]$report.AppendLine("## $jobName")
    [void]$report.AppendLine("")
    [void]$report.AppendLine("失败步骤: " + (($failedSteps | ForEach-Object { $_.name }) -join ", "))
    [void]$report.AppendLine("完整日志: ``ci-failure-logs/$safeName.log``")
    [void]$report.AppendLine("")
    $codeFence = [string][char]96 * 3
    [void]$report.AppendLine($codeFence)

    $lines = $logText -split "`n"
    $matched = New-Object System.Collections.Generic.HashSet[int]
    for ($i = 0; $i -lt $lines.Count; $i++) {
        foreach ($p in $patterns) {
            if ($lines[$i] -match $p) {
                for ($j = [Math]::Max(0, $i - $ContextLines); $j -le [Math]::Min($lines.Count - 1, $i + $ContextLines); $j++) {
                    [void]$matched.Add($j)
                }
                break
            }
        }
    }

    if ($matched.Count -eq 0) {
        [void]$report.AppendLine("(未匹配到已知报错模式, 请查看完整日志末尾 50 行:)")
        $tail = [Math]::Max(0, $lines.Count - 50)
        for ($i = $tail; $i -lt $lines.Count; $i++) {
            [void]$report.AppendLine($lines[$i].TrimEnd("`r"))
        }
    }
    else {
        $sorted = $matched | Sort-Object
        $prev = -10
        foreach ($idx in $sorted) {
            if ($idx -gt $prev + 1) { [void]$report.AppendLine("    ...") }
            [void]$report.AppendLine(("L{0}: {1}" -f ($idx + 1), $lines[$idx].TrimEnd("`r")))
            $prev = $idx
        }
    }
    [void]$report.AppendLine($codeFence)
    [void]$report.AppendLine("")
}

# ---------- 输出报告 ----------
$reportFile = Join-Path $PSScriptRoot "ci-failure-report.md"
$report.ToString() | Out-File -FilePath $reportFile -Encoding utf8

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "报告已生成: $reportFile" -ForegroundColor Green
Write-Host "完整日志目录: $outDir" -ForegroundColor Green
Write-Host ""
Write-Host "下一步: 把 ci-failure-report.md 的内容直接发给 AI 分析," -ForegroundColor Yellow
Write-Host "      或连同 ci-failure-logs/ 下的完整日志一起发。" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Green

# 同时在控制台输出报告内容(方便直接复制)
Write-Host "`n--- 报告内容预览 ---"
Get-Content $reportFile -Raw
