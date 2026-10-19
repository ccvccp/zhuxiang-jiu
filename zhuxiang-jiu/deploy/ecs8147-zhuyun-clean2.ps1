﻿﻿﻿# 知识库第二批清理(麒麟批次动态圈选 + 学报乱码 + 垃圾碎片 -> 退役)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }
$T = 'C:\tmp8147\arch'
New-Item -ItemType Directory -Force $T | Out-Null

$py = @'
import asyncio, os, re, sys
os.environ['STORE_MODE'] = 'redis'; os.environ['LOCK_MODE'] = 'redis'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6379/0?protocol=2'
sys.path.insert(0, r'C:\backend')
from repositories.knowledge_repository import KnowledgeRepository
from services.knowledge_service import KnowledgeService

# ① 学报乱码(Latin-1 形态) ② 垃圾碎片 + 麒麟散条(273 孔子麟凤歌)
EXTRA_IDS = [117, 119, 122, 44, 215, 226, 227, 273]
# ② 麒麟整批(动态圈选, 按主题头匹配)
KIRIN_HEADS = {'维基-麒麟', '维基百科-麒麟'}

async def main():
    svc = KnowledgeService()
    repo = KnowledgeRepository()
    pub = await repo.list_entries(status='published', limit=2000)
    lines = ['published before: %d' % len(pub)]

    targets = set(EXTRA_IDS)
    for e in pub:
        if (e.get('source') or '') != 'crawl':
            continue
        head = re.sub(r'\(\d+/\d+\)', '', (e.get('question') or '').split(':')[0]).strip()
        if head in KIRIN_HEADS:
            targets.add(int(e.get('id')))
    targets = sorted(targets)
    lines.append('targets (%d): %s' % (len(targets), ','.join(map(str, targets))))

    ok = fail = 0
    for eid in targets:
        try:
            await svc.retire_entry(eid)
            ok += 1
        except Exception as ex:
            fail += 1
            lines.append('RETIRE #%d FAILED: %s' % (eid, str(ex)[:50]))
    lines.append('retired ok=%d fail=%d' % (ok, fail))

    bad = []
    for eid in targets:
        e = await repo.get_entry(eid)
        st = e.get('status') if e else 'GONE'
        if st != 'retired':
            bad.append('#%d=%s' % (eid, st))
    lines.append('verify: %s' % ('ALL-RETIRED' if not bad else 'BAD: ' + ','.join(bad)))
    pub2 = await repo.list_entries(status='published', limit=2000)
    lines.append('published after: %d' % len(pub2))
    report = '\n'.join(lines)
    open(r'C:\tmp8147\arch\zhuyun-clean2.txt', 'w', encoding='utf-8').write(report)
    print('CLEAN2-DONE')

asyncio.run(main())
'@
[IO.File]::WriteAllText('C:\backend\_clean_kb2.py', $py, (New-Object Text.UTF8Encoding $false))
Write-Host '== [1/2] 执行退役(redis 持久态) =='
Push-Location C:\backend
$out = & C:\Python312\python.exe 'C:\backend\_clean_kb2.py' 2>&1 | Out-String
Pop-Location
Info ($out.Trim())
if (-not $out.Contains('CLEAN2-DONE')) { Info '!! 清理脚本异常退出'; exit 1 }
Remove-Item 'C:\backend\_clean_kb2.py' -Force -ErrorAction SilentlyContinue

Write-Host '== [2/2] 回传报告 =='
$f = "$T\zhuyun-clean2.txt"
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($f))
Write-Host ("REPORT-LEN " + (Get-Item $f).Length)
Write-Host 'RB64-BEGIN'
Write-Host $b64
Write-Host 'RB64-END'
Write-Host 'ZHUYUN-CLEAN2-DONE'
