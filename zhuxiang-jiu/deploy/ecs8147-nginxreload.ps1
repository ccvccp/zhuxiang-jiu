# [nginx reload 补执行] conf 已写入(no-store), Start-Process 绕 stderr 假失败 + 验证响应头
$ErrorActionPreference = 'Continue'
Set-Location C:\nginx
$p = Start-Process -FilePath 'C:\nginx\nginx.exe' -ArgumentList '-s','reload' -Wait -PassThru -NoNewWindow
Write-Host ('reload_exit=' + $p.ExitCode)
Start-Sleep -Seconds 2
$c = [IO.File]::ReadAllText('C:\nginx\conf\nginx.conf')
Write-Host ('store_in_conf=' + ([regex]::Matches($c, 'no-store, must-revalidate')).Count)
# 本机探响应头
$h = curl.exe -s -I -m 10 -H 'Host: zyjiu.com' 'http://127.0.0.1/products.html' 2>$null
($h -split "`n") | Where-Object { $_ -match 'cache-control|HTTP' } | ForEach-Object { Write-Host $_.Trim() }
Write-Host 'RELOAD-DONE'