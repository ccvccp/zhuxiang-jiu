# 清理知识库乱码条目(扫描乱码分布 -> 退役 #50 -> 核验)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "    $m" }
$T = 'C:\tmp8147\arch'
New-Item -ItemType Directory -Force $T | Out-Null

$py = @'
import asyncio, base64, os, sys
os.environ['STORE_MODE'] = 'redis'; os.environ['LOCK_MODE'] = 'redis'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6379/0?protocol=2'
sys.path.insert(0, r'C:\backend')
from repositories.knowledge_repository import KnowledgeRepository
from services.knowledge_service import KnowledgeService

CLEAN_IDS = [50]  # 本次退役对象

def mojibake_stats(text):
    """统计疑似编码损坏字符(C1控制/替换符/西里尔/拉丁扩展/希腊)"""
    if not text:
        return 0, 0.0
    bad = 0
    for ch in text:
        o = ord(ch)
        if (0x80 <= o <= 0x9f or o == 0xfffd
                or 0x400 <= o <= 0x4ff    # 西里尔(GBK 双解码典型产物)
                or 0x100 <= o <= 0x2ff     # 拉丁扩展/IPA
                or 0x370 <= o <= 0x3ff):   # 希腊
            bad += 1
    return bad, bad / len(text)

async def main():
    lines = []
    svc = KnowledgeService()
    repo = KnowledgeRepository()
    pub = await repo.list_entries(status='published', limit=2000)
    lines.append('published before: %d' % len(pub))

    # ---- 1. 乱码扫描(只报告, 不动) ----
    lines.append('== mojibake scan (bad>=20 or ratio>=0.05) ==')
    flagged = 0
    for e in pub:
        text = (e.get('question') or '') + (e.get('answer') or '')
        bad, ratio = mojibake_stats(text)
        if bad >= 20 or ratio >= 0.05:
            flagged += 1
            lines.append('  #%d [%s/%s] bad=%d ratio=%.3f len=%d %s' % (
                e.get('id'), e.get('category'), e.get('source'),
                bad, ratio, len(text), (e.get('question') or '')[:30]))
    lines.append('  flagged total: %d' % flagged)

    # ---- 2. 退役 #50 ----
    for eid in CLEAN_IDS:
        try:
            r = await svc.retire_entry(eid)
            lines.append('RETIRE #%d -> %s' % (eid, r.get('status')))
        except Exception as ex:
            lines.append('RETIRE #%d FAILED: %s' % (eid, ex))

    # ---- 3. 核验 ----
    e = await repo.get_entry(50)
    lines.append('verify #50 status: %s' % (e.get('status') if e else 'GONE'))
    pub2 = await repo.list_entries(status='published', limit=2000)
    lines.append('published after: %d' % len(pub2))

    report = '\n'.join(lines)
    open(r'C:\tmp8147\arch\zhuyun-clean-report.txt', 'w', encoding='utf-8').write(report)
    print('CLEAN-DONE')

asyncio.run(main())
'@
[IO.File]::WriteAllText('C:\backend\_clean_kb.py', $py, (New-Object Text.UTF8Encoding $false))
Write-Host '== [1/2] 执行清理(redis 持久态) =='
Push-Location C:\backend
$out = & C:\Python312\python.exe 'C:\backend\_clean_kb.py' 2>&1 | Out-String
Pop-Location
Info ($out.Trim())
if (-not $out.Contains('CLEAN-DONE')) { Info '!! 清理脚本异常退出'; exit 1 }
Remove-Item 'C:\backend\_clean_kb.py' -Force -ErrorAction SilentlyContinue

Write-Host '== [2/2] 回传报告 =='
$f = "$T\zhuyun-clean-report.txt"
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($f))
Write-Host ("REPORT-LEN " + (Get-Item $f).Length)
Write-Host 'RB64-BEGIN'
Write-Host $b64
Write-Host 'RB64-END'
Write-Host 'ZHUYUN-CLEAN-DONE'
