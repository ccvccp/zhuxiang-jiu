﻿﻿﻿﻿﻿﻿﻿# 短剧首批(drama_monologue) 18:00 黄金档发布编排——68 抖音 / 69 视频号
# 时序: 18:01 计划任务(zxjiu-sv73-publish-process)自动转态 queued→rpa_pending
#       → 本脚本 18:02+ 启动: ①核态(未转则补转) ②douyin-bot→68 ③bot.js→69(串行)
# 回执: 发布成功后跑 manage 核验拿作品 URL → drama_receipt.py(stdin)登记(半值守)
# 用法: powershell -File drama_publish_1800.ps1
$ErrorActionPreference = 'Continue'
$node = 'd:\网站架构设计\nodejs\node-v20.18.2-win-x64\node.exe'
Set-Location 'd:\网站架构设计\channels-bot'

# ---- ① 转态核验/补转(queued→rpa_pending; 18:01 任务若已跑则直接通过) ----
$probe = @'
import asyncio
async def m():
    from services.promo_service import PromoService
    rows = await PromoService().list_contents()
    st = {c.get("contentId"): (c.get("receipt") or {}).get("mode")
          for c in rows if c.get("contentId") in (68, 69)}
    need = [k for k, v in st.items() if v != "rpa_pending"]
    if need:
        await PromoService().process_publish_queue()
        rows = await PromoService().list_contents()
        st = {c.get("contentId"): (c.get("receipt") or {}).get("mode")
              for c in rows if c.get("contentId") in (68, 69)}
    print("STATE", st)
asyncio.run(m())
'@
$state = $probe | ssh -o ConnectTimeout=20 root@47.236.61.117 "docker exec -i zhuxiang-backend-1 python -"
$stamp = Get-Date -Format 'HH:mm:ss'
"$stamp [转态] $state"
if ($state -notmatch 'rpa_pending.*rpa_pending') {
    "$stamp [ABORT] 68/69 未全部转态 rpa_pending——先查计划任务/队列"
    exit 9
}

# ---- ② 68 抖音(douyin-bot 全自动) ----
"$stamp [68/抖音] douyin-bot 启动"
& $node douyin-bot.js config_pub_68_dy.json *>&1 | Tee-Object -FilePath pub68_dy.log
$rc68 = $LASTEXITCODE
"$(Get-Date -Format 'HH:mm:ss') [68/抖音] exit=$rc68 $(if ($rc68 -eq 0) {'OK'} else {'FAIL(勿重发——先 manage 核对)'})"

# ---- ③ 69 视频号(bot.js 全自动, AI 标注自动勾选) ----
if ($rc68 -eq 0) {
    "$(Get-Date -Format 'HH:mm:ss') [69/视频号] bot.js 启动"
    & $node bot.js config_pub_69_wx.json *>&1 | Tee-Object -FilePath pub69_wx.log
    $rc69 = $LASTEXITCODE
    "$(Get-Date -Format 'HH:mm:ss') [69/视频号] exit=$rc69 $(if ($rc69 -eq 0) {'OK'} else {'FAIL(勿重发——先 manage 核对)'})"
} else {
    "$(Get-Date -Format 'HH:mm:ss') [69/视频号] 跳过(68 失败联动停)"
    $rc69 = -1
}

# ---- ④ 汇总(回执半值守: 成功→manage 拿 URL→drama_receipt.py 登记) ----
"===== 汇总 $(Get-Date -Format 'HH:mm:ss') ====="
"68 抖音:  exit=$rc68   69 视频号: exit=$rc69"
"下一步: ① toutiao/bilibili 不涉及 ② 成功项跑 manage 核验拿作品 URL"
"        ③ Get-Content drama_receipt.py -Raw | ssh root@47.236.61.117 'docker exec -i zhuxiang-backend-1 python -'"
exit ($(if ($rc68 -eq 0 -and $rc69 -eq 0) {0} else {1}))
