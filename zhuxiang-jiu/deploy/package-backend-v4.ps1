# P4-10 生产代码包打包(本地执行, 无需 AK)
# 产物: deploy/dist/backend-code-v4.tgz —— 仅运行时代码(*.py + requirements.txt)
$ErrorActionPreference = 'Stop'
$backendDir = (Resolve-Path (Join-Path $PSScriptRoot '..\backend')).Path
$distDir = Join-Path $PSScriptRoot 'dist'
New-Item -ItemType Directory -Force $distDir | Out-Null
$out = Join-Path $distDir 'backend-code-v4.tgz'
if (Test-Path $out) { Remove-Item $out -Force }

# 排除目录清单(本地 vendored 库/缓存/部署脚本/运行时数据)
$skipDirs = @(
    '__pycache__', '.pycache', '.pytest_cache', '.ruff_cache', '.mypy_cache',
    '.pip_libs', '.deps', '.lint_libs', 'node_modules',
    'deploy', 'agent_tasks', 'live_qa_logs', 'logs', 'media', 'channels-bot', '.git'
)
$excludes = New-Object System.Collections.Generic.List[string]
foreach ($d in $skipDirs) { $excludes.Add('--exclude=' + $d) }
$excludes.Add('--exclude=*.pyc')
$excludes.Add('--exclude=.env')
$excludes.Add('--exclude=.env.example')

# staging: 复制运行时代码到临时目录再打包(规避 tar 通配漏新目录)
$stage = Join-Path $env:TEMP ('backend-pack-' + (Get-Date -Format 'yyyyMMddHHmmss'))
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null
$files = Get-ChildItem $backendDir -Recurse -File | Where-Object {
    $rel = $_.FullName.Substring($backendDir.Length + 1)
    $ok = $true
    foreach ($d in $skipDirs) { if ($rel -like ('*' + $d + '\*')) { $ok = $false; break } }
    if ($_.Name -eq '.env' -or $_.Name -eq '.env.example' -or $_.Extension -eq '.pyc') { $ok = $false }
    if ($_.Extension -eq '.py' -or $_.Name -eq 'requirements.txt') { } else { $ok = $false }
    $ok
}
foreach ($f in $files) {
    $rel = $f.FullName.Substring($backendDir.Length + 1)
    $dest = Join-Path $stage $rel
    $destDir = Split-Path $dest -Parent
    if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force $destDir | Out-Null }
    Copy-Item $f.FullName $dest -Force
}
Write-Host ('staged: ' + $files.Count + ' files')

Push-Location $stage
try {
    $argLine = '-czf "' + $out + '" .'
    cmd /c ('tar ' + $argLine + ' 2>&1')
    if ($LASTEXITCODE -ne 0) { throw ('tar 退出码 ' + $LASTEXITCODE) }
} finally { Pop-Location }
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue

$mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Host ('PACK-OK ' + $out + '  ' + $mb + 'MB  files=' + $files.Count)
# 包内抽查: P4-10 关键新文件必须在内
# 校验用 python(Unicode 安全; 子进程 bsdtar 对中文路径处理不可靠, 弃用 tar -tzf)
$pyCheck = 'import sys, tarfile' + "`n" + 'print("\n".join(tarfile.open(sys.argv[1], "r:gz").getnames()))'
$tmpPy = Join-Path $env:TEMP 'pack_check.py'
[IO.File]::WriteAllText($tmpPy, $pyCheck, [Text.UTF8Encoding]::new($true))
$entries = @(python $tmpPy $out)
Remove-Item $tmpPy -Force -ErrorAction SilentlyContinue
$mustList = @('./services/dpo_collect_scheduler.py', './services/selfplay_service.py', './services/mcp_server_service.py', './routes/dpo_routes.py', './main.py', './requirements.txt')
$miss = 0
foreach ($m in $mustList) {
    if ($entries -notcontains $m) { Write-Host ('MISSING-IN-PACK: ' + $m); $miss++ }
}
if ($miss -gt 0) { exit 1 }
Write-Host 'PACK-CHECK-OK (P4-10 关键文件 6/6 在包内)'
