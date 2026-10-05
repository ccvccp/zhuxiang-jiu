# 明日凌晨自动队列(2026-10-06 00:05 计划任务触发):
#   ① publish_content(70) 入队 18:00 黄金档(日限 0 点重置)
#   ② 快手补排产 1 条(vertical 零 GPU) + 本地渲染 + 三审 approve
#   ③ 落盘新条目信息 -> tomorrow_queue_result.log(agent 次日消费生成 bot 配置)
$log = 'd:\网站架构设计\channels-bot\tomorrow_queue_result.log'
"== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 明日队列自动执行 ==" | Out-File $log -Encoding utf8

# ① 70 入队
$pipe = @'
import asyncio
async def m():
    from services.promo_service import PromoService
    r = await PromoService().publish_content(
        70, publish_at="2026-10-06T18:00:00+08:00")
    print("publish 70 ->", r.get("status"))
asyncio.run(m())
'@
"[$(Get-Date -Format HH:mm:ss)] ① 70 入队 ..." | Out-File $log -Append -Encoding utf8
($pipe | ssh -o ConnectTimeout=20 root@47.236.61.117 "docker exec -i zhuxiang-backend-1 python -") | Out-File $log -Append -Encoding utf8

# ② 快手排产 vertical 1 条
"[$(Get-Date -Format HH:mm:ss)] ② 快手排产 vertical ..." | Out-File $log -Append -Encoding utf8
Set-Location 'd:\网站架构设计\zhuxiang-jiu\backend'
$daily = python sv73_daily.py --count 1 --template vertical --platforms kuaishou 2>&1
$daily | Out-File $log -Append -Encoding utf8

# ③ 查新条目(cid/scriptId/短码) -> 本地渲染 + 三审
$q = @'
import asyncio
async def m():
    from services.promo_service import PromoService
    rows = await PromoService().list_contents(platform="kuaishou")
    for c in rows[:3]:
        if c.get("status") in ("draft", "pending_review", "approved"):
            sv = c.get("sv73") or {}
            print("NEW", c.get("contentId"), sv.get("storyboardId"),
                  c.get("status"))
            return
asyncio.run(m())
'@
$qout = $q | ssh -o ConnectTimeout=20 root@47.236.61.117 "docker exec -i zhuxiang-backend-1 python -"
$qout | Out-File $log -Append -Encoding utf8
if ($qout -match 'NEW (\d+) (sv73_[0-9a-f]{12})') {
    $cid = $Matches[1]; $sid = $Matches[2]
    "[$(Get-Date -Format HH:mm:ss)] ③ 渲染 $sid ..." | Out-File $log -Append -Encoding utf8
    $build = python build_sv73_dev.py $sid 2>&1
    $build | Select-Object -Last 6 | Out-File $log -Append -Encoding utf8
    $rv = @'
import asyncio, sys
async def m():
    from services.promo_service import PromoService
    r = await PromoService().review_content(int(sys.argv[1]), approved=True)
    print("review", sys.argv[1], "->", r.get("status"))
asyncio.run(m())
'@
    $rv = $rv -replace 'sys\.argv\[1\]', $cid
    ($rv | ssh -o ConnectTimeout=20 root@47.236.61.117 "docker exec -i zhuxiang-backend-1 python -") | Out-File $log -Append -Encoding utf8
    "RESULT cid=$cid sid=$sid" | Out-File $log -Append -Encoding utf8
} else {
    "WARN 新条目未识别(次日手动补)" | Out-File $log -Append -Encoding utf8
}
"== DONE $(Get-Date -Format HH:mm:ss) ==" | Out-File $log -Append -Encoding utf8
