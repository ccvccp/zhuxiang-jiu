# 复核v3: python 全部输出(含stderr)落文件
$py = @'
import json, redis, traceback
out = []
try:
    d = json.load(open(r'C:\tmp8147\api3.json', encoding='utf-8'))
    for p in (d.get('products') or []):
        out.append('%s -> %s' % (p.get('product_id'), (p.get('image') or '')[:70]))
    r = redis.Redis.from_url('redis://127.0.0.1:6379/0?protocol=2', decode_responses=True)
    ks = [k for k in r.scan_iter(match='*product*') if r.type(k) == 'hash']
    out.append('redis product hash: %d' % len(ks))
    out.append('sample: %s' % sorted(ks)[:4])
except Exception:
    out.append(traceback.format_exc())
open(r'C:\tmp8147\recheck.txt', 'w', encoding='utf-8').write('\n'.join(out))
'@
[IO.File]::WriteAllText('C:\tmp8147\recheck.py', $py, [Text.UTF8Encoding]::new($false))
& C:\Python312\python.exe C:\tmp8147\recheck.py 2> C:\tmp8147\recheck-err.txt
Write-Host ('python exit: ' + $LASTEXITCODE)
if (Test-Path C:\tmp8147\recheck.txt) { Get-Content C:\tmp8147\recheck.txt -Encoding UTF8 | ForEach-Object { Write-Host $_ } } else { Write-Host 'recheck.txt 仍未生成' }
if (Test-Path C:\tmp8147\recheck-err.txt) { Get-Content C:\tmp8147\recheck-err.txt | Select-Object -First 8 | ForEach-Object { Write-Host ('ERR: ' + $_) } }
Write-Host 'RECHECK3-DONE'
