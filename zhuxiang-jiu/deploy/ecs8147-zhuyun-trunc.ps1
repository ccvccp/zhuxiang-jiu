# 超长条目截断重建(#188/#135 -> 句界截断 ~320 字 -> 退役旧条+重建发布)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }
$T = 'C:\tmp8147\arch'
New-Item -ItemType Directory -Force $T | Out-Null

$py = @'
import asyncio, os, sys
os.environ['STORE_MODE'] = 'redis'; os.environ['LOCK_MODE'] = 'redis'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6379/0?protocol=2'
sys.path.insert(0, r'C:\backend')
from repositories.knowledge_repository import KnowledgeRepository
from services.knowledge_service import KnowledgeService

TARGET_IDS = [188, 135]
CAP = 320          # 目标长度上限
WINDOW = 360       # 句界搜索窗口
SENT = '。；！？\n'

def truncate(text):
    if len(text) <= CAP:
        return text
    win = text[:WINDOW]
    cut = max(win.rfind(c) for c in SENT)
    if cut < CAP // 2:               # 窗口内无合适句界 -> 硬截
        return text[:CAP]
    return text[:cut + 1]

async def main():
    svc = KnowledgeService()
    repo = KnowledgeRepository()
    lines = ['published before: %d' % len(await repo.list_entries(status='published', limit=2000))]

    for eid in TARGET_IDS:
        e = await repo.get_entry(eid)
        if not e or e.get('status') != 'published':
            lines.append('#%d SKIP (status=%s)' % (eid, e.get('status') if e else 'GONE'))
            continue
        old_len = len(e.get('answer') or '')
        new_a = truncate(e.get('answer') or '')
        lines.append('#%d [%s] answer %d -> %d  Q: %s' % (
            eid, e.get('category'), old_len, len(new_a), (e.get('question') or '')[:30]))
        await svc.retire_entry(eid)
        ne = await svc.create_entry(
            question=e['question'], answer=new_a,
            category=e.get('category') or 'faq',
            keywords=e.get('keywords') or '',
            source=e.get('source') or 'crawl', created_by=1)
        nid = ne['id']
        await svc.review_entry(nid, approve=True, reviewer_id=1, reason='超长截断重建')
        await svc.publish_entry(nid, publisher_id=1)
        chk = await repo.get_entry(nid)
        lines.append('  -> new #%d status=%s alen=%d' % (nid, chk['status'], len(chk['answer'])))

    pub2 = await repo.list_entries(status='published', limit=2000)
    lines.append('published after: %d' % len(pub2))
    report = '\n'.join(lines)
    open(r'C:\tmp8147\arch\zhuyun-trunc.txt', 'w', encoding='utf-8').write(report)
    print('TRUNC-DONE')

asyncio.run(main())
'@
[IO.File]::WriteAllText('C:\backend\_trunc_kb.py', $py, (New-Object Text.UTF8Encoding $false))
Write-Host '== [1/2] 执行截断重建(redis 持久态) =='
Push-Location C:\backend
$out = & C:\Python312\python.exe 'C:\backend\_trunc_kb.py' 2>&1 | Out-String
Pop-Location
Info ($out.Trim())
if (-not $out.Contains('TRUNC-DONE')) { Info '!! 截断脚本异常退出'; exit 1 }
Remove-Item 'C:\backend\_trunc_kb.py' -Force -ErrorAction SilentlyContinue

Write-Host '== [2/2] 回传报告 =='
$f = "$T\zhuyun-trunc.txt"
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($f))
Write-Host ("REPORT-LEN " + (Get-Item $f).Length)
Write-Host 'RB64-BEGIN'
Write-Host $b64
Write-Host 'RB64-END'
Write-Host 'ZHUYUN-TRUNC-DONE'
