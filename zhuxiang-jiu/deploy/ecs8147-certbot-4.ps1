# certbot 迁国内 步骤4: deploy-hook + 月度计划任务 + dry-run 终验
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($msg) { Write-Host "==> $msg" }
function Info($msg) { Write-Host "    $msg" }

Step '[1/4] deploy-hook(续期后自动部署到 nginx)'
$deploy = @'
Copy-Item C:\Certbot\live\zyjiu.com\fullchain.pem C:\nginx\conf\cert.pem -Force
Copy-Item C:\Certbot\live\zyjiu.com\privkey.pem   C:\nginx\conf\key.pem  -Force
$t = cmd /c "cd /d C:\nginx && nginx.exe -t 2>&1"
if ($LASTEXITCODE -ne 0) {
    # 新证书配置异常时回滚(保留 .bak-le-old 为上一代可用证书)
    Copy-Item C:\nginx\conf\cert.pem.bak-le-old C:\nginx\conf\cert.pem -Force
    Copy-Item C:\nginx\conf\key.pem.bak-le-old  C:\nginx\conf\key.pem  -Force
    cmd /c "cd /d C:\nginx && nginx.exe -s reload"
    exit 1
}
cmd /c "cd /d C:\nginx && nginx.exe -s reload"
exit 0
'@
[IO.File]::WriteAllText('C:\certbot-hooks\deploy.ps1', $deploy, [Text.UTF8Encoding]::new($false))
cmd /c "echo @powershell -NoProfile -ExecutionPolicy Bypass -File C:\certbot-hooks\deploy.ps1 > C:\certbot-hooks\deploy.bat"
Info 'deploy hook 就位'

Step '[2/4] renewal conf 追加 deploy_hook(幂等)'
$rc = 'C:\Certbot\renewal\zyjiu.com.conf'
$lines = [IO.File]::ReadAllLines($rc, [Text.Encoding]::UTF8)
if (-not ($lines | Where-Object { $_ -match '^deploy_hook' })) {
    $lines + 'deploy_hook = C:\certbot-hooks\deploy.bat' | Set-Content $rc -Encoding ascii
    Info 'deploy_hook 已追加'
} else { Info 'deploy_hook 已存在' }

Step '[3/4] Windows 计划任务(每月 1 日 04:00)'
$renew = @'
$env:PYTHONUTF8 = '1'
$log = 'C:\certbot-hooks\renew-run.log'
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] renew start" | Add-Content $log
cmd /c "cd /d C:\ && C:\Python312\Scripts\certbot.exe renew --quiet >> $log 2>&1"
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] renew exit=$LASTEXITCODE" | Add-Content $log
'@
[IO.File]::WriteAllText('C:\certbot-hooks\renew.ps1', $renew, [Text.UTF8Encoding]::new($false))
cmd /c "echo @powershell -NoProfile -ExecutionPolicy Bypass -File C:\certbot-hooks\renew.ps1 > C:\certbot-hooks\renew.bat"
cmd /c "schtasks /delete /tn zyjiu-certbot-renew /f 2>nul"
cmd /c "schtasks /create /tn zyjiu-certbot-renew /tr C:\certbot-hooks\renew.bat /sc monthly /d 1 /st 04:00 /ru SYSTEM /rl HIGHEST /f"
$task = schtasks /query /tn zyjiu-certbot-renew
Info ($task -join ' ')

Step '[4/4] dry-run 终验(staging 全链: auth->LE验证, 不动生产证书)'
cmd /c "cd /d C:\ && C:\Python312\Scripts\certbot.exe renew --dry-run > C:\tmp8147\certbot-dryrun.log 2>&1"
$rc2 = $LASTEXITCODE
Info ("dry-run 退出码: " + $rc2)
Get-Content C:\tmp8147\certbot-dryrun.log -Tail 12 | ForEach-Object { Info $_ }
# dry-run 后确认生产证书未被替换
$tcp = New-Object Net.Sockets.TcpClient('127.0.0.1', 443)
$ssl = New-Object Net.Security.SslStream($tcp.GetStream(), $false, { $true })
$ssl.AuthenticateAsClient('zyjiu.com')
$cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
Info ("生产证书仍为: " + $cert.Subject + " 到期 " + $cert.NotAfter)
$ssl.Dispose(); $tcp.Dispose()
if ($rc2 -ne 0) { exit 1 }
Write-Host 'CERTBOT-STEP4-DONE'
