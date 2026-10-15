﻿# zyjiu 国内 ECS 一键安装 (8.147.66.237, 管理员 PowerShell 运行)
# 功能: nginx/Windows 静态站 + /api 反代海外 + 证书 + 服务常驻
$ErrorActionPreference = 'Stop'
$OSS = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/ecs8147'

Write-Host '==> [1/7] 创建目录+停旧服务(防文件锁)' -ForegroundColor Cyan
New-Item -ItemType Directory -Force C:\www, C:\nginx, C:\tmp8147 | Out-Null
$svc0 = Get-Service nginx8147 -ErrorAction SilentlyContinue
if ($svc0) {
    cmd /c "C:\nginx\nssm.exe stop nginx8147" | Out-Null
    Start-Sleep -Seconds 2
}
cmd /c "taskkill /f /im nginx.exe 2>nul"
cmd /c "taskkill /f /im nssm.exe 2>nul"
Start-Sleep -Seconds 1

Write-Host '==> [2/7] 下载安装包(nginx/dist/证书/nssm)' -ForegroundColor Cyan
$dl = @{ 'nginx.zip'='C:\tmp8147\nginx.zip'; 'dist.tgz'='C:\tmp8147\dist.tgz';
         'cert.pem'='C:\tmp8147\cert.pem'; 'nginx.conf'='C:\tmp8147\nginx.conf';
         'site-proxy.conf'='C:\tmp8147\site-proxy.conf'; 'nssm.exe'='C:\tmp8147\nssm.exe' }
foreach ($k in $dl.Keys) {
    Invoke-WebRequest "$OSS/$k" -OutFile $dl[$k] -UseBasicParsing
    Write-Host "    $k OK"
}
# 私钥走临时授权链接(24h 有效, 不落公开桶)
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/ecs8147/key.pem?OSSAccessKeyId=__PATCHED_BY_ECS8147_KEYURL__&Expires=0&Signature=placeholder' -OutFile C:\tmp8147\key.pem -UseBasicParsing
Write-Host '    key.pem OK'

Write-Host '==> [3/7] 解压 nginx 并落位配置' -ForegroundColor Cyan
Expand-Archive C:\tmp8147\nginx.zip -DestinationPath C:\tmp8147\nginxx -Force
$inner = (Get-ChildItem C:\tmp8147\nginxx -Directory | Select-Object -First 1).FullName
Copy-Item "$inner\*" C:\nginx\ -Recurse -Force
# 解压后落位自定义配置(顺序关键: zip 内默认 conf 会覆盖先放的文件)
New-Item -ItemType Directory -Force C:\nginx\conf | Out-Null
Copy-Item C:\tmp8147\nginx.conf      C:\nginx\conf\nginx.conf      -Force
Copy-Item C:\tmp8147\site-proxy.conf C:\nginx\conf\site-proxy.conf -Force
Copy-Item C:\tmp8147\cert.pem        C:\nginx\conf\cert.pem        -Force
Copy-Item C:\tmp8147\key.pem         C:\nginx\conf\key.pem         -Force
Copy-Item C:\tmp8147\nssm.exe        C:\nginx\nssm.exe             -Force
New-Item -ItemType Directory -Force C:\nginx\logs | Out-Null
Write-Host '    conf/cert/key/nssm 已落位'

Write-Host '==> [4/7] 解压前端产物' -ForegroundColor Cyan
tar -xzf C:\tmp8147\dist.tgz -C C:\www
Write-Host "    dist: $((Get-ChildItem C:\www\dist | Measure-Object).Count) 项"

Write-Host '==> [5/7] 防火墙放行 80/443' -ForegroundColor Cyan
netsh advfirewall firewall delete rule name="nginx8147-80" | Out-Null
netsh advfirewall firewall delete rule name="nginx8147-443" | Out-Null
netsh advfirewall firewall add rule name="nginx8147-80"  dir=in action=allow protocol=TCP localport=80  | Out-Null
netsh advfirewall firewall add rule name="nginx8147-443" dir=in action=allow protocol=TCP localport=443 | Out-Null
Write-Host '    OK'

Write-Host '==> [6/7] 注册系统服务并启动' -ForegroundColor Cyan
$svc = Get-Service nginx8147 -ErrorAction SilentlyContinue
if ($svc) {
    cmd /c "C:\nginx\nssm.exe stop nginx8147" | Out-Null
    cmd /c "C:\nginx\nssm.exe remove nginx8147 confirm" | Out-Null
}
cmd /c "C:\nginx\nssm.exe install nginx8147 C:\nginx\nginx.exe" | Out-Null
cmd /c "C:\nginx\nssm.exe set nginx8147 AppDirectory C:\nginx" | Out-Null
cmd /c "C:\nginx\nssm.exe set nginx8147 AppStdout C:\nginx\logs\service-out.log" | Out-Null
cmd /c "C:\nginx\nssm.exe set nginx8147 AppStderr C:\nginx\logs\service-err.log" | Out-Null
cmd /c "C:\nginx\nssm.exe start nginx8147" | Out-Null
Start-Sleep -Seconds 3

Write-Host '==> [7/7] 自检' -ForegroundColor Cyan
$r1 = try { (Invoke-WebRequest http://127.0.0.1/ -UseBasicParsing -TimeoutSec 10).StatusCode } catch { "FAIL: $($_.Exception.Message)" }
Write-Host "    http  127.0.0.1 → $r1"
$r2 = curl.exe -sk -o NUL -w "%{http_code}" https://127.0.0.1/
Write-Host "    https 127.0.0.1 → $r2"
if ($r1 -eq 200 -and $r2 -eq '200') {
    Write-Host '==> 全部就绪! 下一步: ECS 控制台安全组放行 80/443, 然后 DNS @ 切到 8.147.66.237' -ForegroundColor Green
} else {
    Write-Host '!! 自检未过, 查看错误日志' -ForegroundColor Yellow
    Get-Content C:\nginx\logs\error.log -Tail 10 -ErrorAction SilentlyContinue
}

