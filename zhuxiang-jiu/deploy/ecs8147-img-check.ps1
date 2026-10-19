# ECS 检查 images/products 目录
$ErrorActionPreference = 'Continue'
$p = 'C:\www\dist\images\products'
if (Test-Path $p) {
    Write-Host ('目录存在, 文件数: ' + @(Get-ChildItem $p -File).Count)
    Get-ChildItem $p -File | ForEach-Object { Write-Host ('  ' + $_.Name + ' ' + $_.Length + 'B') }
} else { Write-Host 'images/products 目录不存在' }
