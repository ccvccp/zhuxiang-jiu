﻿﻿# 73号(sv) 黄金时段发布队列自动转态——每日 18:01(北京时间)
# queued(scheduledAt<=now) → rpa_pending; 输出留档 publish_process.log
# RPA 发布段不在本任务内(bot 半值守——到点由 agent/人工触发)
# 2026-10-11 迁国内: SSH 海外 docker 链已替换为国内生产 API(https://zyjiu.com)
$log = 'd:\网站架构设计\channels-bot\publish_process.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
try {
    $BASE = 'https://zyjiu.com'
    $login = Invoke-RestMethod -Uri "$BASE/api/auth/login" -Method Post -ContentType 'application/json' -Body (@{ phone='13800000002'; password='test123456' } | ConvertTo-Json) -TimeoutSec 20
    $h = @{ Authorization = "Bearer $($login.accessToken)"; 'X-Role' = 'admin' }
    $res = Invoke-RestMethod -Uri "$BASE/api/promo/publish/process" -Method Post -Headers $h -ContentType 'application/json' -Body '{}' -TimeoutSec 30
    $lines = @()
    foreach ($c in @($res.data)) {
        $mode = ''
        if ($c.receipt) { $mode = $c.receipt.mode }
        $lines += "$($c.contentId) $($c.status) $mode"
    }
    $lines += "DONE $($lines.Count)"
    $out = $lines -join "`n"
} catch {
    $out = "FAIL: $($_.Exception.Message)"
    # 失败告警钩子(2026-10-11): 桌面弹窗 + flag 文件 + 事件日志
    $stamp2 = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path (Join-Path (Split-Path $log -Parent) 'PUBLISH_FAIL_ALERT.flag') -Value "[$stamp2] process-task FAILED: $($_.Exception.Message)" -Encoding UTF8
    try { cmd /c "msg * /time:90 sv73 TRANSITION FAILED :: $($_.Exception.Message)" } catch {}
    try { cmd /c "eventcreate /t ERROR /id 731 /l APPLICATION /d ""sv73 transition FAILED: $($_.Exception.Message)"" " } catch {}
}
"$stamp`n$out" | Out-File -FilePath $log -Append -Encoding utf8
