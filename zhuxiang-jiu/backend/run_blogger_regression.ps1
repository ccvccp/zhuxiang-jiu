# 40号 DV博主+雷达 全量测试回归批跑器(28 套件)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$files = Get-ChildItem "test_blogger*.py", "test_radar*.py" | Sort-Object Name
$summary = @()
foreach ($f in $files) {
    $out = python $f.FullName 2>&1 | Out-String
    $code = $LASTEXITCODE
    # 提取结果行(各套件格式: 总计: X 通过 / Y 失败 | 通过 X 项, 失败 Y 项 | ALL PASS)
    $line = ($out -split "`n" | Select-String -Pattern "总计|通过.*失败|ALL PASS|PASS" | Select-Object -Last 1)
    $summaryLine = if ($line) { $line.ToString().Trim() } else { "(no result line)" }
    $status = if ($code -eq 0) { "PASS" } else { "FAIL($code)" }
    $summary += [PSCustomObject]@{ Suite = $f.Name; Status = $status; Result = $summaryLine }
    Write-Host ("{0,-28} {1,-8} {2}" -f $f.Name, $status, $summaryLine)
}
Write-Host "===================="
$fail = ($summary | Where-Object { $_.Status -ne "PASS" }).Count
Write-Host "SUITES: $($summary.Count)  FAIL: $fail"
$summary | Where-Object { $_.Status -ne "PASS" } | Format-Table -AutoSize
