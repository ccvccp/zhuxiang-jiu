# 最简构建诊断脚本 - 一次性输出关键信息
$ErrorActionPreference = "Continue"
cd d:\网站架构设计
$out = "d:\网站架构设计\build-result.txt"

"========== 1. 构建开始 ==========" | Out-File $out -Encoding utf8
docker compose build --no-cache backend 2>&1 | Out-File $out -Encoding utf8 -Append
$buildExit = $LASTEXITCODE
"" | Out-File $out -Encoding utf8 -Append
"构建退出码: $buildExit" | Out-File $out -Encoding utf8 -Append

if ($buildExit -eq 0) {
    "" | Out-File $out -Encoding utf8 -Append
    "========== 2. 构建成功,启动容器 ==========" | Out-File $out -Encoding utf8 -Append
    docker compose up -d 2>&1 | Out-File $out -Encoding utf8 -Append
    Start-Sleep -Seconds 30

    "" | Out-File $out -Encoding utf8 -Append
    "========== 3. 容器状态 ==========" | Out-File $out -Encoding utf8 -Append
    docker compose ps 2>&1 | Out-File $out -Encoding utf8 -Append

    "" | Out-File $out -Encoding utf8 -Append
    "========== 4. Backend 日志 ==========" | Out-File $out -Encoding utf8 -Append
    docker compose logs --tail 30 backend 2>&1 | Out-File $out -Encoding utf8 -Append
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "完成! 结果已保存到: $out" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Get-Item $out | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
