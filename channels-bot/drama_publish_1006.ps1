# 10-06 短剧 70/77 抖音发布无人值守编排(计划任务 zxjiu-drama-pub-1006 @18:00 触发)
# 时序: ①轮询 70 转态 rpa_pending(服务器 PROMO_PUBLISH_AUTO 每 5min 周期; 本地 18:01 任务双保险)
#       ②douyin-bot 发布 70 → manage 核验 dump 留证
#       ③分段心跳等到 18:31 → 轮询 77 转态 → 发布 77 → manage
#       ④汇总 exit(0 全成/1 有败)
# 铁律: bot 失败/未确认(exit=6)不自动重跑(防双发, 事后 manage 核对人工处置)
# 回执: 半值守——manage 取作品 URL 后填 drama_receipt.py 传服务器登记
$ErrorActionPreference = 'Continue'
$node = 'd:\网站架构设计\nodejs\node-v20.18.2-win-x64\node.exe'
Set-Location 'd:\网站架构设计\channels-bot'
$log = 'd:\网站架构设计\channels-bot\drama_pub_1006.log'
function W([string]$m) {
  $line = '{0} {1}' -f (Get-Date -Format 'MM-dd HH:mm:ss'), $m
  Add-Content -Path $log -Value $line -Encoding UTF8
  Write-Host $line
}

$probe = @'
import asyncio
async def m():
    from services.promo_service import PromoService
    rows = await PromoService().list_contents()
    for c in rows:
        if c.get("contentId") in (70, 77):
            print(c.get("contentId"), c.get("status"), (c.get("receipt") or {}).get("mode", ""))
asyncio.run(m())
'@

function Wait-RpaPending([int]$cid, [int]$maxSec) {
  $deadline = (Get-Date).AddSeconds($maxSec)
  while ((Get-Date) -lt $deadline) {
    $out = $probe | ssh -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 root@47.236.61.117 "docker exec -i zhuxiang-backend-1 python -"
    $line = (@($out) | Where-Object { $_ -match ('^' + $cid + '\s') }) -join ''
    if ($line -match 'rpa_pending') { W ('[' + $cid + '] rpa_pending OK :: ' + $line); return $true }
    W ('[' + $cid + '] waiting rpa_pending :: ' + $line)
    Start-Sleep -Seconds 30
  }
  W ('[' + $cid + '] ABORT 转态等待超时(' + $maxSec + 's)')
  return $false
}

function Publish-One([int]$cid, [string]$cfg) {
  if (-not (Wait-RpaPending $cid 480)) { return 1 }
  W ('[' + $cid + '] douyin-bot 启动 (' + $cfg + ')')
  & $node douyin-bot.js $cfg *>&1 | Tee-Object -FilePath ('pub' + $cid + '_dy.log')
  $rc = $LASTEXITCODE
  $tag = if ($rc -eq 0) { 'OK' } elseif ($rc -eq 6) { 'PUBLISH_UNCONFIRMED(勿重发, 以 manage 核对为准)' } else { 'FAIL(勿重发, 以 manage 核对为准)' }
  W ('[' + $cid + '] bot exit=' + $rc + ' ' + $tag)
  if ($rc -eq 0 -or $rc -eq 6) {
    W ('[' + $cid + '] manage 核验启动')
    & $node douyin-bot.js config_manage_1006.json *>&1 | Tee-Object -FilePath ('manage_' + $cid + '.log')
    W ('[' + $cid + '] manage exit=' + $LASTEXITCODE)
    Copy-Item 'douyin_manage_items.json' ('manage_items_' + $cid + '.json') -Force -ErrorAction SilentlyContinue
    Copy-Item 'douyin_manage_1.png' ('manage_' + $cid + '.png') -Force -ErrorAction SilentlyContinue
    W ('[' + $cid + '] manage 留证 manage_items_' + $cid + '.json / manage_' + $cid + '.png')
  }
  return $rc
}

W '=== 10-06 短剧 70/77 抖音发布编排开始 ==='
$rc70 = Publish-One 70 'config_pub_70_dy.json'
$t77 = [datetime]::Today.AddHours(18).AddMinutes(31)
if ((Get-Date) -lt $t77) {
  W ('70 段结束(exit=' + $rc70 + '), 心跳等待 18:31 77 档...')
  while ((Get-Date) -lt $t77) { Start-Sleep -Seconds 60 }
}
$rc77 = Publish-One 77 'config_pub_77_dy.json'
W ('===== 汇总 70:exit=' + $rc70 + ' 77:exit=' + $rc77 + ' =====')
W '回执: manage 取作品 URL → 填 drama_receipt.py → ssh 传服务器登记'
exit ($(if ($rc70 -eq 0 -and $rc77 -eq 0) { 0 } else { 1 }))
