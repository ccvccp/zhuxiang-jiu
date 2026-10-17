# 探针2: chat 键名结构 + shadowLlm 定位
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }
$k = & C:\redis\redis-cli.exe KEYS '*chat*' 2>$null
Write-Host ('chat 键总数: ' + @($k).Count)
$k | Select-Object -First 15 | ForEach-Object { Info $_ }
# 会话消息结构: 找一个会话键看类型
$sesKey = @($k | Where-Object { $_ -match 'session' } | Select-Object -First 1)[0]
if ($sesKey) {
    Info ('--- 样例会话键: ' + $sesKey + '  TYPE: ' + (& C:\redis\redis-cli.exe TYPE $sesKey))
}
# 今天部署验证的消息id=120-123, 找承载键
foreach ($pat in @('*messages*', '*CS1792*', '*chat:msg*')) {
    $r = & C:\redis\redis-cli.exe KEYS $pat 2>$null
    Info ("pattern $pat -> " + @($r).Count + ' 个: ' + (($r | Select-Object -First 5) -join ', '))
}
Write-Host 'PROBE2-DONE'
