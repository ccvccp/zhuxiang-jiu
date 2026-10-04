# 73号(sv) 黄金时段发布队列自动转态——每日 18:01(北京时间)
# queued(scheduledAt<=now) → rpa_pending; 输出留档 publish_process.log
# RPA 发布段不在本任务内(bot 半值守——到点由 agent/人工触发)
$log = 'd:\网站架构设计\channels-bot\publish_process.log'
$pipe = @'
import asyncio
async def m():
    from services.promo_service import PromoService
    rows = await PromoService().process_publish_queue()
    for c in rows:
        print(c.get("contentId"), c.get("status"),
              (c.get("receipt") or {}).get("mode", ""))
    print("DONE", len(rows))
asyncio.run(m())
'@
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pipe))
$out = $b64 | ssh -o ConnectTimeout=20 root@47.236.61.117 `
  "docker exec -i zhuxiang-backend-1 python -c `"import base64,sys;exec(base64.b64decode(sys.stdin.read()))`""
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
"$stamp`n$out" | Out-File -FilePath $log -Append -Encoding utf8
