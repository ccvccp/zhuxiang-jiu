# 取证: ECS 上真实 nginx 配置(root/缓存头/html 处理)
$ErrorActionPreference = 'Continue'
Write-Host '== nginx 进程与配置路径 =='
$proc = Get-Process nginx -ErrorAction SilentlyContinue | Select-Object -First 1
if ($proc) { Write-Host ('nginx PID: ' + $proc.Id + '  Path: ' + $proc.Path) } else { Write-Host 'nginx 进程未找到(可能服务名不同)' }
Get-Service | Where-Object { $_.Name -like '*nginx*' } | ForEach-Object { Write-Host ('服务: ' + $_.Name + ' ' + $_.Status) }

Write-Host '== 常见配置位置探测 =='
foreach ($p in @('C:\nginx\conf\nginx.conf', 'C:\www\nginx\conf\nginx.conf', 'C:\Program Files\nginx\conf\nginx.conf', 'D:\nginx\conf\nginx.conf')) {
    if (Test-Path $p) { Write-Host ('FOUND: ' + $p + ' (' + (Get-Item $p).Length + 'B)') }
}

Write-Host '== 443/80 监听 =='
netstat -ano | Select-String ':443\s+:.*LISTENING|:80\s+:.*LISTENING' | ForEach-Object { $_.Line.Trim() }

Write-Host '== http 127.0.0.1 html 响应头(缓存策略) =='
curl.exe -s -I -m 8 -H 'Host: zxjiu.com' http://127.0.0.1/official.html
Write-Host '== https 本机自访(证书SNI) =='
curl.exe -s -I -m 8 -k https://127.0.0.1/ -H 'Host: zxjiu.com' 2>&1 | Select-Object -First 12
