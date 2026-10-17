﻿# 10-07 18:05 发布数据自动对比报告(挂 zxjiu-compare-1007 一次性计划任务)
# 链路: 先跑 monitor_stats.ps1 拉双平台最新数据(Chrome 前台), 再解析生成 compare_1007.txt
# 对比维度: 68 黄金档 vs 70/77 深夜档(档期衰减) + 69 vs 口播300天花板(短剧起量判定)
$ErrorActionPreference = 'Continue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $dir 'compare_1007.txt'
function W([string]$s) { Add-Content -Path $log -Value $s -Encoding UTF8 }

("===== 10-07 发布数据对比  生成于 $(Get-Date -Format 'yyyy-MM-dd HH:mm') =====") | Set-Content -Path $log -Encoding UTF8

# ---- ① 拉最新双平台数据(monitor 内部含抖音 manage + 视频号 stats) ----
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'monitor_stats.ps1')

# ---- ② 抖音: 解析 manage 卡片(播放/点赞/完播率/2秒跳出率) ----
W ''
W '[抖音·短剧三条] (发布档 → 数据)'
$dyJson = Join-Path $dir 'douyin_manage_items.json'
if (Test-Path $dyJson) {
  $seen = @{}
  $cards = Get-Content $dyJson -Raw -Encoding UTF8 | ConvertFrom-Json
  foreach ($card in @($cards)) {
    if ($card -match '^(\S{1,30})[^|]*\|.*?\| (\d{4}年\d{2}月\d{2}日 [\d:]+) \| (\S+) \| 播放 \| (\d+) \| 点赞 \| (\d+).*?完播率 \| ([\d.]+%) \| 2秒跳出率 \| ([\d.]+%)') {
      $key = $matches[1] + $matches[2]
      if (-not $seen.ContainsKey($key)) {
        $seen[$key] = $true
        $slot = if ($matches[2] -match '10月05日') { '黄金档(68基线)' } else { '深夜补发档' }
        W ('  {0} | {1} | {2} | 播放{3} 赞{4} | 完播{5} 跳出{6}' -f $matches[1], $matches[2], $slot, $matches[4], $matches[5], $matches[6], $matches[7])
      }
    }
  }
} else { W '  (douyin_manage_items.json 缺失)' }

# ---- ③ 视频号: 69 短剧 vs 口播基线 ----
W ''
W '[视频号·69短剧 vs 口播基线]'
$wxTxt = Join-Path $dir 'channels_stats_raw.txt'
if (Test-Path $wxTxt) {
  $lines = (Get-Content $wxTxt -Encoding UTF8) | Where-Object { $_.Trim() -ne '' } | ForEach-Object { $_.Trim() }
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^(\d{4}年\d{2}月\d{2}日 [\d:]+)$') {
      $title = if ($i -gt 0) { $lines[$i-1] } else { '?' }
      $n = @()
      for ($k = 1; $k -le 5 -and ($i + $k) -lt $lines.Count; $k++) {
        if ($lines[$i+$k] -match '^\d+$') { $n += $lines[$i+$k] } else { break }
      }
      if ($n.Count -ge 5) {
        $tag = if ($title -match '非遗.*打卡新宠|三年来谁都笑') { '  <- 69短剧' } else { '' }
        W ('  {0} | {1} | 播放{2}{3}' -f $title.Substring(0, [Math]::Min(22, $title.Length)), $matches[1], $n[0], $tag)
        $i += 5
      }
    }
  }
} else { W '  (channels_stats_raw.txt 缺失)' }

# ---- ④ 自动结论 ----
W ''
W '[自动判定]'
W '  · 70/77 深夜档 vs 68 黄金档: 看上方播放量级差(68 基线为 30h=9)'
W '  · 69 是否突破口播300天花板: 看上方 69 播放是否 >=300'
W '  · 完播率参照: 68 基线 13.33%/跳出 60%, 70/77 对照'
W ''
W '说明: 本报告由 zxjiu-compare-1007 于 18:05 自动生成; 台账明细见 stats_history.log'
