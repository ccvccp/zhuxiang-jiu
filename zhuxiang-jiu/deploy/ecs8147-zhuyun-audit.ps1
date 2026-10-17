# 知识库剩余条目质量普查(主题分桶/超长/空关键词 -> 只读报告)
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

async def main():
    repo = KnowledgeRepository()
    pub = await repo.list_entries(status='published', limit=2000)
    lines = ['published: %d' % len(pub)]

    # ---- 1. 按来源主题分桶 ----
    buckets = {}
    for e in pub:
        src = e.get('source') or 'none'
        if src != 'crawl':
            continue
        q = (e.get('question') or '')
        head = q.split(':')[0]
        head = re.sub(r'\(\d+/\d+\)', '', head).strip()
        buckets.setdefault(head, []).append(int(e.get('id')))
    lines.append('== crawl 主题分桶 (共 %d 桶) ==' % len(buckets))
    for k in sorted(buckets, key=lambda x: -len(buckets[x])):
        ids = sorted(buckets[k])
        lines.append('  [%d 条] %s  ids=%s' % (len(ids), k, ','.join(map(str, ids[:12])) + ('...' if len(ids) > 12 else '')))

    # ---- 2. 非 crawl 条目概览(manual 等) ----
    nc = [e for e in pub if (e.get('source') or '') != 'crawl']
    bycat = {}
    for e in nc:
        bycat.setdefault(e.get('category') or '?', []).append(int(e.get('id')))
    lines.append('== 非 crawl 条目 %d 条, 按分类 ==' % len(nc))
    for k in sorted(bycat):
        lines.append('  [%d] %s' % (len(bycat[k]), k))

    # ---- 3. 质量旗标 ----
    lines.append('== 旗标: answer>2000字 / keywords 空 / answer<10字 ==')
    for e in pub:
        a = e.get('answer') or ''
        kw = (e.get('keywords') or '').strip()
        flags = []
        if len(a) > 2000: flags.append('LONG=%d' % len(a))
        if not kw: flags.append('NO-KW')
        if len(a) < 10: flags.append('SHORT=%d' % len(a))
        if flags:
            lines.append('  #%d [%s/%s] %s  %s' % (e.get('id'), e.get('category'), e.get('source'), '|'.join(flags), (e.get('question') or '')[:24]))
    report = '\n'.join(lines)
    open(r'C:\tmp8147\arch\zhuyun-audit.txt', 'w', encoding='utf-8').write(report)
    print('AUDIT-DONE lines=%d' % len(lines))

asyncio.run(main())
'@
[IO.File]::WriteAllText('C:\backend\_audit_kb.py', $py, (New-Object Text.UTF8Encoding $false))
Write-Host '== [1/2] 执行普查(redis 持久态) =='
Push-Location C:\backend
$out = & C:\Python312\python.exe 'C:\backend\_audit_kb.py' 2>&1 | Out-String
Pop-Location
Info ($out.Trim())
if (-not $out.Contains('AUDIT-DONE')) { Info '!! 普查异常退出'; exit 1 }
Remove-Item 'C:\backend\_audit_kb.py' -Force -ErrorAction SilentlyContinue

Write-Host '== [2/2] 回传报告 =='
$f = "$T\zhuyun-audit.txt"
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($f))
Write-Host ("REPORT-LEN " + (Get-Item $f).Length)
Write-Host 'RB64-BEGIN'
Write-Host $b64
Write-Host 'RB64-END'
Write-Host 'ZHUYUN-AUDIT-DONE'
