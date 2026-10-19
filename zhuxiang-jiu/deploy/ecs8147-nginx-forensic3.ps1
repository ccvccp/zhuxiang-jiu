# 取证3: nginx 配置全文(Write-Host 通道) + 响应头
$ErrorActionPreference = 'Continue'
Write-Host '== nginx.conf =='
(Get-Content C:\nginx\conf\nginx.conf) | ForEach-Object { Write-Host $_ }
Write-Host '== site-proxy.conf =='
(Get-Content C:\nginx\conf\site-proxy.conf) | ForEach-Object { Write-Host $_ }
Write-Host '== official.html 响应头 =='
$h = curl.exe -s -D - -o NUL -m 8 -H 'Host: zxjiu.com' http://127.0.0.1/official.html
$h | ForEach-Object { Write-Host $_ }
