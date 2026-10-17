# 国内 Redis 构成摸底: dbsize + 二级前缀分组(对齐海外对比)
$ProgressPreference = 'SilentlyContinue'
function Info($m) { Write-Host "    $m" }

$cli = @('C:\Program Files\Redis\redis-cli.exe', 'C:\redis\redis-cli.exe', 'C:\backend\.deps\redis-cli.exe') |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $cli) {
    $cli = (Get-Command redis-cli.exe -ErrorAction SilentlyContinue).Source
}
if (-not $cli) { $cli = (Get-ChildItem C:\ -Filter redis-cli.exe -Recurse -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1).FullName }
Info ("redis-cli: $cli")
if (-not $cli) { Write-Host 'NO_REDIS_CLI'; exit 0 }

Info ("dbsize: " + (& $cli -n 0 dbsize))
$keys = & $cli -n 0 --scan --count 3000 2>$null
Info ("scan 样本键数: " + @($keys).Count)
$keys | ForEach-Object { ($_ -split ':')[0..1] -join ':' } |
    Group-Object | Sort-Object Count -Descending | Select-Object -First 25 |
    ForEach-Object { Info ("{0,7} {1}" -f $_.Count, $_.Name) }
Write-Host 'CN-REDIS-AUDIT-DONE'
