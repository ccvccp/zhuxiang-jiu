# zxjiu.com(被 Beaver 备案拦截) -> zyjiu.com 生产配置迁移 + 重启
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Info($m) { Write-Host ("    " + $m) }

Write-Host '==> 修正 .env 域名引用'
$f = 'C:\backend\.env'
$c = [IO.File]::ReadAllText($f, [Text.Encoding]::UTF8)
$c = $c -replace 'BAIDU_PUSH_SITE=zxjiu\.com', 'BAIDU_PUSH_SITE=zyjiu.com'
$c = $c -replace 'SITE_BASE_URL=https://zxjiu\.com', 'SITE_BASE_URL=https://zyjiu.com'
$c = $c -replace 'ENTRY_WEBAUTHN_RP_ID=zxjiu\.com', 'ENTRY_WEBAUTHN_RP_ID=zyjiu.com'
$c = $c -replace 'ENTRY_WEBAUTHN_ORIGINS=https://zxjiu\.com,https://www\.zxjiu\.com', 'ENTRY_WEBAUTHN_ORIGINS=https://zyjiu.com'
[IO.File]::WriteAllText($f, $c, [Text.UTF8Encoding]::new($false))
[IO.File]::ReadAllLines($f, [Text.Encoding]::UTF8) | Where-Object { $_ -match 'BAIDU_PUSH_SITE|SITE_BASE_URL|WEBAUTHN' } | ForEach-Object { Info $_ }

Write-Host '==> 重启后端'
cmd /c "C:\nginx\nssm.exe restart backend8147"
Start-Sleep -Seconds 12
Info ("backend8147: " + (Get-Service backend8147).Status)
$code = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/health
Info ("api/health: " + $code)
if ($code -ne '200') {
    Get-Content C:\backend\logs\service-err.log -Tail 15 | ForEach-Object { Info $_ }
    exit 1
}
Write-Host 'DOMAIN-SWITCH-DONE'
