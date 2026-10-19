# 取证: ECS 后端存储模式/数据持久化情况
Write-Host '--- .env(脱敏看关键键):'
Get-Content C:\backend\.env | Where-Object { $_ -match 'STORE|REDIS|DB|MODE' } | ForEach-Object { Write-Host $_ }
Write-Host '--- data 目录:'
Get-ChildItem C:\backend\data -ErrorAction SilentlyContinue | Select-Object -First 15 | ForEach-Object { Write-Host ($_.Name + ' ' + $_.Length + 'B ' + $_.LastWriteTime) }
Write-Host '--- 当前 product_repository 图片基线:'
$f = 'C:\backend\repositories\product_repository.py'
Write-Host ('含 _PROD_IMG_BASE 相对路径: ' + ([IO.File]::ReadAllText($f) -match '/images/products'))
Write-Host ('文件时间: ' + (Get-Item $f).LastWriteTime)
Write-Host 'PROBE3-DONE'
