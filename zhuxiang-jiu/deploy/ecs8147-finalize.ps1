# Phase 4 收尾: asyncio 常态化固化 + 重启验证 + 生产冒烟
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($msg) { Write-Host "==> $msg" }
function Info($msg) { Write-Host "    $msg" }

Step '[1/4] .env 显式固化 asyncio 模式'
$envFile = 'C:\backend\.env'
$content = [IO.File]::ReadAllText($envFile, [Text.Encoding]::UTF8)
$changed = $false
if ($content -match '(?m)^#?\s*STORE_MODE=') {
    $content = $content -replace '(?m)^#?\s*STORE_MODE=.*', 'STORE_MODE=asyncio'
} else {
    $content = $content.TrimEnd("`r`n") + "`nSTORE_MODE=asyncio`n"
    $changed = $true
}
if ($content -match '(?m)^#?\s*LOCK_MODE=') {
    $content = $content -replace '(?m)^#?\s*LOCK_MODE=.*', 'LOCK_MODE=asyncio'
} else {
    $content = $content + "LOCK_MODE=asyncio`n"
}
[IO.File]::WriteAllText($envFile, $content, [Text.UTF8Encoding]::new($false))
Get-Content $envFile | Select-String 'STORE_MODE|LOCK_MODE' | ForEach-Object { Info $_.Line }
Info 'OK'

Step '[2/4] 重启后端服务(加载固化配置)'
cmd /c "C:\nginx\nssm.exe restart backend8147"
Start-Sleep -Seconds 12
$st = (Get-Service backend8147).Status
Info ("backend8147: " + $st)
if ($st -ne 'Running') {
    Get-Content C:\backend\logs\service-err.log -Tail 20 | ForEach-Object { Info $_ }
    exit 1
}

Step '[3/4] 生产冒烟(经公网域名)'
$tests = @(
    @{n='health'; u='https://zxjiu.com/api/health'},
    @{n='docs';   u='https://zxjiu.com/docs'},
    @{n='openapi';u='https://zxjiu.com/openapi.json'},
    @{n='static'; u='https://zxjiu.com/'}
)
foreach ($t in $tests) {
    $c = curl.exe -sk -o NUL -w '%{http_code}' -m 10 $t.u
    Info ($t.n + " -> " + $c)
}

Step '[4/4] WS 握手探测(/ws 若存在)'
$wsCode = curl.exe -sk -o NUL -w '%{http_code}' -m 8 -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" https://zxjiu.com/ws
Info ("ws handshake -> " + $wsCode + " (101=升级成功, 404=无此路由, 502=后端不通)")
Write-Host 'PHASE4-DONE'
