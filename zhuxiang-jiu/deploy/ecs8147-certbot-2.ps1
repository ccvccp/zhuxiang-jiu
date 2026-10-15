# certbot 迁国内 步骤2: 双域 SAN 新签(静态目录挑战方案, 不依赖 OSS)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($msg) { Write-Host "==> $msg" }
function Info($msg) { Write-Host "    $msg" }

Step '[1/5] 挑战目录自检(静态 root 天然命中)'
New-Item -ItemType Directory -Force C:\www\dist\.well-known\acme-challenge | Out-Null
Set-Content C:\www\dist\.well-known\acme-challenge\probe-8147 'challenge-probe-ok' -Encoding ascii
$c1 = curl.exe -sk -o NUL -w '%{http_code}' -m 8 https://zyjiu.com/.well-known/acme-challenge/probe-8147
$c2 = curl.exe -sk -o NUL -w '%{http_code}' -m 8 https://zxjiu.com/.well-known/acme-challenge/probe-8147
$v1 = curl.exe -sk -m 8 https://zyjiu.com/.well-known/acme-challenge/probe-8147
Info ("zyjiu: $c1 / zxjiu: $c2 / 值: $v1")
if ($c1 -ne '200' -or $c2 -ne '200' -or $v1 -ne 'challenge-probe-ok') {
    Info '!! 挑战路径不通(需检查 nginx 80/443 对 .well-known 的处理)'
    exit 1
}
Remove-Item C:\www\dist\.well-known\acme-challenge\probe-8147 -Force
Info '自检通过, 已清理探针'

Step '[2/5] 落静态目录版 hooks'
# auth: 写挑战文件
$auth = @'
$dir = 'C:\www\dist\.well-known\acme-challenge'
New-Item -ItemType Directory -Force $dir | Out-Null
[IO.File]::WriteAllText("$dir\$($env:CERTBOT_TOKEN)", $env:CERTBOT_VALIDATION)
exit 0
'@
[IO.File]::WriteAllText('C:\certbot-hooks\auth.ps1', $auth, [Text.UTF8Encoding]::new($false))
# cleanup: 删挑战文件
$clean = @'
Remove-Item "C:\www\dist\.well-known\acme-challenge\$($env:CERTBOT_TOKEN)" -Force -ErrorAction SilentlyContinue
exit 0
'@
[IO.File]::WriteAllText('C:\certbot-hooks\cleanup.ps1', $clean, [Text.UTF8Encoding]::new($false))
cmd /c "echo @powershell -NoProfile -ExecutionPolicy Bypass -File C:\certbot-hooks\auth.ps1 > C:\certbot-hooks\auth.bat"
cmd /c "echo @powershell -NoProfile -ExecutionPolicy Bypass -File C:\certbot-hooks\cleanup.ps1 > C:\certbot-hooks\cleanup.bat"
Info 'hooks 就位(静态目录版)'

Step '[3/5] certbot 新签 zyjiu.com(单域; zxjiu.com 未备案被阿里云 Beaver 拦截无法通过 LE 验证, 已退役——生产 API 全走 zyjiu.com 同域 /api)'
$env:PYTHONUTF8 = '1'
cmd /c "cd /d C:\ && C:\Python312\Scripts\certbot.exe certonly --manual --preferred-challenges http -d zyjiu.com --manual-auth-hook C:\certbot-hooks\auth.bat --manual-cleanup-hook C:\certbot-hooks\cleanup.bat --email zhuxiang-ops@zyjiu.com --agree-tos --no-eff-email --non-interactive --keep-until-expiring > C:\tmp8147\certbot-sign.log 2>&1"
$rc = $LASTEXITCODE
Info ("certbot 退出码: " + $rc)
Get-Content C:\tmp8147\certbot-sign.log -Tail 15 | ForEach-Object { Info $_ }
if ($rc -ne 0) { exit 1 }

Step '[4/5] 部署证书到 nginx(备份+热替换+reload)'
$src = 'C:\Certbot\live\zyjiu.com'
Copy-Item C:\nginx\conf\cert.pem C:\nginx\conf\cert.pem.bak-le-old -Force
Copy-Item C:\nginx\conf\key.pem  C:\nginx\conf\key.pem.bak-le-old  -Force
Copy-Item "$src\fullchain.pem" C:\nginx\conf\cert.pem -Force
Copy-Item "$src\privkey.pem"   C:\nginx\conf\key.pem  -Force
$t = cmd /c "cd /d C:\nginx && nginx.exe -t 2>&1"
Info ("nginx -t: " + ($t -join ' '))
if ($LASTEXITCODE -ne 0) {
    Copy-Item C:\nginx\conf\cert.pem.bak-le-old C:\nginx\conf\cert.pem -Force
    Copy-Item C:\nginx\conf\key.pem.bak-le-old  C:\nginx\conf\key.pem  -Force
    cmd /c "cd /d C:\nginx && nginx.exe -s reload"
    Info '!! nginx -t 失败已回滚旧证书'
    exit 1
}
cmd /c "cd /d C:\nginx && nginx.exe -s reload"
Start-Sleep -Seconds 3
Info '证书已部署并 reload'

Step '[5/5] 终验'
$expire = curl.exe -sk -v -m 8 https://zyjiu.com/ 2>&1 | Select-String 'expire date|subject:'
Info ($expire -join ' | ')
foreach ($d in @('zyjiu.com','127.0.0.1')) {
    $c = curl.exe -sk -o NUL -w '%{http_code}' -m 8 "https://$d/api/health"
    Info ("$d /api/health -> " + $c)
}
Write-Host 'CERTBOT-STEP2-DONE'
