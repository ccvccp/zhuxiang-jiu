# 竹韵 shadow 观察期每日巡检(国内 ECS 环回, 无敏感信息)
# 输出: 最近24h chat 消息统计——shadowLlm 留痕数/care 命中数/场景分布
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }
Write-Host '==> [1/3] 健康+env 确认'
$h = curl.exe -s -m 10 http://127.0.0.1:8000/api/decision/health
Info ("health: " + $h.Substring(0, [Math]::Min(50, $h.Length)))
$envReg = (Get-Item 'HKLM:\SYSTEM\CurrentControlSet\Services\backend8147\Parameters').GetValue('AppEnvironmentExtra')
Info ("nssm env: " + (@($envReg) -join ' | '))
$keySet = Select-String -Path C:\backend\.env -Pattern '^LLM_API_KEY=\S' -Quiet
Info ("LLM_API_KEY in .env: " + $keySet)
Write-Host '==> [2/3] Redis chat 会话/消息统计(最近24h)'
$msgKeys = & C:\redis\redis-cli.exe KEYS 'zhuxiang:chat:message:*' 2>$null
Info ("chat 键总数(含消息): " + @($msgKeys).Count)
# 抽样: 消息键里 shadowLlm 非空计数 + ragMode 分布(最近200条扫描)
$slm = 0; $care = 0; $sample = @()
foreach ($k in ($msgKeys | Select-Object -Last 200)) {
    $v = & C:\redis\redis-cli.exe GET $k 2>$null
    if (-not $v) { continue }
    if ($v -match '"shadowLlm":\s*\{') { $slm++ }
    if ($v -match '12356|zhuyun_care') { $care++ }
    if ($sample.Count -lt 3 -and $v -match '"shadowLlm":\s*\{') { $sample += $k }
}
Info ("扫描200条: shadowLlm留痕=$slm care命中=$care")
Write-Host '==> [3/3] shadowLlm 样本抽检(前3条留痕, 人格质量)'
foreach ($k in $sample) {
    $v = & C:\redis\redis-cli.exe GET $k 2>$null
    if ($v -match '"shadowLlm":\s*\{[^}]*"answer":\s*"((?:[^"\\]|\\.){0,160})') {
        $a = $Matches[1] -replace '\\n', ' ' -replace '\\"', '"'
        Info ($k + ' -> ' + $a)
    } else { Info ($k + ' (无 answer 字段)') }
}
Write-Host 'OBSERVE-DONE'
