# 双平台作品数据监控 (抖音 manage + 视频号 stats)
# 用法: powershell -File monitor_stats.ps1   (手动/计划任务)
# 产出: stats_history.log 追加式台账(时间戳+各作品播放/点赞)
$ErrorActionPreference = 'Continue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$node = 'd:\网站架构设计\nodejs\node-v20.18.2-win-x64\node.exe'
$log  = Join-Path $dir 'stats_history.log'

function Append([string]$s) { Add-Content -Path $log -Value $s -Encoding UTF8 }

Append "===== $(Get-Date -Format 'yyyy-MM-dd HH:mm') ====="

# ---- 抖音 ----
& $node (Join-Path $dir 'douyin-bot.js') (Join-Path $dir 'config_dy_manage.json') 2>$null | Out-Null
$dyJson = Join-Path $dir 'douyin_manage_items.json'
if (Test-Path $dyJson) {
  $seen = @{}
  Append "[抖音]"
  (Get-Content $dyJson -Raw -Encoding UTF8 | ConvertFrom-Json) | ForEach-Object {
    if ($_ -match '^(.{20}).*?\| (\d{4}年\d{2}月\d{2}日 [\d:]+) \| (\S+) \| 播放 \| (\d+) \| 点赞 \| (\d+) \| 评论 \| (\d+)') {
      $key = ($_.Substring(0, [Math]::Min(30, $_.Length)))
      if (-not $seen.ContainsKey($key)) {
        $seen[$key] = $true
        Append ("  {0} | {1} | {2} | 播放{3} 赞{4} 评{5}" -f $matches[1], $matches[2], $matches[3], $matches[4], $matches[5], $matches[6])
      }
    }
  }
}

# ---- 视频号 ----
& $node (Join-Path $dir 'bot.js') (Join-Path $dir 'config_wx_stats.json') 2>$null | Out-Null
$wxTxt = Join-Path $dir 'channels_stats_raw.txt'
if (Test-Path $wxTxt) {
  Append "[视频号]"
  $lines = (Get-Content $wxTxt -Encoding UTF8) | Where-Object { $_.Trim() -ne '' } | ForEach-Object { $_.Trim() }
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^(\d{4}年\d{2}月\d{2}日 [\d:]+)$') {
      $date = $matches[1]
      $title = if ($i -gt 0) { $lines[$i-1].Substring(0, [Math]::Min(24, $lines[$i-1].Length)) } else { '?' }
      $n = @()
      for ($k = 1; $k -le 5 -and ($i + $k) -lt $lines.Count; $k++) {
        if ($lines[$i+$k] -match '^\d+$') { $n += $lines[$i+$k] } else { break }
      }
      if ($n.Count -ge 5) {
        Append ("  {0} | {1} | 播放{2} 赞{3} 评{4} 享{5} 藏{6}" -f $title, $date, $n[0], $n[1], $n[2], $n[3], $n[4])
        $i += 5
      }
    }
  }
}
Append ""
Write-Host "done -> $log"
