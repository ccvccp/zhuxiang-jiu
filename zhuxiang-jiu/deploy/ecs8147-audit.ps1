# 迁移完整性审计: ECS 侧全项体检
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Sec($m) { Write-Host ("### " + $m) }
function Info($m) { Write-Host ("  " + $m) }

Sec 'A. 服务与进程'
foreach ($s in @('nginx8147','backend8147')) {
    $sv = Get-Service $s -ErrorAction SilentlyContinue
    if ($sv) { Info ($s + ": " + $sv.Status + " (StartType=" + $sv.StartType + ")") } else { Info ($s + ": MISSING") }
}
$p = Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like 'C:\Python312*' } | Select-Object -First 1
if ($p) { Info ("backend pid=" + $p.Id + " mem=" + [math]::Round($p.WorkingSet64/1MB,0) + "MB uptime=" + ((Get-Date) - $p.StartTime).ToString('hh\:mm\:ss')) }
$ap = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\backend8147\Parameters' -Name AppParameters -ErrorAction SilentlyContinue).AppParameters
Info ("AppParameters: " + $ap)

Sec 'B. 关键配置生效'
foreach ($k in @('STORE_MODE','LOCK_MODE','SITE_BASE_URL','CORS_ORIGINS','SV73_MODE','PAY60_CHANNEL_MODE')) {
    $v = [IO.File]::ReadAllLines('C:\backend\.env', [Text.Encoding]::UTF8) | Where-Object { $_ -match "^$k=" } | Select-Object -First 1
    Info ($v)
}

Sec 'C. 后端错误日志(近500行 error/exception 计数)'
$errs = (Get-Content C:\backend\logs\service-err.log -Tail 500 -ErrorAction SilentlyContinue | Select-String -Pattern 'ERROR|Traceback|Exception' -CaseSensitive:$false | Measure-Object).Count
Info ("error/exception 行数: " + $errs)
Get-Content C:\backend\logs\service-err.log -Tail 500 -ErrorAction SilentlyContinue | Select-String -Pattern 'ERROR|Traceback' -CaseSensitive:$false | Select-Object -First 5 | ForEach-Object { Info ($_.Line.Substring(0, [Math]::Min(130, $_.Line.Length))) }

Sec 'D. 证书续期自动化'
Get-ChildItem C:\Certbot\renewal | ForEach-Object { Info ($_.Name) }
foreach ($rc in @('zyjiu.com','v.zyjiu.com')) {
    $hook = ([IO.File]::ReadAllLines("C:\Certbot\renewal\$rc.conf") | Where-Object { $_ -match '^deploy_hook' })
    Info ($rc + " deploy_hook: " + $hook)
}
$t = schtasks /query /tn zyjiu-certbot-renew 2>&1 | Select-String 'zyjiu-certbot'
Info ("schtask: " + ($t -join ''))
Get-ChildItem C:\certbot-hooks -File | ForEach-Object { Info ("hook file: " + $_.Name + " (" + $_.Length + "B)") }

Sec 'E. 资源与防火墙'
Get-PSDrive C | ForEach-Object { Info ("C: free " + [math]::Round($_.Free/1GB,1) + "GB / used " + [math]::Round($_.Used/1GB,1) + "GB") }
$fw = netsh advfirewall firewall show rule name=all | Select-String 'nginx8147'
Info ("firewall nginx rules: " + (@($fw).Count) + " 条")

Sec 'F. 本地 WS 握手(/api/xiaozhu/ws/)'
$ws = curl.exe -s -o NUL -w '%{http_code}' -m 5 -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' http://127.0.0.1:8000/api/xiaozhu/ws/voice
Info ("WS 握手: " + $ws + " (101=升级成功; 4xx=路由要求参数也算通)")

Sec 'G. 代码 zxjiu 残留复查(backend 非 test)'
$left = Get-ChildItem C:\backend -Recurse -Filter *.py -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\\(\.deps|__pycache__)\\' -and $_.Name -notmatch '^test_|^generate_|_deploy_fab|^prod_admin_e2e' } | Select-String -Pattern 'zxjiu\.com' -List
Info ("功能性残留: " + (@($left).Count) + " 处")
$left | Select-Object -First 5 | ForEach-Object { Info ("  " + $_.Path.Replace('C:\backend\','') + ":" + $_.LineNumber) }
Write-Host 'AUDIT-ECS-DONE'
