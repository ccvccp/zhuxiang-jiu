# sv73 黄金档 RPA 发布编排(通用参数版)——douyin/wechat_channels 双平台
# 时序: 18:01 计划任务(zxjiu-sv73-publish-process)自动转态 queued→rpa_pending
#       → 本脚本 18:05+ 启动: ①核态(未转则补转) ②bot 发布 ③日志留档
# 用法: powershell -File sv73_publish_golden.ps1 -Cid 75                 # 抖音(默认)
#       powershell -File sv73_publish_golden.ps1 -Cid 88 -Platform wx   # 视频号
# 回执: 发布成功后次日 manage 核验拿作品 URL → drama_receipt.py 登记(半值守)
param([int]$Cid = 75, [string]$Platform = "dy")
$ErrorActionPreference = 'Continue'
$node = 'd:\网站架构设计\nodejs\node-v20.18.2-win-x64\node.exe'
Set-Location 'd:\网站架构设计\channels-bot'
$platLabel = @{ dy = '抖音'; wx = '视频号' }[$Platform]
$botJs = @{ dy = 'douyin-bot.js'; wx = 'bot.js' }[$Platform]
$cfg = "config_pub_${Cid}_${Platform}.json"
$log = "pub${Cid}_${Platform}.log"

# ---- 失败告警钩子(2026-10-11): 桌面弹窗 + flag 文件 + 事件日志 三通道 ----
# 教训: 10-09/10-10 75/76/87 转态 ABORT 无人发现, bot 从未执行"假发布"3 天
function Publish-Alert([int]$alertCid, [string]$alertPlat, [string]$reason) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$stamp] cid=$alertCid plat=$alertPlat FAILED: $reason"
    Add-Content -Path 'PUBLISH_FAIL_ALERT.flag' -Value $line -Encoding UTF8
    try { cmd /c "msg * /time:90 sv73 PUBLISH FAILED cid=$alertCid $alertPlat :: $reason :: see channels-bot\PUBLISH_FAIL_ALERT.flag" } catch {}
    try { cmd /c "eventcreate /t ERROR /id 731 /l APPLICATION /d ""sv73 publish FAILED cid=$alertCid $alertPlat: $reason"" " } catch {}
}
"===== sv73_publish_golden cid=$Cid plat=$platLabel $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====="

# ---- ① 转态核验/补转(queued→rpa_pending; 国内生产 https://zyjiu.com API, 2026-10-11 迁国内后替换 SSH 海外链) ----
$BASE = 'https://zyjiu.com'
$login = Invoke-RestMethod -Uri "$BASE/api/auth/login" -Method Post -ContentType 'application/json' -Body (@{ phone='13800000002'; password='test123456' } | ConvertTo-Json) -TimeoutSec 20
$h = @{ Authorization = "Bearer $($login.accessToken)"; 'X-Role' = 'admin' }
$state = ''
foreach ($try in 1..2) {
    $contents = Invoke-RestMethod -Uri "$BASE/api/promo/contents?limit=100" -Headers $h -TimeoutSec 20
    $row = @($contents.data) | Where-Object { $_.contentId -eq $Cid } | Select-Object -First 1
    $mode = ''
    if ($row -and $row.receipt) { $mode = $row.receipt.mode }
    $state = "STATE $Cid $mode"
    if ($state -match 'rpa_pending') { break }
    $null = Invoke-RestMethod -Uri "$BASE/api/promo/publish/process" -Method Post -Headers $h -ContentType 'application/json' -Body '{}' -TimeoutSec 30
    Start-Sleep -Seconds 3
}
"$(Get-Date -Format 'HH:mm:ss') [转态] $state"
if ($state -notmatch 'rpa_pending') {
    "$(Get-Date -Format 'HH:mm:ss') [ABORT] $Cid 未转态 rpa_pending——先查计划任务/队列"
    Publish-Alert $Cid $platLabel "转态核验失败($state)——bot 未执行, 内容未发布"
    exit 9
}

# ---- ② bot 全自动发布(dy: 60s 上传下限/PUBLISH_OK 真值核验; wx: AI 标注自动勾选) ----
"$(Get-Date -Format 'HH:mm:ss') [$Cid/$platLabel] $botJs 启动 cfg=$cfg"
& $node $botJs $cfg *>&1 | Tee-Object -FilePath $log
$rc = $LASTEXITCODE
"$(Get-Date -Format 'HH:mm:ss') [$Cid/$platLabel] exit=$rc $(if ($rc -eq 0) {'OK'} else {'FAIL(勿重发——先 manage 核对)'})"
if ($rc -ne 0) { Publish-Alert $Cid $platLabel "bot exit=$rc——发布未确认成功, 勿重发先 manage 核对" }

# ---- ③ 汇总(回执半值守提示) ----
"===== 汇总 ====="
"$Cid ${platLabel}: exit=$rc"
"下一步: 成功则次日 manage 核验拿作品 URL → drama_receipt.py(stdin)登记"
exit $(if ($rc -eq 0) {0} else {1})
